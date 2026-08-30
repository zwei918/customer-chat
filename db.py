"""db.py — SQLite 数据层：visitors/sessions/messages"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

DB_PATH = Path(os.environ.get("XIAOMEI_DB") or Path(__file__).parent / "data.db")
_UPLOAD_ROOT = Path(os.environ.get("XIAOMEI_UPLOADS") or Path(__file__).parent)
_local = threading.local()

_KW_STOP = set("的了是在我不有和就人都吗呢啊哦呀这那什么怎么可以会能要到")
_LATIN_WORD = re.compile(r"[A-Za-z0-9]{2,}")


def _conn() -> sqlite3.Connection:
    if not getattr(_local, "conn", None):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return _local.conn


def _table_cols(table: str) -> set[str]:
    return {str(r[1]) for r in _conn().execute(f"PRAGMA table_info({table})").fetchall()}


def _add_col(table: str, name: str, spec: str) -> None:
    if name not in _table_cols(table):
        _conn().execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def init_db() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS visitors (
            visitor_id TEXT PRIMARY KEY,
            first_seen TEXT, last_seen TEXT,
            msg_count INTEGER DEFAULT 0,
            note TEXT DEFAULT '',
            workspace_id INTEGER DEFAULT 1,
            tags TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            visitor_id TEXT,
            created_at TEXT, last_msg_at TEXT,
            status TEXT DEFAULT 'bot',
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
        CREATE INDEX IF NOT EXISTS idx_msg_dir_id ON messages(direction, id);
        CREATE INDEX IF NOT EXISTS idx_sess_visitor ON sessions(visitor_id);
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS skill_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            login_name TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'agent',
            skill_group_id INTEGER,
            max_chats INTEGER NOT NULL DEFAULT 8,
            presence TEXT NOT NULL DEFAULT 'offline',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token_hash TEXT PRIMARY KEY,
            staff_id INTEGER,
            created_at TEXT,
            expires_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            slug TEXT,
            welcome TEXT DEFAULT '',
            guide_qs TEXT DEFAULT '[]',
            allow_image INTEGER DEFAULT 1,
            allow_file INTEGER DEFAULT 1,
            allow_voice INTEGER DEFAULT 1,
            chat_bg TEXT DEFAULT '#f2f2f7',
            online INTEGER DEFAULT 1,
            online_lock INTEGER DEFAULT 1,
            is_default INTEGER DEFAULT 0,
            handoff_keywords TEXT DEFAULT '转人工,人工客服,找人工'
        );
        CREATE TABLE IF NOT EXISTS canned_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            group_name TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS csat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            visitor_id TEXT,
            score INTEGER,
            comment TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            session_id TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL DEFAULT 1,
            staff_id INTEGER,
            action TEXT,
            detail TEXT,
            created_at TEXT
        );
        """
    )
    for col, spec in [
        ("workspace_id", "INTEGER DEFAULT 1"),
        ("brand_id", "INTEGER"),
        ("channel", "TEXT DEFAULT 'web'"),
        ("assignee_id", "INTEGER"),
        ("skill_group_id", "INTEGER"),
    ]:
        _add_col("sessions", col, spec)
    for col, spec in [
        ("sender_type", "TEXT"),
        ("staff_id", "INTEGER"),
        ("workspace_id", "INTEGER DEFAULT 1"),
    ]:
        _add_col("messages", col, spec)
    _add_col("visitors", "workspace_id", "INTEGER DEFAULT 1")
    _add_col("visitors", "tags", "TEXT DEFAULT ''")
    _add_col("visitors", "avatar", "TEXT DEFAULT ''")
    _add_col("staff", "workspace_id", "INTEGER DEFAULT 1")
    _add_col("staff", "skill_group_id", "INTEGER")
    _add_col("staff", "max_chats", "INTEGER DEFAULT 8")
    _add_col("staff", "presence", "TEXT DEFAULT 'offline'")

    conn.execute("INSERT OR IGNORE INTO workspaces(id, name, created_at) VALUES(1, '默认工作区', ?)", (_now(),))
    conn.execute("INSERT OR IGNORE INTO skill_groups(id, workspace_id, name) VALUES(1, 1, '默认组')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('name', '客服小美')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('admin_name', '运营中台')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('online', '1')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('chat_bg', '#f2f2f7')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('last_read_msg_id', '0')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('allow_image', '1')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('allow_file', '1')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('allow_voice', '1')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('routing_mode', 'least_busy')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('rr_index', '0')")
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('sticky_enabled', '1')")
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES('business_hours',"
        "'{\"enabled\":false,\"tz\":\"Asia/Shanghai\",\"start\":\"09:00\",\"end\":\"18:00\",\"days\":[1,2,3,4,5]}')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES('guide_text', '你好，我是{name}，有问题直接发给我就好。')"
    )
    conn.execute(
        """INSERT OR IGNORE INTO settings(key, value) VALUES('guide_qs',
        '[{"label":"怎么收费？","q":"你们怎么收费？"},{"label":"怎么合作？","q":"怎么开始合作？"},{"label":"转人工","q":"转人工"}]')"""
    )
    if conn.execute("SELECT 1 FROM settings WHERE key='online_lock'").fetchone() is None:
        online = get_setting("online", "1")
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('online_lock', ?)",
            ("0" if online == "0" else "1",),
        )
    conn.execute("UPDATE sessions SET status='bot' WHERE status IS NULL OR status='' OR status='active'")
    if conn.execute("SELECT 1 FROM brands").fetchone() is None:
        conn.execute(
            """INSERT INTO brands(workspace_id, name, slug, welcome, guide_qs, allow_image, allow_file, allow_voice,
               chat_bg, online, online_lock, is_default, handoff_keywords)
               VALUES(1, ?, 'default', ?, ?, 1, 1, 1, ?, 1, 1, 1, '转人工,人工客服,找人工')""",
            (
                get_setting("name", "客服小美") or "客服小美",
                get_setting("guide_text", "你好，我是{name}，有问题直接发给我就好。"),
                get_setting("guide_qs", "[]"),
                get_setting("chat_bg", DEFAULT_CHAT_BG) or DEFAULT_CHAT_BG,
            ),
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
    row = conn.execute("SELECT avatar FROM visitors WHERE visitor_id=?", (visitor_id,)).fetchone()
    current = (row["avatar"] if row else "") or ""
    if not visitor_avatar_path(current):
        pick = _pick_visitor_avatar()
        if pick:
            conn.execute("UPDATE visitors SET avatar=? WHERE visitor_id=?", (pick, visitor_id))
    conn.commit()


def ensure_session(session_id: str, visitor_id: str | None) -> None:
    if not session_id:
        return
    conn = _conn()
    conn.execute(
        """INSERT OR IGNORE INTO sessions(session_id, visitor_id, created_at, last_msg_at, status, workspace_id, channel)
           VALUES(?,?,?,?, 'bot', 1, 'web')""",
        (session_id, visitor_id or "", _now(), _now()),
    )
    conn.execute("UPDATE sessions SET last_msg_at=? WHERE session_id=?", (_now(), session_id))
    conn.commit()


def insert_msg(session_id: str | None, visitor_id: str | None, direction: str, msg_type: str, content: str,
               sender_type: str | None = None, staff_id: int | None = None) -> int:
    if not sender_type:
        sender_type = "visitor" if direction == "customer" else "bot"
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO messages(session_id, visitor_id, direction, msg_type, content, created_at,
           sender_type, staff_id, workspace_id) VALUES(?,?,?,?,?,?,?,?,1)""",
        (session_id or "", visitor_id or "", direction, msg_type, content, _now(), sender_type, staff_id),
    )
    msg_id = int(cur.lastrowid)
    if visitor_id:
        conn.execute(
            "UPDATE visitors SET msg_count=msg_count+1, last_seen=? WHERE visitor_id=?",
            (_now(), visitor_id),
        )
    if session_id:
        conn.execute(
            "UPDATE sessions SET msg_count=msg_count+1, last_msg_at=? WHERE session_id=?",
            (_now(), session_id),
        )
    conn.commit()
    return msg_id


def attach_messages_session(msg_ids: list[int], session_id: str, visitor_id: str | None) -> None:
    """首条客户消息先入库、后拿到 AstrBot session_id 时回填，避免看板「有消息、会话为 0」。"""
    ids = [int(i) for i in msg_ids if i]
    if not ids or not session_id:
        return
    ensure_session(session_id, visitor_id)
    conn = _conn()
    conn.executemany(
        "UPDATE messages SET session_id=? WHERE id=? AND (session_id='' OR session_id IS NULL)",
        [(session_id, i) for i in ids],
    )
    n = int(conn.execute("SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0])
    conn.execute(
        "UPDATE sessions SET msg_count=?, last_msg_at=? WHERE session_id=?",
        (n, _now(), session_id),
    )
    conn.commit()


def query_messages(session_id: str | None = None, visitor_id: str | None = None,
                   keyword: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    where, args = _msg_where(session_id, visitor_id, keyword)
    q = "SELECT * FROM messages" + where + " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in _conn().execute(q, args).fetchall()]


def count_messages(session_id: str | None = None, visitor_id: str | None = None,
                   keyword: str | None = None) -> int:
    where, args = _msg_where(session_id, visitor_id, keyword)
    return int(_conn().execute("SELECT COUNT(*) FROM messages" + where, args).fetchone()[0])


def _msg_where(session_id: str | None, visitor_id: str | None, keyword: str | None) -> tuple[str, list]:
    q = " WHERE 1=1"
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
    return q, args


def list_visitors(limit: int = 50, offset: int = 0) -> list[dict]:
    rows = _conn().execute(
        "SELECT v.*, (SELECT COUNT(*) FROM messages m WHERE m.visitor_id=v.visitor_id) AS total_msgs "
        "FROM visitors v ORDER BY v.last_seen DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["avatar_url"] = visitor_avatar_url(d.get("avatar"))
        out.append(d)
    return out


def count_visitors() -> int:
    return int(_conn().execute("SELECT COUNT(*) FROM visitors").fetchone()[0])


def list_sessions(visitor_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    q = "SELECT * FROM sessions WHERE 1=1"
    args: list = []
    if visitor_id:
        q += " AND visitor_id=?"
        args.append(visitor_id)
    q += " ORDER BY last_msg_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in _conn().execute(q, args).fetchall()]


def unread_customer() -> int:
    last = 0
    try:
        last = int(get_setting("last_read_msg_id") or 0)
    except (TypeError, ValueError):
        last = 0
    return int(
        _conn().execute(
            "SELECT COUNT(*) FROM messages WHERE direction='customer' AND id>?",
            (last,),
        ).fetchone()[0]
    )


def mark_inbox_read() -> int:
    max_id = int(
        _conn().execute(
            "SELECT COALESCE(MAX(id),0) FROM messages WHERE direction='customer'"
        ).fetchone()[0]
    )
    set_setting("last_read_msg_id", str(max_id))
    return max_id


def _top_keywords(limit: int = 20) -> list[tuple[str, int]]:
    rows = _conn().execute(
        "SELECT content FROM messages WHERE direction='customer' AND msg_type='text' ORDER BY id DESC LIMIT 200"
    ).fetchall()
    cnt: Counter = Counter()
    for r in rows:
        s = str(r["content"] or "")
        chars = [ch for ch in s if "\u4e00" <= ch <= "\u9fff"]
        for i in range(len(chars) - 1):
            gram = chars[i] + chars[i + 1]
            if all(c in _KW_STOP for c in gram):
                continue
            cnt[gram] += 1
        for w in _LATIN_WORD.findall(s):
            cnt[w.lower()] += 1
    return cnt.most_common(limit)


def stats() -> dict:
    c = _conn()
    today = _now()[:10]
    yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 13 * 86400))

    def n(q, args=()):
        return int(c.execute(q, args).fetchone()[0])

    day_rows = {
        str(r["d"]): dict(r)
        for r in c.execute(
            """SELECT substr(created_at,1,10) AS d,
                      COUNT(*) AS msgs,
                      COUNT(DISTINCT visitor_id) AS visitors,
                      SUM(CASE WHEN direction='customer' THEN 1 ELSE 0 END) AS customer
               FROM messages WHERE created_at>=? GROUP BY d""",
            (since,),
        ).fetchall()
    }
    sess_rows = {
        str(r["d"]): int(r["n"])
        for r in c.execute(
            "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM sessions WHERE created_at>=? GROUP BY d",
            (since,),
        ).fetchall()
    }
    daily = []
    for i in range(13, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        row = day_rows.get(day) or {}
        daily.append({
            "date": day,
            "msgs": int(row.get("msgs") or 0),
            "visitors": int(row.get("visitors") or 0),
            "customer": int(row.get("customer") or 0),
            "sessions": sess_rows.get(day, 0),
        })

    tp, yp = today + "%", yday + "%"
    return {
        "visitors": n("SELECT COUNT(*) FROM visitors"),
        "sessions": n("SELECT COUNT(*) FROM sessions"),
        "messages": n("SELECT COUNT(*) FROM messages"),
        "customer_msgs": n("SELECT COUNT(*) FROM messages WHERE direction='customer'"),
        "assistant_msgs": n("SELECT COUNT(*) FROM messages WHERE direction='assistant'"),
        "today_msgs": n("SELECT COUNT(*) FROM messages WHERE created_at LIKE ?", (tp,)),
        "today_visitors": n("SELECT COUNT(DISTINCT visitor_id) FROM messages WHERE created_at LIKE ?", (tp,)),
        "today_customer": n("SELECT COUNT(*) FROM messages WHERE direction='customer' AND created_at LIKE ?", (tp,)),
        "today_sessions": n("SELECT COUNT(*) FROM sessions WHERE created_at LIKE ?", (tp,)),
        "yesterday_msgs": n("SELECT COUNT(*) FROM messages WHERE created_at LIKE ?", (yp,)),
        "yesterday_visitors": n("SELECT COUNT(DISTINCT visitor_id) FROM messages WHERE created_at LIKE ?", (yp,)),
        "attachments": n("SELECT COUNT(*) FROM messages WHERE msg_type!='text'"),
        "unread_customer": unread_customer(),
        "daily": daily,
        "top_keywords": _top_keywords(),
    }


BRAND_DIR = _UPLOAD_ROOT / "uploads" / "brand"
OPS_DIR = _UPLOAD_ROOT / "uploads" / "ops"
VISITOR_AVATAR_DIR = _UPLOAD_ROOT / "uploads" / "visitors"
CHAT_FILE_DIR = _UPLOAD_ROOT / "uploads" / "chat"
MAX_VISITOR_AVATARS = 24
_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_CHAT_FILE_EXTS = _AVATAR_EXTS | {
    ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx",
    ".mp3", ".wav", ".m4a", ".webm", ".ogg", ".aac",
}


def get_setting(key: str, default: str = "") -> str:
    row = _conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row and row[0] is not None else default


def set_setting(key: str, value: str) -> None:
    _conn().execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    _conn().commit()


def logo_file() -> Path | None:
    return _logo_in(BRAND_DIR)


def ops_logo_file() -> Path | None:
    return _logo_in(OPS_DIR)


def _logo_in(folder: Path) -> Path | None:
    if not folder.exists():
        return None
    for p in sorted(folder.glob("logo.*")):
        if p.is_file():
            return p
    return None


def ops_config() -> dict:
    name = (get_setting("admin_name", "运营中台") or "运营中台").strip()[:32]
    logo = ops_logo_file()
    return {
        "name": name or "运营中台",
        "logo_url": f"/admin/api/ops-logo?v={int(logo.stat().st_mtime)}" if logo else "",
    }


def visitor_avatar_path(name: str | None) -> Path | None:
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        return None
    folder = VISITOR_AVATAR_DIR.resolve()
    path = (VISITOR_AVATAR_DIR / raw).resolve()
    if path.parent != folder or not path.is_file():
        return None
    if path.suffix.lower() not in _AVATAR_EXTS:
        return None
    return path


def visitor_avatar_url(name: str | None) -> str:
    path = visitor_avatar_path(name)
    if not path:
        return ""
    return f"/api/visitor-avatar/{path.name}?v={int(path.stat().st_mtime)}"


def list_visitor_avatars() -> list[dict]:
    if not VISITOR_AVATAR_DIR.exists():
        return []
    out = []
    for p in sorted(VISITOR_AVATAR_DIR.iterdir()):
        if visitor_avatar_path(p.name):
            out.append({"name": p.name, "url": visitor_avatar_url(p.name)})
    return out


def add_visitor_avatar(data: bytes, ext: str) -> dict:
    if ext.lower() not in _AVATAR_EXTS:
        raise ValueError("不支持的图片类型")
    VISITOR_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    if len(list_visitor_avatars()) >= MAX_VISITOR_AVATARS:
        raise ValueError(f"最多 {MAX_VISITOR_AVATARS} 张客户头像")
    name = secrets.token_hex(8) + ext.lower()
    path = VISITOR_AVATAR_DIR / name
    path.write_bytes(data)
    return {"name": name, "url": visitor_avatar_url(name)}


def delete_visitor_avatar(name: str) -> None:
    path = visitor_avatar_path(name)
    if path:
        path.unlink(missing_ok=True)


def save_chat_file(data: bytes, ext: str) -> dict:
    ext = (ext or "").lower()
    if ext not in _CHAT_FILE_EXTS:
        raise ValueError("不支持的文件类型")
    CHAT_FILE_DIR.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ext
    path = CHAT_FILE_DIR / name
    path.write_bytes(data)
    return {"name": name, "url": f"/api/chat-file/{name}"}


def chat_file_path(name: str) -> Path | None:
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        return None
    folder = CHAT_FILE_DIR.resolve()
    path = (CHAT_FILE_DIR / raw).resolve()
    if not CHAT_FILE_DIR.exists() or path.parent != folder or not path.is_file():
        return None
    if path.suffix.lower() not in _CHAT_FILE_EXTS:
        return None
    return path


def _pick_visitor_avatar() -> str:
    names = [x["name"] for x in list_visitor_avatars()]
    return secrets.choice(names) if names else ""


DEFAULT_CHAT_BG = "#f2f2f7"


def normalize_hex(value: str | None, default: str = DEFAULT_CHAT_BG) -> str | None:
    s = (value or "").strip().lower()
    if len(s) == 4 and s[0] == "#" and all(c in "0123456789abcdef" for c in s[1:]):
        s = "#" + "".join(c * 2 for c in s[1:])
    if len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdef" for c in s[1:]):
        return s
    return default if value is None or str(value).strip() == "" else None


def widget_config(visitor_id: str | None = None) -> dict:
    brand = default_brand()
    name = (brand.get("name") or get_setting("name", "客服小美") or "客服小美").strip()[:32]
    logo = logo_file()
    locked = bool(brand.get("online_lock", True))
    qs = _guide_qs()
    avatar_url = ""
    if visitor_id:
        row = _conn().execute("SELECT avatar FROM visitors WHERE visitor_id=?", (visitor_id,)).fetchone()
        avatar_url = visitor_avatar_url(row["avatar"] if row else "")
    return {
        "name": name or "客服小美",
        "brand_id": brand.get("id"),
        "online_lock": locked,
        "online": True if locked else bool(brand.get("online", True)),
        "logo_url": f"/api/widget/logo?v={int(logo.stat().st_mtime)}" if logo else "",
        "chat_bg": normalize_hex(brand.get("chat_bg") or get_setting("chat_bg", DEFAULT_CHAT_BG), DEFAULT_CHAT_BG) or DEFAULT_CHAT_BG,
        "allow_image": bool(brand.get("allow_image", True)),
        "allow_file": bool(brand.get("allow_file", True)),
        "allow_voice": bool(brand.get("allow_voice", True)),
        "guide_text": brand.get("welcome") or get_setting("guide_text", ""),
        "guide_qs": qs,
        "handoff_keywords": brand.get("handoff_keywords") or "转人工,人工客服,找人工",
        "visitor_avatar_url": avatar_url,
    }


def _guide_qs() -> list:
    rows = [
        dict(r)
        for r in _conn().execute(
            "SELECT title, content FROM canned_replies ORDER BY id ASC LIMIT 8"
        ).fetchall()
    ]
    if rows:
        return [
            {
                "label": str(r.get("title") or "")[:16],
                "q": str(r.get("title") or "")[:80],
                "a": str(r.get("content") or "")[:400],
            }
            for r in rows
            if str(r.get("title") or "").strip()
        ]
    return default_brand().get("guide_qs") or []


def match_guide_answer(text: str) -> str:
    needle = (text or "").strip()
    if not needle:
        return ""
    for item in _guide_qs():
        answer = (item.get("a") or "").strip()
        if not answer:
            continue
        if needle == (item.get("q") or "").strip() or needle == (item.get("label") or "").strip():
            return answer
    return ""


def default_brand() -> dict:
    row = _conn().execute("SELECT * FROM brands WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    if not row:
        row = _conn().execute("SELECT * FROM brands ORDER BY id LIMIT 1").fetchone()
    if not row:
        return {
            "id": None, "name": get_setting("name", "客服小美"), "welcome": get_setting("guide_text", ""),
            "guide_qs": _parse_qs(get_setting("guide_qs", "[]")), "allow_image": True, "allow_file": True,
            "allow_voice": True, "chat_bg": get_setting("chat_bg", DEFAULT_CHAT_BG),
            "online": get_setting("online", "1") != "0", "online_lock": get_setting("online_lock", "1") != "0",
            "handoff_keywords": "转人工,人工客服,找人工",
        }
    d = dict(row)
    d["allow_image"] = int(d.get("allow_image") or 0) != 0
    d["allow_file"] = int(d.get("allow_file") or 0) != 0
    d["allow_voice"] = int(d.get("allow_voice") or 0) != 0
    d["online"] = int(d.get("online") or 0) != 0
    d["online_lock"] = int(d.get("online_lock") or 0) != 0
    d["guide_qs"] = _parse_qs(d.get("guide_qs") or "[]")
    return d


def _parse_qs(raw: str) -> list:
    try:
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            out = []
            for item in data[:8]:
                if isinstance(item, dict) and item.get("q"):
                    out.append({"label": str(item.get("label") or item["q"])[:16], "q": str(item["q"])[:80]})
            return out
    except Exception:
        pass
    return []


def is_locked_online() -> bool:
    return bool(default_brand().get("online_lock"))


def is_online() -> bool:
    b = default_brand()
    if b.get("online_lock"):
        return True
    return bool(b.get("online"))


def get_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    row = _conn().execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    vis = _conn().execute(
        "SELECT avatar FROM visitors WHERE visitor_id=?", (d.get("visitor_id") or "",)
    ).fetchone()
    d["visitor_avatar_url"] = visitor_avatar_url(vis["avatar"] if vis else "")
    return d


def set_session_fields(session_id: str, **fields) -> None:
    if not session_id or not fields:
        return
    allowed = {"status", "assignee_id", "skill_group_id", "brand_id", "channel", "visitor_id", "workspace_id"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return
    args.append(session_id)
    _conn().execute("UPDATE sessions SET " + ",".join(sets) + " WHERE session_id=?", args)
    _conn().commit()


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 120000)
    return salt.hex() + "$" + dk.hex()


def verify_password(plain: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt_hex), 120000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_admin_session(token_hash: str, staff_id: int | None) -> None:
    exp = int(time.time()) + 86400
    _conn().execute(
        "INSERT INTO admin_sessions(token_hash, staff_id, created_at, expires_at) VALUES(?,?,?,?)",
        (token_hash, staff_id, _now(), exp),
    )
    _conn().commit()


def get_admin_session(token_hash: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM admin_sessions WHERE token_hash=? AND expires_at>?",
        (token_hash, int(time.time())),
    ).fetchone()
    return dict(row) if row else None


def delete_admin_session(token_hash: str) -> None:
    _conn().execute("DELETE FROM admin_sessions WHERE token_hash=?", (token_hash,))
    _conn().commit()


def get_staff(staff_id: int) -> dict | None:
    row = _conn().execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
    return dict(row) if row else None


def get_staff_by_login(login_name: str) -> dict | None:
    row = _conn().execute("SELECT * FROM staff WHERE login_name=?", (login_name,)).fetchone()
    return dict(row) if row else None


def find_staff_login(username: str) -> dict | None:
    name = (username or "").strip()
    if not name:
        return None
    row = get_staff_by_login(name)
    if row:
        return row
    rows = _conn().execute("SELECT * FROM staff WHERE name=?", (name,)).fetchall()
    if len(rows) == 1:
        return dict(rows[0])
    return None


def list_staff() -> list[dict]:
    rows = _conn().execute(
        """SELECT s.*, g.name AS skill_group_name,
           (SELECT COUNT(*) FROM sessions x WHERE x.assignee_id=s.id AND x.status='assigned') AS open_chats
           FROM staff s LEFT JOIN skill_groups g ON g.id=s.skill_group_id ORDER BY s.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def create_staff(login_name: str, name: str, password: str, role: str = "agent",
                 skill_group_id: int | None = None, max_chats: int = 8) -> dict:
    cur = _conn().execute(
        """INSERT INTO staff(workspace_id, login_name, name, password_hash, role, skill_group_id, max_chats, presence, enabled, created_at)
           VALUES(1,?,?,?,?,?,?, 'offline', 1, ?)""",
        (login_name, name, hash_password(password), role, skill_group_id, max_chats, _now()),
    )
    _conn().commit()
    return get_staff(int(cur.lastrowid)) or {}


def update_staff(staff_id: int, **fields) -> None:
    allowed = {"name", "role", "skill_group_id", "max_chats", "enabled", "presence", "password_hash"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return
    args.append(staff_id)
    _conn().execute("UPDATE staff SET " + ",".join(sets) + " WHERE id=?", args)
    _conn().commit()


def delete_staff(staff_id: int) -> None:
    _conn().execute("DELETE FROM staff WHERE id=?", (staff_id,))
    _conn().commit()


def public_staff(row: dict | None) -> dict | None:
    if not row:
        return None
    return {k: row[k] for k in row if k != "password_hash"}


def list_skill_groups() -> list[dict]:
    return [dict(r) for r in _conn().execute("SELECT * FROM skill_groups ORDER BY id").fetchall()]


def create_skill_group(name: str) -> dict:
    cur = _conn().execute("INSERT INTO skill_groups(workspace_id, name) VALUES(1, ?)", (name,))
    _conn().commit()
    row = _conn().execute("SELECT * FROM skill_groups WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_skill_group(gid: int) -> None:
    if gid == 1:
        return
    _conn().execute("UPDATE staff SET skill_group_id=1 WHERE skill_group_id=?", (gid,))
    _conn().execute("DELETE FROM skill_groups WHERE id=?", (gid,))
    _conn().commit()


def list_brands() -> list[dict]:
    out = []
    for r in _conn().execute("SELECT * FROM brands ORDER BY is_default DESC, id").fetchall():
        d = dict(r)
        d["guide_qs"] = _parse_qs(d.get("guide_qs") or "[]")
        d["allow_image"] = int(d.get("allow_image") or 0) != 0
        d["allow_file"] = int(d.get("allow_file") or 0) != 0
        d["allow_voice"] = int(d.get("allow_voice") or 0) != 0
        d["online"] = int(d.get("online") or 0) != 0
        d["online_lock"] = int(d.get("online_lock") or 0) != 0
        out.append(d)
    return out


def save_brand(brand_id: int | None, data: dict) -> dict:
    if brand_id:
        existing = next((b for b in list_brands() if b["id"] == brand_id), None)
        if existing:
            merged = dict(existing)
            merged.update({k: v for k, v in data.items() if v is not None})
            data = merged
    qs = data.get("guide_qs")
    if isinstance(qs, list):
        qs = json.dumps(qs, ensure_ascii=False)
    elif qs is None:
        qs = "[]"
    fields = {
        "name": (data.get("name") or "客服").strip()[:32],
        "welcome": (data.get("welcome") or data.get("guide_text") or "")[:200],
        "guide_qs": qs or "[]",
        "allow_image": 1 if data.get("allow_image", True) else 0,
        "allow_file": 1 if data.get("allow_file", True) else 0,
        "allow_voice": 1 if data.get("allow_voice", True) else 0,
        "chat_bg": normalize_hex(data.get("chat_bg"), DEFAULT_CHAT_BG) or DEFAULT_CHAT_BG,
        "online": 1 if data.get("online", True) else 0,
        "online_lock": 1 if data.get("online_lock", True) else 0,
        "handoff_keywords": (data.get("handoff_keywords") or "转人工,人工客服,找人工")[:120],
    }
    if data.get("online_lock"):
        fields.pop("online", None)
    conn = _conn()
    if brand_id:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute("UPDATE brands SET " + sets + " WHERE id=?", [*fields.values(), brand_id])
        if data.get("is_default"):
            conn.execute("UPDATE brands SET is_default=0")
            conn.execute("UPDATE brands SET is_default=1 WHERE id=?", (brand_id,))
        conn.commit()
        set_setting("name", fields["name"])
        if "online" in fields:
            set_setting("online", str(fields["online"]))
        set_setting("online_lock", str(fields.get("online_lock", 1)))
        set_setting("chat_bg", fields["chat_bg"])
        return [b for b in list_brands() if b["id"] == brand_id][0]
    conn.execute("UPDATE brands SET is_default=0")
    cur = conn.execute(
        """INSERT INTO brands(workspace_id, name, welcome, guide_qs, allow_image, allow_file, allow_voice,
           chat_bg, online, online_lock, is_default, handoff_keywords)
           VALUES(1,?,?,?,?,?,?,?,?,?,1,?)""",
        (fields["name"], fields["welcome"], fields["guide_qs"], fields["allow_image"], fields["allow_file"],
         fields["allow_voice"], fields["chat_bg"], fields.get("online", 1), fields["online_lock"], fields["handoff_keywords"]),
    )
    conn.commit()
    return [b for b in list_brands() if b["id"] == cur.lastrowid][0]


def desk_conversations(queue: str, staff_id: int | None, limit: int = 50) -> list[dict]:
    q = """SELECT s.*, v.note AS visitor_note, v.avatar AS visitor_avatar,
           (SELECT content FROM messages m WHERE m.session_id=s.session_id ORDER BY id DESC LIMIT 1) AS last_content,
           st.name AS assignee_name
           FROM sessions s
           LEFT JOIN visitors v ON v.visitor_id=s.visitor_id
           LEFT JOIN staff st ON st.id=s.assignee_id WHERE 1=1"""
    args: list = []
    if queue == "queued":
        q += " AND s.status='queued'"
    elif queue == "mine" and staff_id:
        q += " AND s.status='assigned' AND s.assignee_id=?"
        args.append(staff_id)
    elif queue == "bot":
        q += " AND s.status IN ('bot','active')"
    elif queue == "resolved":
        q += " AND s.status='resolved'"
    q += " ORDER BY s.last_msg_at DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in _conn().execute(q, args).fetchall():
        d = dict(r)
        d["visitor_avatar_url"] = visitor_avatar_url(d.get("visitor_avatar"))
        out.append(d)
    return out


def list_canned() -> list[dict]:
    return [dict(r) for r in _conn().execute("SELECT * FROM canned_replies ORDER BY id DESC").fetchall()]


def add_canned(title: str, content: str, group_name: str = "") -> dict:
    cur = _conn().execute(
        "INSERT INTO canned_replies(workspace_id, title, content, group_name) VALUES(1,?,?,?)",
        (title, content, group_name),
    )
    _conn().commit()
    row = _conn().execute("SELECT * FROM canned_replies WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_canned(cid: int) -> None:
    _conn().execute("DELETE FROM canned_replies WHERE id=?", (cid,))
    _conn().commit()


def add_csat(session_id: str, visitor_id: str, score: int, comment: str = "") -> None:
    _conn().execute(
        "INSERT INTO csat(session_id, visitor_id, score, comment, created_at) VALUES(?,?,?,?,?)",
        (session_id, visitor_id, int(score), comment[:200], _now()),
    )
    _conn().commit()


def csat_stats() -> dict:
    row = _conn().execute("SELECT COUNT(*) AS n, AVG(score) AS avg FROM csat").fetchone()
    n = int(row["n"] or 0)
    dist = {str(i): 0 for i in range(1, 6)}
    for r in _conn().execute("SELECT score, COUNT(*) AS c FROM csat GROUP BY score"):
        dist[str(int(r["score"]))] = int(r["c"])
    return {"count": n, "avg": round(float(row["avg"] or 0), 2), "dist": dist}


def agent_report() -> list[dict]:
    rows = _conn().execute(
        """SELECT s.id, s.name, s.presence,
           (SELECT COUNT(*) FROM sessions x WHERE x.assignee_id=s.id) AS assigned_total,
           (SELECT COUNT(*) FROM sessions x WHERE x.assignee_id=s.id AND x.status='assigned') AS open_now,
           (SELECT COUNT(*) FROM messages m WHERE m.staff_id=s.id) AS replies
           FROM staff s ORDER BY s.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def messages_since(session_id: str, after_id: int = 0, limit: int = 100) -> list[dict]:
    if not session_id:
        return []
    return [
        dict(r)
        for r in _conn().execute(
            "SELECT * FROM messages WHERE session_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (session_id, int(after_id or 0), limit),
        ).fetchall()
    ]


def patch_default_brand(data: dict) -> dict:
    brand = default_brand()
    merged = dict(brand)
    merged.update(data)
    return save_brand(brand.get("id"), merged)


def last_assignee_for_visitor(visitor_id: str) -> int | None:
    row = _conn().execute(
        "SELECT assignee_id FROM sessions WHERE visitor_id=? AND assignee_id IS NOT NULL ORDER BY last_msg_at DESC LIMIT 1",
        (visitor_id,),
    ).fetchone()
    if row and row["assignee_id"]:
        return int(row["assignee_id"])
    return None


def available_agents(skill_group_id: int | None = None) -> list[dict]:
    q = """SELECT s.*, 
           (SELECT COUNT(*) FROM sessions x WHERE x.assignee_id=s.id AND x.status='assigned') AS open_chats
           FROM staff s WHERE s.enabled=1 AND s.presence='online'"""
    args: list = []
    if skill_group_id:
        q += " AND (s.skill_group_id=? OR s.skill_group_id IS NULL)"
        args.append(skill_group_id)
    rows = [dict(r) for r in _conn().execute(q, args).fetchall()]
    return [r for r in rows if int(r.get("open_chats") or 0) < int(r.get("max_chats") or 8)]


def within_business_hours() -> bool:
    raw = get_setting("business_hours", "")
    try:
        conf = json.loads(raw) if raw else {}
    except Exception:
        return True
    if not conf.get("enabled"):
        return True
    now = time.localtime()
    days = conf.get("days") or [1, 2, 3, 4, 5]
    # Monday=0 in Python tm_wday; conf uses 1=Mon ... 7=Sun
    py = now.tm_wday + 1
    if py not in days:
        return False
    start = str(conf.get("start") or "09:00")
    end = str(conf.get("end") or "18:00")
    cur = f"{now.tm_hour:02d}:{now.tm_min:02d}"
    return start <= cur <= end
