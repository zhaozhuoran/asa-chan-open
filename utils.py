import json
import os
import time
from datetime import datetime
import threading

STATS_FILE = "data/stats.json"

class StatsManager:
    def __init__(self, stats_file=STATS_FILE):
        self.stats_file = stats_file
        self.lock = threading.Lock()
        self.stats = self._load_stats()

    def _load_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r") as f:
                    stats = json.load(f)
                    # Check for day/month reset
                    now = datetime.now()
                    current_day = now.strftime("%Y-%m-%d")
                    current_month = now.strftime("%Y-%m")

                    if stats.get("day") != current_day:
                        stats["day"] = current_day
                        stats["daily_api_calls"] = 0
                        stats["daily_now_requests"] = 0

                    if stats.get("month") != current_month:
                        stats["month"] = current_month
                        stats["monthly_api_calls"] = 0

                    return stats
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        now = datetime.now()
        return {
            "day": now.strftime("%Y-%m-%d"),
            "month": now.strftime("%Y-%m"),
            "daily_api_calls": 0,
            "monthly_api_calls": 0,
            "daily_now_requests": 0
        }

    def _save_stats(self):
        with open(self.stats_file, "w") as f:
            json.dump(self.stats, f, indent=4)

    def increment_api_call(self):
        with self.lock:
            self._check_reset()
            self.stats["daily_api_calls"] += 1
            self.stats["monthly_api_calls"] += 1
            self._save_stats()

    def increment_now_request(self):
        with self.lock:
            self._check_reset()
            self.stats["daily_now_requests"] += 1
            self._save_stats()

    def _check_reset(self):
        now = datetime.now()
        current_day = now.strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")

        changed = False
        if self.stats.get("day") != current_day:
            self.stats["day"] = current_day
            self.stats["daily_api_calls"] = 0
            self.stats["daily_now_requests"] = 0
            changed = True

        if self.stats.get("month") != current_month:
            self.stats["month"] = current_month
            self.stats["monthly_api_calls"] = 0
            changed = True

        if changed:
            self._save_stats()

    def can_make_api_call(self):
        with self.lock:
            self._check_reset()
            return (self.stats["daily_api_calls"] < 1000 and
                    self.stats["monthly_api_calls"] < 31000)

    def can_make_now_request(self, active_subscribers_count):
        with self.lock:
            self._check_reset()
            max_now = max(0, 1000 - active_subscribers_count)
            return self.stats["daily_now_requests"] < max_now

class WeatherCache:
    def __init__(self):
        self.cache = {} # {city_name: {type: {data, expires}}}
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
                "expires": time.time() + duration_seconds
            }
