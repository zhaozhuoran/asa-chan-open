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
from utils import StatsManager, WeatherCache
import re

# Initialize environment
load_dotenv()
QWEATHER_KEY = os.getenv("QWEATHER_KEY", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
USER_SETTINGS_FILE = "data/user_settings.json"

# Ensure data directory exists
os.makedirs("data", exist_ok=True)

app = App(token=SLACK_BOT_TOKEN)

# Thread safety locks
settings_lock = threading.Lock()
schedule_lock = threading.Lock()

# Global managers
stats_manager = StatsManager()
weather_cache = WeatherCache()


# User settings management
def load_settings():
    if os.path.exists(USER_SETTINGS_FILE):
        try:
            with open(USER_SETTINGS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}


def save_settings(settings):
    with open(USER_SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)


def update_user_setting(
    user_id, city=None, time_str=None, timezone=None, subscribed=None, initialized=None
):
    with settings_lock:
        settings = load_settings()
        if user_id not in settings:
            settings[user_id] = {
                "city": None,
                "time": None,
                "timezone": None,
                "subscribed": False,
                "initialized": False,
            }

        if city is not None:
            settings[user_id]["city"] = city
        if time_str is not None:
            settings[user_id]["time"] = time_str
        if timezone is not None:
            settings[user_id]["timezone"] = timezone
        if subscribed is not None:
            settings[user_id]["subscribed"] = subscribed
        if initialized is not None:
            settings[user_id]["initialized"] = initialized

        save_settings(settings)
        user_setting = settings[user_id]

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
    with settings_lock:
        settings = load_settings()
        return settings.get(
            user_id,
            {
                "city": None,
                "time": None,
                "timezone": None,
                "subscribed": False,
                "initialized": False,
            },
        )


def get_active_subscribers_count():
    with settings_lock:
        settings = load_settings()
        return sum(1 for s in settings.values() if s.get("subscribed"))


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


def generate_report_daily(city_name: str, timezone_str="Asia/Shanghai"):
    data = fetch_weather_daily(city_name)
    if not data or not data.get("daily") or len(data["daily"]) == 0:
        return "⚠️ Failed to fetch weather data, please try again later"

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
            "\n⚠️ High UV Index, please use sun protection"
            if int(uv_index) >= 5
            else ""
        )
    except:
        uv_warning = ""

    return f"""🗓️ {now.strftime('%Y.%m.%d')} Weather Report for {city_name}:
☀️ Condition: {text_day}
🌡️ Temp: {temp_min}-{temp_max}℃
☀️ UV Index: {uv_index}
💨 Wind: Level {wind_scale_day} {wind_dir_day}
💧 Humidity: {humidity}%{uv_warning}"""


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


def generate_report_now(city_name: str, timezone_str="Asia/Shanghai"):
    data = fetch_weather_now(city_name)
    if not data or not data.get("now"):
        return "⚠️ Failed to fetch weather data, please try again later"

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

    return f"""🗓️ {now.strftime('%Y.%m.%d %H:%M')} Real-time Weather for {city_name}:
☀️ Condition: {text}
🌡️ Temp: {temp}℃
🌡️ Feels Like: {feels_like}℃
💨 Wind: Level {wind_scale} {wind_dir}
💧 Humidity: {humidity}%
🌧️ Pressure: {pressure} hPa
🌧️ Precipitation (last 1h): {precip} mm"""


def send_welcome_message(say, user_id):
    welcome_text = (
        f"Hello! 👋 Welcome to AsaChan Weather Bot.\n\n"
        "To get started, you need to set up your preferences using the following commands:\n"
        "1️⃣ `/asachan-setcity [City]` - Set your city (e.g., `/asachan-setcity Tokyo`)\n"
        "2️⃣ `/asachan-settimezone [UTC Offset]` - Set your timezone (e.g., `/asachan-settimezone UTC+9` or `UTC-5:30`)\n"
        "3️⃣ `/asachan-settime [HH:MM]` - Set your daily notification time (e.g., `/asachan-settime 08:00`)\n\n"
        "Once these are set, you can use `/asachan-start` to subscribe to daily reports or `/asachan-now` for real-time weather.\n"
    )
    say(welcome_text)
    update_user_setting(user_id, initialized=True)


def check_initialization(ack, say, command):
    ack()
    user_id = command["user_id"]
    setting = get_user_setting(user_id)
    if not setting.get("initialized"):
        send_welcome_message(say, user_id)
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
            send_welcome_message(say, user_id)


