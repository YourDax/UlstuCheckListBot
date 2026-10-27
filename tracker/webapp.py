"""Mini App: проверка подписи Telegram и JSON-API для интерфейса (webapp/index.html).

Доступ только у владельца: каждый запрос несёт initData от Telegram, подписанный токеном бота."""
import hashlib
import hmac
import json
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, parse_qsl

from . import config, db, subjects

INIT_MAX_AGE = 24 * 3600
STATUSES = ("new", "doing", "done")


def verify_init_data(init_data, token, owner_id, now=None, max_age=INIT_MAX_AGE):
    """id владельца, если подпись Telegram верна, данные свежие и это именно владелец; иначе None."""
    if not init_data or not token or not owner_id:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want.encode(), got.encode()):
        return None
    try:
        if (now or time.time()) - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None
    return user.get("id") if user.get("id") == owner_id else None


def display_title(t):
    """Для сообщений из одного файла «[файл: Лабораторная работа 1.pdf]» — имя файла без расширения."""
    m = re.fullmatch(r"\[файл:\s*(.+?)\]", t["title"].strip())
    return re.sub(r"\.\w{1,5}$", "", m[1]) if m else t["title"]


def task_json(t, now):
    d = datetime.strptime(t["deadline"], db.ISO) if t["deadline"] else None
    return {
        "id": t["id"], "title": display_title(t), "subject": t["subject"], "status": t["status"],
        "deadline": t["deadline"], "days": (d.date() - now.date()).days if d else None,
        "overdue": bool(d and d < now and t["status"] != "done"),
        "body": t["body"] if t["body"].strip() != t["title"].strip() else "",
        "link": t["link"],
    }


def _subjects(con):
    names = list(subjects.load())
    for row in con.execute("SELECT DISTINCT subject FROM tasks WHERE subject IS NOT NULL").fetchall():
        if row["subject"] not in names:
            names.append(row["subject"])
    return names


def _parse_deadline(value):
    """'' / None — убрать срок; 'YYYY-MM-DD' (23:59) или 'YYYY-MM-DDTHH:MM'. ValueError при мусоре."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if len(value) <= 10:
        dt = dt.replace(hour=23, minute=59)
    return dt.replace(second=0, microsecond=0, tzinfo=None)


def _clean(s, limit):
    return " ".join(str(s).split())[:limit]


def handle(con, method, path, query, body):
    """Маршруты /api/app/*. Возвращает (код, json-словарь)."""
    now = config.now()
    m = re.fullmatch(r"/api/app/tasks(?:/(\d+))?", path)
    if not m:
        return 404, {"error": "not found"}
    tid = int(m[1]) if m[1] else None
    try:
        data = json.loads(body) if body else {}
    except ValueError:
        return 400, {"error": "bad json"}

    if method == "GET" and tid is None:
        include_done = parse_qs(query).get("all", ["0"])[0] == "1"
        return 200, {"now": now.strftime(db.ISO), "subjects": _subjects(con),
                     "tasks": [task_json(t, now) for t in db.list_tasks(con, include_done=include_done)]}

    if method == "POST" and tid is None:  # создать
        title = _clean(data.get("title", ""), 200)
        if not title:
            return 400, {"error": "Нужно название"}
        try:
            deadline = _parse_deadline(data.get("deadline"))
        except ValueError:
            return 400, {"error": "Не понял дату"}
        new_id = db.add_manual(con, title, deadline, _clean(data.get("subject") or "", 80) or None)
        return 200, {"task": task_json(db.get_task(con, new_id), now)}

    if tid is None:
        return 405, {"error": "method"}
    task = db.get_task(con, tid)
    if not task:
        return 404, {"error": "Задания нет"}

    if method == "DELETE":
        db.delete(con, tid)
        return 200, {"ok": True}
    if method == "POST":  # изменить: любые из status / deadline / subject / title
        fields = {}
        if "status" in data:
            if data["status"] not in STATUSES:
                return 400, {"error": "status"}
            fields["status"] = data["status"]
        if "deadline" in data:
            try:
                dl = _parse_deadline(data["deadline"])
            except ValueError:
                return 400, {"error": "Не понял дату"}
            fields.update(deadline=db.iso(dl), manual_deadline=1, reminded=0)
        if "subject" in data:
            fields["subject"] = _clean(data["subject"] or "", 80) or None
        if "title" in data:
            title = _clean(data["title"], 200)
            if not title:
                return 400, {"error": "Нужно название"}
            fields["title"] = title
        if fields:
            db.update(con, tid, **fields)
        return 200, {"task": task_json(db.get_task(con, tid), now)}
    return 405, {"error": "method"}
