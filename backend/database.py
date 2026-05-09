"""
SQLite database: users, memberships, daily_usage.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "vidflow.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
            status TEXT NOT NULL CHECK(status IN ('active', 'expired', 'cancelled')),
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            amount REAL NOT NULL,
            stripe_session_id TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            usage_date TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            UNIQUE(user_id, usage_date)
        );
    """)
    conn.commit()

    # Migration: add stripe_session_id column if missing (for DBs created by older code)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memberships)").fetchall()]
    if "stripe_session_id" not in cols:
        conn.execute("ALTER TABLE memberships ADD COLUMN stripe_session_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memberships_stripe_session ON memberships(stripe_session_id)")
        conn.commit()