# Slack Command Handlers
@app.command("/asachan-ping")
def handle_ping(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    say("Pong!")


@app.command("/asachan-up")
def handle_up(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    say("Bot is up and running!")


@app.command("/asachan-start")
def handle_start(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)

    # Validation
    missing = []
    if not setting.get("city"):
        missing.append("City (`/asachan-setcity`)")
    if not setting.get("time"):
        missing.append("Notification Time (`/asachan-settime`)")
    if not setting.get("timezone"):
        missing.append("Timezone (`/asachan-settimezone`)")

    if missing:
        say(
            f"❌ Subscription failed. Please complete your settings first:\n"
            + "\n".join(f"- {m}" for m in missing)
        )
        return

    # Check 1k subscriber limit
    if get_active_subscribers_count() >= 1000 and not setting.get("subscribed"):
        say("❌ Sorry, the bot has reached the maximum number of subscribers (1000).")
        return

    setting = update_user_setting(user_id, subscribed=True)
    say(
        f"✅ Subscription successful! Daily weather report will be sent at {setting['time']} ({setting['timezone']})\n📍 Current City: {setting['city']}"
    )


@app.command("/asachan-unsub")
def handle_unsub(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    update_user_setting(user_id, subscribed=False)
    say("❎ Unsubscribed successfully")


@app.command("/asachan-now")
def handle_now(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)

    if not setting.get("city"):
        say("❌ City not set. Please use `/asachan-setcity [City]` first.")
        return

    # Quota check for now requests: subscribers are always allowed (if API quota permits)
    # Non-subscribers are limited to (1000 - current_subscribers) total requests per day
    if not setting.get("subscribed"):
        if not stats_manager.can_make_now_request(get_active_subscribers_count()):
            say(
                "❌ The bot is currently receiving too many requests. Please try again later."
            )
            return
        stats_manager.increment_now_request()

    report = generate_report_now(
        setting["city"], setting.get("timezone", "Asia/Shanghai")
    )
    say(report)


@app.command("/asachan-status")
def handle_status(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)

    status_msg = f"📍 City: {setting.get('city') or 'Not set'}\n"
    status_msg += f"⏰ Time: {setting.get('time') or 'Not set'}\n"
    status_msg += f"🌍 Timezone: {setting.get('timezone') or 'Not set'}\n"
    status_msg += f"🔔 Subscribed: {'Yes' if setting.get('subscribed') else 'No'}"

    say(f"Current Settings:\n{status_msg}")


@app.command("/asachan-setcity")
def handle_setcity(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    text = command.get("text", "").strip()
    if not text:
        say("Please provide a city name, e.g., `/asachan-setcity Shanghai`")
        return

    city_name = text
    try:
        get_location_id(city_name)
        update_user_setting(user_id, city=city_name)
        say(f"✅ City set to: {city_name}")
    except ValueError:
        say(f"❌ City not found: {city_name}, please check the spelling")
    except Exception as e:
        say(f"❌ Failed to set city: {str(e)}")


@app.command("/asachan-settime")
def handle_settime(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    text = command.get("text", "").strip()
    if not text:
        say("Please provide a time (HH:MM), e.g., `/asachan-settime 08:30`")
        return

    try:
        datetime.strptime(text, "%H:%M")
        update_user_setting(user_id, time_str=text)
        say(f"✅ Notification time set to: {text}")
    except ValueError:
        say("❌ Invalid time format, please use HH:MM (e.g., 07:15)")


@app.command("/asachan-settimezone")
def handle_settimezone(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    text = command.get("text", "").strip().upper()
    if not text:
        say(
            "Please provide a UTC offset, e.g., `/asachan-settimezone UTC+8` or `UTC-5:30`"
        )
        return

    # Validate format: UTC[+/-]H[:MM]
    pattern = r"^UTC[+-](\d{1,2})(:(\d{2}))?$"
    match = re.match(pattern, text)
    if not match:
        say(
            "❌ Invalid timezone format. Please use UTC+H, UTC+H:MM, UTC-H, or UTC-H:MM (e.g., UTC+8, UTC-5:30)"
        )
        return

    update_user_setting(user_id, timezone=text)
    say(f"✅ Timezone set to: {text}")


@app.command("/asachan-dailynow")
def handle_dailynow(ack, say, command):
    if not check_initialization(ack, say, command):
        return
    user_id = command["user_id"]
    setting = get_user_setting(user_id)

    if not setting.get("city"):
        say("❌ City not set. Please use `/asachan-setcity [City]` first.")
        return

    report = generate_report_daily(
        setting["city"], setting.get("timezone", "Asia/Shanghai")
    )
    say(report)


# Task Scheduling System
def send_daily_report(user_id, city, timezone_str):
    report = generate_report_daily(city, timezone_str)
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


def init_schedule():
    with settings_lock:
        settings = load_settings()
    for user_id, s in settings.items():
        if (
            s.get("subscribed")
            and s.get("city")
            and s.get("time")
            and s.get("timezone")
        ):
            update_schedule(user_id, s.get("city"), s.get("time"), s.get("timezone"))


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
