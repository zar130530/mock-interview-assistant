"""SQLite 持久化层：简历去重存储 + 面试记录。"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).resolve().parent / "interview.db"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS resumes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT UNIQUE,
            filename      TEXT,
            raw_text      TEXT,
            created_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS interviews (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            resume_id      INTEGER,
            company        TEXT,
            position       TEXT,
            jd             TEXT,
            knowledge_base TEXT DEFAULT '',
            alignment      TEXT,
            questions      TEXT,
            status         TEXT DEFAULT 'pending',
            user_answers   TEXT DEFAULT '{}',
            created_at     TEXT,
            updated_at     TEXT
        );
        """
    )
    conn.commit()
    conn.close()
