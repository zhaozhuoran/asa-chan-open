import json
import os
from db import get_db_conn, db_lock

USER_SETTINGS_FILE = "data/user_settings.json"
STATS_FILE = "data/stats.json"

def migrate():
    migrated_users = False
    migrated_stats = False

    with get_db_conn() as conn:
        cursor = conn.cursor()

        # Migrate users
        if os.path.exists(USER_SETTINGS_FILE) and not USER_SETTINGS_FILE.endswith(".bak"):
            try:
                with open(USER_SETTINGS_FILE, "r") as f:
                    settings = json.load(f)
                    for user_id, s in settings.items():
                        with db_lock:
                            cursor.execute('''
                                INSERT OR IGNORE INTO users (user_id, city, time, timezone, subscribed, initialized)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (
                                user_id,
                                s.get("city"),
                                s.get("time"),
                                s.get("timezone"),
                                1 if s.get("subscribed") else 0,
                                1 if s.get("initialized") else 0
                            ))
                print(f"Migrated {len(settings)} users.")
                migrated_users = True
            except Exception as e:
                print(f"Error migrating users: {e}")

        # Migrate stats
        if os.path.exists(STATS_FILE) and not STATS_FILE.endswith(".bak"):
            try:
                with open(STATS_FILE, "r") as f:
                    stats = json.load(f)
                    with db_lock:
                        # Only migrate stats if the DB stats are empty (initial state)
                        cursor.execute('SELECT day FROM stats WHERE id = 1')
                        current_day = cursor.fetchone()[0]
                        if not current_day:
                            cursor.execute('''
                                UPDATE stats SET
                                    day = ?,
                                    month = ?,
                                    daily_api_calls = ?,
                                    monthly_api_calls = ?,
                                    daily_now_requests = ?
                                WHERE id = 1
                            ''', (
                                stats.get("day", ""),
                                stats.get("month", ""),
                                stats.get("daily_api_calls", 0),
                                stats.get("monthly_api_calls", 0),
                                stats.get("daily_now_requests", 0)
                            ))
                            migrated_stats = True
                            print("Migrated stats.")
            except Exception as e:
                print(f"Error migrating stats: {e}")

        conn.commit()

    # Rename files to prevent repeated migration
    if migrated_users:
        os.rename(USER_SETTINGS_FILE, USER_SETTINGS_FILE + ".bak")
    if migrated_stats:
        os.rename(STATS_FILE, STATS_FILE + ".bak")

if __name__ == "__main__":
    migrate()
