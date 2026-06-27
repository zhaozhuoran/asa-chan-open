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

            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    city TEXT,
                    time TEXT,
                    timezone TEXT,
                    subscribed BOOLEAN DEFAULT 0,
                    initialized BOOLEAN DEFAULT 0
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
                    daily_now_requests INTEGER DEFAULT 0
                )
            """)

            # Initialize stats row if not exists
            cursor.execute("SELECT COUNT(*) FROM stats WHERE id = 1")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO stats (id, day, month, daily_api_calls, monthly_api_calls, daily_now_requests)
                    VALUES (1, '', '', 0, 0, 0)
                """)

            conn.commit()


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
