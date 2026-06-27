import json
import os
import time
from datetime import datetime
import threading
from db import get_db_conn, db_lock


class StatsManager:
    def __init__(self):
        pass

    def _ensure_daily_reset(self, cursor):
        """Ensures stats are reset for the current day/month within a transaction"""
        now = datetime.now()
        current_day = now.strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")

        cursor.execute("SELECT day, month FROM stats WHERE id = 1")
        row = cursor.fetchone()
        if not row:
            return

        updates = []
        params = []

        if row["day"] != current_day:
            updates.append("day = ?, daily_api_calls = 0, daily_now_requests = 0")
            params.append(current_day)

        if row["month"] != current_month:
            updates.append("month = ?, monthly_api_calls = 0")
            params.append(current_month)

        if updates:
            cursor.execute(
                f"UPDATE stats SET {', '.join(updates)} WHERE id = 1", params
            )

    def increment_api_call(self):
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                self._ensure_daily_reset(cursor)
                cursor.execute("""
                    UPDATE stats SET
                        daily_api_calls = daily_api_calls + 1,
                        monthly_api_calls = monthly_api_calls + 1
                    WHERE id = 1
                """)
                conn.commit()

    def increment_now_request(self):
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                self._ensure_daily_reset(cursor)
                cursor.execute("""
                    UPDATE stats SET
                        daily_now_requests = daily_now_requests + 1
                    WHERE id = 1
                """)
                conn.commit()

    def can_make_api_call(self):
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                self._ensure_daily_reset(cursor)
                cursor.execute(
                    "SELECT daily_api_calls, monthly_api_calls FROM stats WHERE id = 1"
                )
                stats = cursor.fetchone()
                return (
                    stats["daily_api_calls"] < 1000
                    and stats["monthly_api_calls"] < 31000
                )

    def can_make_now_request(self, active_subscribers_count):
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                self._ensure_daily_reset(cursor)
                cursor.execute("SELECT daily_now_requests FROM stats WHERE id = 1")
                stats = cursor.fetchone()
                max_now = max(0, 1000 - active_subscribers_count)
                return stats["daily_now_requests"] < max_now


class WeatherCache:
    def __init__(self):
        self.cache = {}  # {city_name: {type: {data, expires}}}
        self.lock = threading.Lock()

    def get(self, city_name, weather_type):
        with self.lock:
            city_cache = self.cache.get(city_name, {})
            record = city_cache.get(weather_type)
            if record and record["expires"] > time.time():
                return record["data"]
            return None

    def set(self, city_name, weather_type, data, duration_seconds):
        with self.lock:
            if city_name not in self.cache:
                self.cache[city_name] = {}
            self.cache[city_name][weather_type] = {
                "data": data,
                "expires": time.time() + duration_seconds,
            }
