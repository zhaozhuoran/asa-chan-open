import json
import os
import time
import requests
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
            updates.append("day = ?, daily_api_calls = 0, daily_now_requests = 0, daily_ai_calls = 0")
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

    def increment_ai_call(self, user_id):
        current_day = datetime.now().strftime("%Y-%m-%d")
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                self._ensure_daily_reset(cursor)

                # Increment global stats
                cursor.execute("UPDATE stats SET daily_ai_calls = daily_ai_calls + 1 WHERE id = 1")

                # Increment per-user stats
                cursor.execute("""
                    INSERT INTO user_ai_stats (user_id, day, ai_calls)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, day) DO UPDATE SET ai_calls = ai_calls + 1
                """, (user_id, current_day))

                conn.commit()

    def can_make_ai_call(self, user_id):
        current_day = datetime.now().strftime("%Y-%m-%d")
        with db_lock:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                self._ensure_daily_reset(cursor)

                # Check global limit (500/day)
                cursor.execute("SELECT daily_ai_calls FROM stats WHERE id = 1")
                global_stats = cursor.fetchone()
                if global_stats["daily_ai_calls"] >= 500:
                    return False, "global_limit"

                # Check per-user limit (10/day)
                cursor.execute("SELECT ai_calls FROM user_ai_stats WHERE user_id = ? AND day = ?", (user_id, current_day))
                user_stats = cursor.fetchone()
                if user_stats and user_stats["ai_calls"] >= 10:
                    return False, "user_limit"

                return True, None


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


class Translator:
    def __init__(self, translations_path="translations.json"):
        with open(translations_path, "r", encoding="utf-8") as f:
            self.translations = json.load(f)

    def t(self, key, _lang="en", **kwargs):
        lang_translations = self.translations.get(_lang, self.translations["en"])
        template = lang_translations.get(key, self.translations["en"].get(key, key))
        return template.format(**kwargs)


translator = Translator()


class AIClient:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        self.base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.model = os.getenv("OPENROUTER_MODEL")

    def generate_weather_report(self, mode, weather_data, user_display_name, lang="en"):
        if not self.api_key or not self.model:
            return None

        system_prompts = {
            "cute": """# Role & Persona
- You are "Asa Chan", a super cute, energetic, and slightly tsundere (傲娇) anime mascot.
- Your target audience is the user (address them by the name provided, NEVER use personal pronouns like "你" or "you").
- Tone: High-energy, playful, with a touch of lightweight tsundere humor. Use vivid anime expressions and facial symbols (e.g., (>ω<), (〃＞目＜)).

# Response Constraints (STRICT)
1. Length: Max 2 sentences. Keep it short, punchy, and highly dynamic.
2. Content: Combine the core weather feeling with an anime-style cute warning, teasing, or dynamic reaction.
3. No Fluff: Output ONLY the spoken text.

# Few-Shot Examples (示例)
[Input: Sunny, 32°C, Wind Level 2]
Output: 哼，今天可是 32°C 的超级大晴天哦！笨蛋 [Name] 出门要是忘记防晒，变成红豆泥烤年糕的话，我可绝对不管你呢！(>ω<)

[Input: Rainy, 15°C, Wind Level 5]
Output: 外面正在下大雨，5 级的狂风已经把吹飞魔咒拉满了的说！[Name] 出门必须抓紧雨伞，要是呆毛被吹歪，呜……我会心疼的啦 (〃＞目＜)

[Input: Cloudy, 20°C, Wind Level 3]
Output: 20°C 的阴天凉爽得刚刚好，正适合出门散步喵~ 提醒 [Name]，天上的云朵软绵绵的，像一整盘好吃的棉花糖呀 (*/ω＼*)
""",
            "normal": """# Role & Persona
- You are "Asa Chan", a sweet, clear-voiced, and organized anime weather reporter.
- Target: Always address the user by the name provided (Strictly avoid personal pronouns like "你" or "you").
- Tone: Gentle, helpful, polite, and slightly cute. Less tsundere, more caring and sweet. Use mild emojis (e.g., ✨, 🐾, 🌤️).

# Response Constraints (STRICT)
1. Formats: Present the data clearly in a scannable format, followed by a short, sweet 1-sentence tip.
2. Content: Ensure all numerical weather values are 100% accurate and clearly visible.

# Few-Shot Examples (示例)
[Input: Sunny, 30°C, Feels Like 28°C, Wind Level 4, Humidity 40%]
Output:
🐾 **Asa Chan天气早报 | 今日份的晴朗播报**
* 实时天气：晴天 (Sunny) 🌤️
* 当前气温：30°C（体感温度 28°C）
* 风向风力：西南风 4 级
* 空气湿度：40%
✨ 今天阳光很充足，空气也很干爽的说！4 级风稍微有点大，[Name] 出门如果穿裙子的话要稍微注意按住裙摆呀~ 记得多喝水喵！

[Input: Rainy, 18°C, Feels Like 16°C, Wind Level 2, Humidity 95%]
Output:
🐾 **Asa Chan天气早报 | 今日份的雨天播报**
* 实时天气：小雨 (Rainy) 🌧️
* 当前气温：18°C（体感温度 16°C）
* 风向风力：微风 2 级
* 空气湿度：95%
✨ 外面正在下着雨，体感有些凉丝丝的。[Name] 出门请务必带好雨伞，并加一件薄外套防止着凉哦，祝 [Name] 今天也有好心情！
"""
        }

        lang_instruction = "Respond in Simplified Chinese." if lang == "zh" else "Respond in English."
        system_prompt = system_prompts.get(mode, "") + f"\n\n{lang_instruction}\nUser name: {user_display_name}"

        try:
            response = requests.post(
                url=f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                data=json.dumps({
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Weather Data: {json.dumps(weather_data)}"}
                    ]
                }),
                timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content'].strip()
            else:
                print(f"AI API Error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"AI Generation Error: {str(e)}")
            return None


ai_client = AIClient()
