"""db.py — SQLite 数据层：visitors/sessions/messages"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"
_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not getattr(_local, "conn", None):
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return _local.conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS visitors (
            visitor_id TEXT PRIMARY KEY,
            first_seen TEXT, last_seen TEXT,
            msg_count INTEGER DEFAULT 0,
            note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            visitor_id TEXT,
            created_at TEXT, last_msg_at TEXT,
            status TEXT DEFAULT 'active',
            msg_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            visitor_id TEXT,
            direction TEXT,
            msg_type TEXT,
            content TEXT,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_msg_visitor ON messages(visitor_id);
        CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_sess_visitor ON sessions(visitor_id);
        """
    )
    conn.commit()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def ensure_visitor(visitor_id: str) -> None:
    if not visitor_id:
        return
    conn = _conn()
    conn.execute(
        "INSERT OR IGNORE INTO visitors(visitor_id, first_seen, last_seen) VALUES(?,?,?)",
        (visitor_id, _now(), _now()),
    )
    conn.execute("UPDATE visitors SET last_seen=? WHERE visitor_id=?", (_now(), visitor_id))
    conn.commit()


def ensure_session(session_id: str, visitor_id: str | None) -> None:
    if not session_id:
        return
    conn = _conn()
    conn.execute(
        "INSERT OR IGNORE INTO sessions(session_id, visitor_id, created_at, last_msg_at) VALUES(?,?,?,?)",
        (session_id, visitor_id or "", _now(), _now()),
    )
    conn.execute("UPDATE sessions SET last_msg_at=? WHERE session_id=?", (_now(), session_id))
    conn.commit()


def insert_msg(session_id: str | None, visitor_id: str | None, direction: str, msg_type: str, content: str) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO messages(session_id, visitor_id, direction, msg_type, content, created_at) VALUES(?,?,?,?,?,?)",
        (session_id or "", visitor_id or "", direction, msg_type, content, _now()),
    )
    if visitor_id:
        conn.execute(
            "UPDATE visitors SET msg_count=msg_count+1, last_seen=? WHERE visitor_id=?",
            (_now(), visitor_id),
        )
    if session_id:
        conn.execute(
            "UPDATE sessions SET msg_count=msg_count+1, status='active' WHERE session_id=?",
            (session_id,),
        )
    conn.commit()


def query_messages(session_id: str | None = None, visitor_id: str | None = None,
                   keyword: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    q = "SELECT * FROM messages WHERE 1=1"
    args: list = []
    if session_id:
        q += " AND session_id=?"
        args.append(session_id)
    if visitor_id:
        q += " AND visitor_id=?"
        args.append(visitor_id)
    if keyword:
        q += " AND content LIKE ?"
        args.append(f"%{keyword}%")
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in _conn().execute(q, args).fetchall()]


def list_visitors(limit: int = 50, offset: int = 0) -> list[dict]:
    rows = _conn().execute(
        "SELECT v.*, (SELECT COUNT(*) FROM messages m WHERE m.visitor_id=v.visitor_id) AS total_msgs "
        "FROM visitors v ORDER BY v.last_seen DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def list_sessions(visitor_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    q = "SELECT * FROM sessions WHERE 1=1"
    args: list = []
    if visitor_id:
        q += " AND visitor_id=?"
        args.append(visitor_id)
    q += " ORDER BY last_msg_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in _conn().execute(q, args).fetchall()]


def stats() -> dict:
    c = _conn()
    today = _now()[:10]

    def one(q):
        return c.execute(q, (today,)).fetchone()[0]

    return {
        "visitors": c.execute("SELECT COUNT(*) FROM visitors").fetchone()[0],
        "sessions": c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "today_msgs": one("SELECT COUNT(*) FROM messages WHERE created_at LIKE ?"),
        "today_visitors": one("SELECT COUNT(DISTINCT visitor_id) FROM messages WHERE created_at LIKE ?"),
        "attachments": c.execute("SELECT COUNT(*) FROM messages WHERE msg_type!='text'").fetchone()[0],
        "top_keywords": [],
    }
