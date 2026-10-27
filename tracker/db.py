"""Хранилище: Postgres (Vercel/Neon) при заданном DATABASE_URL, иначе локальный SQLite.

Весь SQL ниже написан с плейсхолдерами `?` и общим для обоих движков диапазоном синтаксиса;
для Postgres `?` превращается в `%s`. Даты хранятся текстом 'YYYY-MM-DD HH:MM' (местное время)."""
import sqlite3
from datetime import datetime

from . import config
from .config import DATA

DATABASE_URL = config.DATABASE_URL

SQLITE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        msg_id INTEGER UNIQUE,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        subject TEXT,
        deadline TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        manual_deadline INTEGER NOT NULL DEFAULT 0,
        reminded INTEGER NOT NULL DEFAULT 0,
        link TEXT,
        posted_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS messages(
        msg_id INTEGER PRIMARY KEY,
        posted_at TEXT,
        sender TEXT,
        text TEXT NOT NULL,
        link TEXT
    )""",
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",
]

PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS tasks(
        id BIGSERIAL PRIMARY KEY,
        msg_id BIGINT UNIQUE,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        subject TEXT,
        deadline TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        manual_deadline INTEGER NOT NULL DEFAULT 0,
        reminded INTEGER NOT NULL DEFAULT 0,
        link TEXT,
        posted_at TEXT,
        created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI')
    )""",
    """CREATE TABLE IF NOT EXISTS messages(
        msg_id BIGINT PRIMARY KEY,
        posted_at TEXT,
        sender TEXT,
        text TEXT NOT NULL,
        link TEXT
    )""",
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",
]

ISO = "%Y-%m-%d %H:%M"


class DB:
    """Тонкая обёртка: одинаковые execute / `with con:` (коммит) / close для SQLite и Postgres."""

    def __init__(self, raw, pg):
        self.raw, self.pg = raw, pg

    def execute(self, sql, args=()):
        if self.pg:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, args)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # в отличие от psycopg, `with` здесь соединение НЕ закрывает
        self.raw.rollback() if exc_type else self.raw.commit()

    def close(self):
        try:
            self.raw.rollback()
        finally:
            self.raw.close()


def open_sqlite(path=None):
    DATA.mkdir(exist_ok=True)
    raw = sqlite3.connect(path or DATA / "tracker.db")
    raw.row_factory = sqlite3.Row
    con = DB(raw, False)
    for stmt in SQLITE_SCHEMA:
        con.execute(stmt)
    raw.commit()
    return con


def open_pg(url):
    import psycopg  # нужен только при работе с Postgres
    from psycopg.rows import dict_row

    # prepare_threshold=None: Neon отдаёт соединение через PgBouncer, ему серверные prepared statements ни к чему
    con = DB(psycopg.connect(url, row_factory=dict_row, prepare_threshold=None), True)
    for stmt in PG_SCHEMA:
        con.execute(stmt)
    con.raw.commit()
    return con


def connect():
    return open_pg(DATABASE_URL) if DATABASE_URL else open_sqlite()


def iso(dt):
    return dt.strftime(ISO) if dt else None


def get_meta(con, key):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_meta(con, key, value):
    with con:
        con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def save_message(con, msg_id, posted_at, sender, text, link):
    with con:
        con.execute("INSERT INTO messages(msg_id,posted_at,sender,text,link) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(msg_id) DO UPDATE SET text=excluded.text",
                    (msg_id, iso(posted_at), sender, text, link))


