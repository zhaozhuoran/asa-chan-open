import os
import json
import schedule
import threading
import time
import requests
from datetime import datetime, timedelta, tzinfo
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import pytz
from utils import StatsManager, WeatherCache, translator, ai_client
import re
from db import get_db_conn, db_lock, init_db
from migrate import migrate
import zipfile
import shutil
import sqlite3
from functools import lru_cache

# Initialize environment
load_dotenv()
QWEATHER_KEY = os.getenv("QWEATHER_KEY", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")

# Ensure data directory exists
os.makedirs("data", exist_ok=True)
os.makedirs("backups", exist_ok=True)

# Initialize database and migrate if needed
init_db()
migrate()

app = App(token=SLACK_BOT_TOKEN)

# Thread safety locks
schedule_lock = threading.Lock()

# Global managers
stats_manager = StatsManager()
weather_cache = WeatherCache()


@lru_cache(maxsize=1024)
def get_user_display_name(user_id):
    try:
        response = app.client.users_info(user=user_id)
        if response["ok"]:
            user = response["user"]
            profile = user.get("profile", {})
            # Prefer display_name, then real_name, then fallback
            name = profile.get("display_name") or profile.get("real_name") or user.get("name")
            return name if name else "user"
        return "user"
    except Exception as e:
        print(f"Error fetching user info: {e}")
        return "user"


# User settings management
def update_user_setting(
    user_id,
    city=None,
    time_str=None,
    timezone=None,
    subscribed=None,
    initialized=None,
    lang=None,
    weather_mode=None,
):
    with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()

            # Check if user exists
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

            if not row:
                cursor.execute(
                    """
                    INSERT INTO users (user_id, city, time, timezone, subscribed, initialized, lang, weather_mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        user_id,
                        city,
                        time_str,
                        timezone,
                        1 if subscribed else 0,
                        1 if initialized else 0,
                        lang or "en",
                        weather_mode or "raw",
                    ),
                )
            else:
                updates = []
                params = []
                if city is not None:
                    updates.append("city = ?")
                    params.append(city)
                if time_str is not None:
                    updates.append("time = ?")
                    params.append(time_str)
                if timezone is not None:
                    updates.append("timezone = ?")
                    params.append(timezone)
                if subscribed is not None:
                    updates.append("subscribed = ?")
                    params.append(1 if subscribed else 0)
                if initialized is not None:
                    updates.append("initialized = ?")
                    params.append(1 if initialized else 0)
                if lang is not None:
                    updates.append("lang = ?")
                    params.append(lang)
                if weather_mode is not None:
                    updates.append("weather_mode = ?")
                    params.append(weather_mode)

                if updates:
                    params.append(user_id)
                    cursor.execute(
                        f'UPDATE users SET {", ".join(updates)} WHERE user_id = ?',
                        params,
                    )

            conn.commit()

            # Fetch updated setting
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user_setting = dict(cursor.fetchone())

    # Update schedule
    if (
        user_setting["subscribed"]
        and user_setting["city"]
        and user_setting["time"]
        and user_setting["timezone"]
    ):
        update_schedule(
            user_id,
            user_setting["city"],
            user_setting["time"],
            user_setting["timezone"],
        )
    else:
        with schedule_lock:
            schedule.clear(user_id)

    return user_setting


def get_user_setting(user_id):
    with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()

            if row:
                return dict(row)
            return {
                "city": None,
                "time": None,
                "timezone": None,
                "subscribed": False,
                "initialized": False,
                "lang": "en",
                "weather_mode": "raw",
            }


def get_active_subscribers_count():
    with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users WHERE subscribed = 1")
            count = cursor.fetchone()[0]
            return count


# Weather API Functions
def get_location_id(city_name: str) -> str:
    """Get city location ID using Qweather GeoAPI"""
    cached_id = weather_cache.get(city_name, "location_id")
    if cached_id:
        return cached_id

    if not stats_manager.can_make_api_call():
        raise ValueError("API Quota exceeded")

    url = "https://geoapi.qweather.com/v2/city/lookup"
    try:
        stats_manager.increment_api_call()
        response = requests.get(
            url,
            params={"location": city_name, "key": QWEATHER_KEY, "lang": "en"},
            timeout=10,
        )
        if response.status_code != 200:
            print(f"GeoAPI HTTP Error: {response.status_code}")
            raise ValueError(f"City not found: {city_name}")

        data = response.json()
        locations = data.get("location")
        if data.get("code") != "200" or not locations or len(locations) == 0:
            print(f"GeoAPI Return Error: code={data.get('code')}")
            raise ValueError(f"City not found: {city_name}")

        loc_id = locations[0].get("id")
        weather_cache.set(
            city_name, "location_id", loc_id, 86400
        )  # Cache location ID for 24h
        return loc_id
    except ValueError:
        raise
    except Exception as e:
        print(f"GeoAPI Error: {str(e)}")
        raise ValueError(f"City not found: {city_name}")


def fetch_weather_daily(city_name: str):
    cached_data = weather_cache.get(city_name, "daily")
    if cached_data:
        return cached_data

    if not stats_manager.can_make_api_call():
        print("API Quota exceeded")
        return None

    try:
        location_id = get_location_id(city_name)
        url = "https://devapi.qweather.com/v7/weather/3d"
        stats_manager.increment_api_call()
        response = requests.get(
            url,
            params={"location": location_id, "key": QWEATHER_KEY, "lang": "en"},
            timeout=10,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if data.get("code") != "200":
            return None

        weather_cache.set(city_name, "daily", data, 4 * 3600)  # 4 hours cache
        return data
    except Exception as e:
        print(f"Weather API Daily Data Error: {str(e)}")
        return None


class CompatibleFixedOffset(pytz.BaseTzInfo):
    """Custom timezone class compatible with schedule library's type check"""

    def __init__(self, offset_mins):
        self._offset = timedelta(minutes=offset_mins)
        sign = "+" if offset_mins >= 0 else "-"
        self.zone = f"UTC{sign}{abs(offset_mins)//60:02d}:{abs(offset_mins)%60:02d}"

    def utcoffset(self, dt):
        return self._offset

    def tzname(self, dt):
        return self.zone

    def dst(self, dt):
        return timedelta(0)

    def localize(self, dt, is_dst=False):
        if dt.tzinfo is not None:
            raise ValueError("Not naive")
        return dt.replace(tzinfo=self)

    def normalize(self, dt):
        if dt.tzinfo is None:
            raise ValueError("Naive")
        return dt.replace(tzinfo=self)

    def __repr__(self):
        return f"<{self.zone}>"


def get_tz_from_str(timezone_str):
    try:
        offset_pattern = r"^UTC([+-])(\d{1,2})(:(\d{2}))?$"
        match = re.match(offset_pattern, timezone_str)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            hours = int(match.group(2))
            minutes = int(match.group(4)) if match.group(4) else 0
            total_minutes = sign * (hours * 60 + minutes)
            return CompatibleFixedOffset(total_minutes)
        else:
            return pytz.timezone(timezone_str)
    except Exception:
        return pytz.UTC


def generate_report_daily(user_id: str, city_name: str, timezone_str="Asia/Shanghai"):
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    mode = setting.get("weather_mode", "raw")

    data = fetch_weather_daily(city_name)
    if not data or not data.get("daily") or len(data["daily"]) == 0:
        return translator.t("fetch_failed", lang)

    ai_warning = ""
    if mode != "raw":
        can_call, reason = stats_manager.can_make_ai_call(user_id)
        if can_call:
            display_name = get_user_display_name(user_id)
            ai_report = ai_client.generate_weather_report(mode, data["daily"][0], display_name, lang)
            if ai_report:
                stats_manager.increment_ai_call(user_id)
                return ai_report
            # Fallback to raw if AI fails
        elif reason == "user_limit":
            ai_warning = f"\n\n> {translator.t('ai_limit_reached', lang)}"
        elif reason == "global_limit":
            ai_warning = f"\n\n> {translator.t('ai_global_limit_reached', lang)}"

    tz = get_tz_from_str(timezone_str)
    now = datetime.now(tz)
    weather = data["daily"][0]

    text_day = weather.get("textDay", "Unknown")
    temp_min = weather.get("tempMin", "N/A")
    temp_max = weather.get("tempMax", "N/A")
    uv_index = weather.get("uvIndex", "0")
    wind_scale_day = weather.get("windScaleDay", "N/A")
    wind_dir_day = weather.get("windDirDay", "N/A")
    humidity = weather.get("humidity", "N/A")

    try:
        uv_warning = (
            translator.t("uv_warning", lang)
            if int(uv_index) >= 5
            else ""
        )
    except:
        uv_warning = ""

    header = translator.t("daily_report_header", lang, date=now.strftime('%Y.%m.%d'), city=city_name)
    condition = translator.t("condition", lang, text=text_day)
    temp = translator.t("temp_range", lang, min=temp_min, max=temp_max)
    uv = translator.t("uv_index", lang, uv=uv_index)
    wind = translator.t("wind_daily", lang, scale=wind_scale_day, dir=wind_dir_day)
    hum = translator.t("humidity", lang, humidity=humidity)

    return f"{header}\n{condition}\n{temp}\n{uv}\n{wind}\n{hum}{uv_warning}{ai_warning}"


def fetch_weather_now(city_name: str):
    cached_data = weather_cache.get(city_name, "now")
    if cached_data:
        return cached_data

    if not stats_manager.can_make_api_call():
        print("API Quota exceeded")
        return None

    try:
        location_id = get_location_id(city_name)
        url = "https://devapi.qweather.com/v7/weather/now"
        stats_manager.increment_api_call()
        response = requests.get(
            url,
            params={"location": location_id, "key": QWEATHER_KEY, "lang": "en"},
            timeout=10,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if data.get("code") != "200":
            return None

        weather_cache.set(city_name, "now", data, 1800)  # 30 mins cache
        return data
    except Exception as e:
        print(f"Weather API Real-time Data Error: {str(e)}")
        return None


def generate_report_now(user_id: str, city_name: str, timezone_str="Asia/Shanghai"):
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    mode = setting.get("weather_mode", "raw")

    data = fetch_weather_now(city_name)
    if not data or not data.get("now"):
        return translator.t("fetch_failed", lang)

    ai_warning = ""
    if mode != "raw":
        can_call, reason = stats_manager.can_make_ai_call(user_id)
        if can_call:
            display_name = get_user_display_name(user_id)
            ai_report = ai_client.generate_weather_report(mode, data["now"], display_name, lang)
            if ai_report:
                stats_manager.increment_ai_call(user_id)
                return ai_report
        elif reason == "user_limit":
            ai_warning = f"\n\n> {translator.t('ai_limit_reached', lang)}"
        elif reason == "global_limit":
            ai_warning = f"\n\n> {translator.t('ai_global_limit_reached', lang)}"

    weather = data.get("now")
    tz = get_tz_from_str(timezone_str)
    now = datetime.now(tz)

    text = weather.get("text", "Unknown")
    temp = weather.get("temp", "N/A")
    feels_like = weather.get("feelsLike", "N/A")
    wind_scale = weather.get("windScale", "N/A")
    wind_dir = weather.get("windDir", "N/A")
    humidity = weather.get("humidity", "N/A")
    pressure = weather.get("pressure", "N/A")
    precip = weather.get("precip", "N/A")

    header = translator.t("now_report_header", lang, date=now.strftime('%Y.%m.%d %H:%M'), city=city_name)
    condition = translator.t("condition", lang, text=text)
    temp_str = translator.t("temp_now", lang, temp=temp)
    feels = translator.t("feels_like", lang, temp=feels_like)
    wind = translator.t("wind_now", lang, scale=wind_scale, dir=wind_dir)
    hum = translator.t("humidity", lang, humidity=humidity)
    pres = translator.t("pressure", lang, pressure=pressure)
    prec = translator.t("precipitation", lang, precip=precip)

    return f"{header}\n{condition}\n{temp_str}\n{feels}\n{wind}\n{hum}\n{pres}\n{prec}{ai_warning}"


def send_welcome_message(say, user_id, lang="en"):
    welcome_text = translator.t("welcome", lang)
    say(welcome_text)
    update_user_setting(user_id, initialized=True)


def check_initialization(ack, say, command):
    ack()
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    if not setting.get("initialized"):
        send_welcome_message(say, user_id, setting.get("lang", "en"))
        return False
    return True


@app.event("message")
def handle_message_events(event, say):
    user_id = event.get("user")
    if not user_id:
        return

    # Only respond to DMs if not initialized
    if event.get("channel_type") == "im":
        setting = get_user_setting(user_id)
        if not setting.get("initialized"):
            lang = setting.get("lang", "en")
            send_welcome_message(say, user_id, lang)
            say(translator.t("first_time_notice", lang))


# Slack Command Handlers
@app.command("/asachan-ping")
def handle_ping(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    say(translator.t("ping", lang))


@app.command("/asachan-up")
def handle_up(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    say(translator.t("up", lang))


@app.command("/asachan-start")
def handle_start(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")

    # Validation
    missing = []
    if not setting.get("city"):
        missing.append(translator.t("missing_city", lang))
    if not setting.get("time"):
        missing.append(translator.t("missing_time", lang))
    if not setting.get("timezone"):
        missing.append(translator.t("missing_timezone", lang))

    if missing:
        say(
            translator.t("sub_failed_settings", lang)
            + "\n".join(f"- {m}" for m in missing)
        )
        return

    # Check 1k subscriber limit
    if get_active_subscribers_count() >= 1000 and not setting.get("subscribed"):
        say(translator.t("max_subscribers", lang))
        return

    setting = update_user_setting(user_id, subscribed=True)
    say(
        translator.t(
            "sub_success",
            lang,
            time=setting["time"],
            timezone=setting["timezone"],
            city=setting["city"],
        )
    )


@app.command("/asachan-unsub")
def handle_unsub(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    update_user_setting(user_id, subscribed=False)
    say(translator.t("unsub_success", setting.get("lang", "en")))


@app.command("/asachan-now")
def handle_now(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")

    if not setting.get("city"):
        say(translator.t("city_not_set", lang))
        return

    # Quota check for now requests
    if not setting.get("subscribed"):
        if not stats_manager.can_make_now_request(get_active_subscribers_count()):
            say(translator.t("too_many_requests", lang))
            return
        stats_manager.increment_now_request()

    report = generate_report_now(
        user_id, setting["city"], setting.get("timezone", "Asia/Shanghai")
    )
    say(report)


@app.command("/asachan-status")
def handle_status(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")

    not_set_str = translator.t("not_set", lang)
    status_msg = translator.t("status_city", lang, city=setting.get('city') or not_set_str) + "\n"
    status_msg += translator.t("status_time", lang, time=setting.get('time') or not_set_str) + "\n"
    status_msg += translator.t("status_timezone", lang, timezone=setting.get('timezone') or not_set_str) + "\n"
    status_msg += translator.t("status_subscribed", lang, subscribed=translator.t("yes" if setting.get("subscribed") else "no", lang)) + "\n"
    status_msg += translator.t("status_lang", lang, lang=setting.get("lang", "en")) + "\n"
    status_msg += translator.t("status_mode", lang, mode=setting.get("weather_mode", "raw"))

    say(f"{translator.t('current_settings_header', lang)}\n{status_msg}")


@app.command("/asachan-setcity")
def handle_setcity(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    text = command.get("text", "").strip()
    if not text:
        say(translator.t("provide_city", lang))
        return

    city_name = text
    try:
        get_location_id(city_name)
        update_user_setting(user_id, city=city_name)
        say(translator.t("city_set", lang, city=city_name))
    except ValueError:
        say(translator.t("city_not_found", lang, city=city_name))
    except Exception as e:
        say(translator.t("city_set_failed", lang, error=str(e)))


@app.command("/asachan-settime")
def handle_settime(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    text = command.get("text", "").strip()
    if not text:
        say(translator.t("provide_time", lang))
        return

    try:
        datetime.strptime(text, "%H:%M")
        update_user_setting(user_id, time_str=text)
        say(translator.t("time_set", lang, time=text))
    except ValueError:
        say(translator.t("time_invalid", lang))


@app.command("/asachan-settimezone")
def handle_settimezone(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    text = command.get("text", "").strip().upper()
    if not text:
        say(translator.t("provide_timezone", lang))
        return

    # Validate format: UTC[+/-]H[:MM]
    pattern = r"^UTC[+-](\d{1,2})(:(\d{2}))?$"
    match = re.match(pattern, text)
    if not match:
        say(translator.t("timezone_invalid", lang))
        return

    update_user_setting(user_id, timezone=text)
    say(translator.t("timezone_set", lang, timezone=text))


@app.command("/asachan-mode")
def handle_mode(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    text = command.get("text", "").strip().lower()

    if not text:
        say(translator.t("provide_mode", lang))
        return

    if text not in ["raw", "cute", "normal"]:
        say(translator.t("mode_invalid", lang))
        return

    update_user_setting(user_id, weather_mode=text)
    say(translator.t("mode_set", lang, mode=text))


@app.command("/asachan-lang")
def handle_lang(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    text = command.get("text", "").strip().lower()

    if not text:
        say(translator.t("provide_lang", lang))
        return

    if text not in ["en", "zh"]:
        say(translator.t("lang_invalid", lang))
        return

    update_user_setting(user_id, lang=text)
    say(translator.t("lang_set", text, lang=text))


@app.command("/asachan-privacy")
def handle_privacy(ack, say, command):
    ack()
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    lang = setting.get("lang", "en")
    say(translator.t("privacy_policy", lang))


@app.command("/asachan-dailynow")
def handle_dailynow(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)

    if not setting.get("city"):
        say(translator.t("city_not_set", setting.get("lang", "en")))
        return

    report = generate_report_daily(
        user_id, setting["city"], setting.get("timezone", "Asia/Shanghai")
    )
    say(report)


# Task Scheduling System
def send_daily_report(user_id, city, timezone_str):
    report = generate_report_daily(user_id, city, timezone_str)
    try:
        app.client.chat_postMessage(channel=user_id, text=report)
    except Exception as e:
        print(f"Failed to send to {user_id}: {str(e)}")


def update_schedule(user_id, city, time_str, timezone_str):
    tz = get_tz_from_str(timezone_str)

    with schedule_lock:
        schedule.clear(user_id)
        schedule.every().day.at(time_str, tz).do(
            send_daily_report, user_id=user_id, city=city, timezone_str=timezone_str
        ).tag(user_id)


def daily_backup():
    try:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        temp_db_path = f"backups/app_{date_str}.db"
        backup_filename = f"backups/app_{date_str}.zip"
        latest_filename = "backups/app_latest.zip"

        # Ensure backup directory exists
        os.makedirs("backups", exist_ok=True)

        # Use SQLite's backup API to create a consistent copy of the database
        with db_lock:
            with get_db_conn() as source_conn:
                dest_conn = sqlite3.connect(temp_db_path)
                try:
                    source_conn.backup(dest_conn)
                finally:
                    dest_conn.close()

        # Zip the consistent copy
        with zipfile.ZipFile(backup_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(temp_db_path, arcname="app.db")

        # Remove the temporary DB file
        os.remove(temp_db_path)

        # Create a copy for app_latest.zip
        shutil.copy2(backup_filename, latest_filename)

        # Cleanup stale AI stats (older than 7 days)
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM user_ai_stats WHERE day < ?", (seven_days_ago,))
                conn.commit()

        print(f"✅ Backup created: {backup_filename} and {latest_filename}")
    except Exception as e:
        print(f"❌ Backup failed: {str(e)}")


def init_schedule():
    with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE subscribed = 1 AND city IS NOT NULL AND time IS NOT NULL AND timezone IS NOT NULL"
            )
            active_users = cursor.fetchall()

    for s in active_users:
        update_schedule(s["user_id"], s["city"], s["time"], s["timezone"])

    # Schedule daily backup at 04:00
    with schedule_lock:
        schedule.every().day.at("04:00").do(daily_backup).tag("backup")


def schedule_checker():
    while True:
        with schedule_lock:
            schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        print(
            "❌ Error: SLACK_BOT_TOKEN and SLACK_APP_TOKEN environment variables must be set"
        )
        exit(1)

    print("🔔 Asachan Weather Slack Bot started")
    init_schedule()
    threading.Thread(target=schedule_checker, daemon=True).start()

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
