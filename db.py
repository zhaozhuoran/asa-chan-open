import sqlite3
import threading
import os
from contextlib import contextmanager

DB_PATH = "data/app.db"
db_lock = threading.Lock()


@contextmanager
def get_db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    os.makedirs("data", exist_ok=True)
    with db_lock:
        with get_db_conn() as conn:
            cursor = conn.cursor()

            # Schema version table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)

            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    city TEXT,
                    time TEXT,
                    timezone TEXT,
                    subscribed BOOLEAN DEFAULT 0,
                    initialized BOOLEAN DEFAULT 0,
                    lang TEXT DEFAULT 'en',
                    weather_mode TEXT DEFAULT 'raw'
                )
            """)

            # Stats table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    day TEXT,
                    month TEXT,
                    daily_api_calls INTEGER DEFAULT 0,
                    monthly_api_calls INTEGER DEFAULT 0,
                    daily_now_requests INTEGER DEFAULT 0,
                    daily_ai_calls INTEGER DEFAULT 0
                )
            """)

            # User AI stats table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_ai_stats (
                    user_id TEXT,
                    day TEXT,
                    ai_calls INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, day)
                )
            """)

            # Initialize stats row if not exists
            cursor.execute("SELECT COUNT(*) FROM stats WHERE id = 1")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO stats (id, day, month, daily_api_calls, monthly_api_calls, daily_now_requests, daily_ai_calls)
                    VALUES (1, '', '', 0, 0, 0, 0)
                """)

            # Handle migrations
            cursor.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()

            if row and row[0] is not None:
                current_version = row[0]
            else:
                # Check for legacy databases missing schema_version
                cursor.execute("PRAGMA table_info(users)")
                columns = [r["name"] for r in cursor.fetchall()]
                if "lang" in columns and "weather_mode" in columns:
                    current_version = 3
                elif "lang" in columns:
                    current_version = 2
                else:
                    current_version = 1
                cursor.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (current_version,))

            if current_version < 4:
                # v1-v3 to v4: Robustly ensure all columns exist
                # This handles cases where migrations might have partially failed or columns were missed

                # Users table columns
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'en'")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN weather_mode TEXT DEFAULT 'raw'")
                except sqlite3.OperationalError:
                    pass

                # Stats table columns
                try:
                    cursor.execute("ALTER TABLE stats ADD COLUMN daily_ai_calls INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE stats ADD COLUMN daily_now_requests INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass

            # Always ensure schema_version reflects that we are at the latest version after migrations
            cursor.execute("DELETE FROM schema_version")
            cursor.execute("INSERT INTO schema_version (version) VALUES (4)")
            conn.commit()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