def upsert_task(con, msg_id, title, body, subject, deadline, link, posted_at):
    """Вставляет задание из сообщения или обновляет после правки. True, если задание новое."""
    dl = iso(deadline)
    row = con.execute("SELECT id FROM tasks WHERE msg_id=?", (msg_id,)).fetchone()
    with con:
        if row is None:
            con.execute("INSERT INTO tasks(msg_id,title,body,subject,deadline,link,posted_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (msg_id, title, body, subject, dl, link, iso(posted_at)))
            return True
        # ручной дедлайн правка сообщения не затирает
        con.execute(
            "UPDATE tasks SET title=?, body=?, subject=COALESCE(subject,?), link=?, "
            "reminded=CASE WHEN manual_deadline=0 AND COALESCE(deadline,'')<>COALESCE(?,'') "
            "THEN 0 ELSE reminded END, "
            "deadline=CASE WHEN manual_deadline=1 THEN deadline ELSE ? END WHERE id=?",
            (title, body, subject, link, dl, dl, row["id"]))
        return False


def add_manual(con, title, deadline, subject):
    with con:
        row = con.execute(
            "INSERT INTO tasks(title,subject,deadline,manual_deadline) VALUES(?,?,?,?) RETURNING id",
            (title, subject, iso(deadline), 1 if deadline else 0)).fetchone()
    return row["id"]


def get_task(con, task_id):
    return con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def get_task_by_msg(con, msg_id):
    return con.execute("SELECT * FROM tasks WHERE msg_id=?", (msg_id,)).fetchone()


def list_tasks(con, include_done=False, subject=None, until=None):
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    if not include_done:
        sql += " AND status<>'done'"
    if until:
        sql += " AND deadline IS NOT NULL AND deadline<=?"
        args.append(iso(until))
    sql += " ORDER BY deadline IS NULL, deadline, id"
    rows = con.execute(sql, args).fetchall()
    if subject:  # регистр кириллицы SQL-движки трактуют по-разному — фильтруем в Python
        rows = [r for r in rows if subject.lower() in (r["subject"] or "").lower()]
    return rows


def update(con, task_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with con:
        cur = con.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))
    return cur.rowcount > 0


def update_by_msg(con, msg_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with con:
        return con.execute(f"UPDATE tasks SET {cols} WHERE msg_id=?", (*fields.values(), msg_id)).rowcount


def find_messages(con, query, limit=20):
    q = query.lower()
    rows = con.execute("SELECT * FROM messages ORDER BY msg_id DESC").fetchall()
    return [r for r in rows if q in r["text"].lower()][:limit]


def delete(con, task_id):
    with con:
        return con.execute("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount > 0


def get_message(con, msg_id):
    return con.execute("SELECT * FROM messages WHERE msg_id=?", (msg_id,)).fetchone()


def due_reminders(con, now=None):
    """Открытые задания, по которым пора напомнить: (row, новая маска reminded)."""
    now = now or config.now()
    out = []
    for t in list_tasks(con):
        if not t["deadline"]:
            continue
        hours = (datetime.strptime(t["deadline"], ISO) - now).total_seconds() / 3600
        if hours <= 0:
            continue
        if hours <= 3 and not t["reminded"] & 2:
            out.append((t, 3))
        elif hours <= 24 and not t["reminded"] & 1:
            out.append((t, 1))
    return out


def copy_all(src, dst):
    """Переносит данные из одной базы в другую с теми же id (SQLite -> Postgres). Повторный запуск
    безопасен: существующие записи обновляются. Возвращает число строк по таблицам."""
    counts = {}
    with dst:
        for t in src.execute("SELECT * FROM tasks ORDER BY id").fetchall():
            dst.execute(
                "INSERT INTO tasks(id,msg_id,title,body,subject,deadline,status,manual_deadline,"
                "reminded,link,posted_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET msg_id=excluded.msg_id, title=excluded.title, "
                "body=excluded.body, subject=excluded.subject, deadline=excluded.deadline, "
                "status=excluded.status, manual_deadline=excluded.manual_deadline, "
                "reminded=excluded.reminded, link=excluded.link, posted_at=excluded.posted_at",
                tuple(t[k] for k in ("id", "msg_id", "title", "body", "subject", "deadline", "status",
                                     "manual_deadline", "reminded", "link", "posted_at", "created_at")))
            counts["tasks"] = counts.get("tasks", 0) + 1
        for m in src.execute("SELECT * FROM messages ORDER BY msg_id").fetchall():
            dst.execute("INSERT INTO messages(msg_id,posted_at,sender,text,link) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(msg_id) DO UPDATE SET text=excluded.text",
                        tuple(m[k] for k in ("msg_id", "posted_at", "sender", "text", "link")))
            counts["messages"] = counts.get("messages", 0) + 1
        for r in src.execute("SELECT * FROM meta").fetchall():
            dst.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (r["key"], r["value"]))
            counts["meta"] = counts.get("meta", 0) + 1
        if dst.pg:  # после вставки с явными id сдвигаем счётчик, иначе следующий INSERT упрётся в дубль
            dst.execute("SELECT setval(pg_get_serial_sequence('tasks','id'), "
                        "COALESCE((SELECT MAX(id) FROM tasks), 1))")
    return counts
