import argparse
import asyncio
import sys
from contextlib import closing
from datetime import datetime, timedelta

from . import config, db, parse, subjects, view


def _deadline_arg(s):
    if s in ("-", "нет", "none"):
        return None
    dt = parse.find_deadline(s, config.now(), explicit=True)
    if not dt:
        raise SystemExit(f"Не понял дату «{s}». Примеры: 15.11, 15.11 18:00, завтра, пятница 12:00.")
    return dt


def _need(con, task_id):
    t = db.get_task(con, task_id)
    if not t:
        raise SystemExit(f"Задания #{task_id} нет.")
    return t


async def _with_client(fn):
    from . import tg
    c = tg.client()
    await c.start()  # при первом запуске спросит телефон и код — вводите сами
    try:
        return await fn(c)
    finally:
        await c.disconnect()


def cmd_login(a):
    async def run(c):
        me = await c.get_me()
        print(f"Вход выполнен: {me.first_name} (id {me.id}). Сессия сохранена в data/.")
    asyncio.run(_with_client(run))


def cmd_groups(a):
    async def run(c):
        async for d in c.iter_dialogs():
            if d.is_group or d.is_channel:
                print(f"{d.id}\t{d.name}")
    asyncio.run(_with_client(run))


def cmd_migrate(a):
    """Переносит data/tracker.db в Postgres (DATABASE_URL). Повторный запуск безопасен."""
    if not config.DATABASE_URL:
        raise SystemExit("Задайте DATABASE_URL (строка подключения Postgres из Vercel/Neon) в .env или окружении.")
    path = config.DATA / "tracker.db"
    if not path.exists():
        raise SystemExit(f"Нет локальной базы: {path}")
    src, dst = db.open_sqlite(path), db.open_pg(config.DATABASE_URL)
    try:
        counts = db.copy_all(src, dst)
    finally:
        src.close()
        dst.close()
    print("Перенесено: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))


COMMANDS_MENU = [
    ("app", "Открыть приложение"), ("list", "Открытые задания"), ("soon", "Ближайшие дедлайны"), ("all", "Все, с выполненными"),
    ("add", "Добавить задание"), ("find", "Поиск по сообщениям группы"), ("digest", "Сводка сейчас"),
    ("subjects", "Проставить предметы"), ("help", "Справка"),
]


def cmd_webhook(a):
    from .bot import Api, ApiError

    if not config.BOT_TOKEN:
        raise SystemExit("Задайте BOT_TOKEN (токен от @BotFather) в .env или окружении.")
    api = Api(config.BOT_TOKEN)
    try:
        if a.action == "set":
            if not a.url or not config.WEBHOOK_SECRET:
                raise SystemExit("Нужны URL (https://<проект>.vercel.app/api/webhook) и WEBHOOK_SECRET.")
            api.call("setWebhook", url=a.url, secret_token=config.WEBHOOK_SECRET,
                     allowed_updates=["message", "callback_query"], drop_pending_updates=True)
            api.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS_MENU])
            api.call("setChatMenuButton", menu_button={
                "type": "web_app", "text": "Мои лабы", "web_app": {"url": config.APP_URL}})
            print("Webhook установлен, меню команд обновлено.")
        elif a.action == "delete":
            api.call("deleteWebhook", drop_pending_updates=True)
            print("Webhook удалён.")
        info = api.call("getWebhookInfo")
    except ApiError as e:
        raise SystemExit(f"Telegram ответил ошибкой: {e}")
    print(f"url: {info.get('url') or '—'} · в очереди: {info.get('pending_update_count', 0)}")
    if info.get("last_error_message"):
        print(f"последняя ошибка доставки: {info['last_error_message']}")


def cmd_import(a):
    from .importer import import_export
    with closing(db.connect()) as con:
        total, new, _, archived = import_export(con, a.path, a.chat, not a.keep_overdue)
    print(f"Сообщений: {total}, заданий найдено: {new}, из них в архиве (дедлайн прошёл): {archived}")
    print("Открытые: python -m tracker list   |   с архивом: python -m tracker list --all")


def cmd_find(a):
    """Поиск по всем сохранённым сообщениям — чтобы найти то, что не опознано как задание."""
    with closing(db.connect()) as con:
        rows = db.find_messages(con, " ".join(a.query))
        for r in rows:
            first = r["text"].replace("\n", " / ")
            print(f"сообщение {r['msg_id']} · {r['posted_at']} · {r['sender']}: {first[:140]}")
        print(f"Найдено: {len(rows)}. Сделать заданием: python -m tracker promote НОМЕР")


def cmd_sync(a):
    from . import tg

    async def run(c):
        entity = await tg.resolve_group(c)
        with closing(db.connect()) as con:
            n, new = await tg.sync(c, entity, con, a.limit)
            print(f"Обработано сообщений: {n}, новых заданий: {len(new)}")
    asyncio.run(_with_client(run))


def cmd_watch(a):
    from . import tg

    async def run(c):
        entity = await tg.resolve_group(c)
        with closing(db.connect()) as con:
            await tg.watch(c, entity, con)
    try:
        asyncio.run(_with_client(run))
    except KeyboardInterrupt:
        pass


def cmd_list(a):
    with closing(db.connect()) as con:
        print(view.table(db.list_tasks(con, a.all, a.subject)))


def cmd_soon(a):
    with closing(db.connect()) as con:
        print(view.table(db.list_tasks(con, until=config.now() + timedelta(days=a.days))))


def cmd_show(a):
    with closing(db.connect()) as con:
        print(view.card(_need(con, a.id)))


def _setter(status):
    def run(a):
        with closing(db.connect()) as con:
            _need(con, a.id)
            db.update(con, a.id, status=status)
            print(view.line(db.get_task(con, a.id)))
    return run


def cmd_add(a):
    with closing(db.connect()) as con:
        tid = db.add_manual(con, a.title, _deadline_arg(a.deadline) if a.deadline else None, a.subject)
        print(view.line(db.get_task(con, tid)))


def cmd_deadline(a):
    with closing(db.connect()) as con:
        _need(con, a.id)
        dl = _deadline_arg(" ".join(a.when))
        db.update(con, a.id, deadline=db.iso(dl), manual_deadline=1, reminded=0)
        print(view.line(db.get_task(con, a.id)))


def cmd_subject(a):
    with closing(db.connect()) as con:
        _need(con, a.id)
        db.update(con, a.id, subject=" ".join(a.name))
        print(view.line(db.get_task(con, a.id)))


def cmd_subjects(a):
    """Проставить предмет заданиям, у которых его нет, по subjects.json (вручную заданные не трогает)."""
    mapping = subjects.load()
    if not mapping:
        raise SystemExit(f"Нет {subjects.PATH.name} или он пуст.")
    with closing(db.connect()) as con:
        set_, left = 0, []
        for t in db.list_tasks(con, include_done=True):
            if t["subject"]:
                continue
            s = subjects.match(f"{t['title']}\n{t['body']}", mapping)
            if s:
                db.update(con, t["id"], subject=s)
                set_ += 1
            else:
                left.append(t)
        print(f"Предмет проставлен: {set_}, без предмета осталось: {len(left)}")
        for t in left:
            print("  " + view.line(t))


def cmd_promote(a):
    """Превратить сохранённое сообщение (не распознанное как задание) в задание."""
    with closing(db.connect()) as con:
        m = db.get_message(con, a.msg_id)
        if not m:
            raise SystemExit(f"Сообщения {a.msg_id} нет в базе.")
        posted = datetime.strptime(m["posted_at"], db.ISO)
        db.upsert_task(con, m["msg_id"], parse.make_title(m["text"]), m["text"],
                       subjects.guess(m["text"]), parse.find_deadline(m["text"], posted),
                       m["link"], posted)
        row = con.execute("SELECT * FROM tasks WHERE msg_id=?", (a.msg_id,)).fetchone()
        print(view.line(row))


def cmd_rm(a):
    with closing(db.connect()) as con:
        print("Удалено." if db.delete(con, a.id) else f"Задания #{a.id} нет.")


def build_parser():
    p = argparse.ArgumentParser(prog="tracker", description="Трекер заданий из Telegram-группы")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    add("migrate", cmd_migrate, "перенести локальную базу (data/tracker.db) в Postgres из DATABASE_URL")
    sp = add("webhook", cmd_webhook, "set|info|delete: webhook бота и меню команд")
    sp.add_argument("action", choices=["set", "info", "delete"])
    sp.add_argument("url", nargs="?")
    sp = add("import", cmd_import, "загрузить историю из экспорта Telegram Desktop (result.json)")
    sp.add_argument("path", help="result.json или папка экспорта")
    sp.add_argument("--chat", help="название чата, если в экспорте их несколько")
    sp.add_argument("--keep-overdue", action="store_true",
                    help="не архивировать задания с давно прошедшим дедлайном")
    sp = add("find", cmd_find, "поиск по сохранённым сообщениям группы")
    sp.add_argument("query", nargs="+")
    add("login", cmd_login, "войти в Telegram (один раз)")
    add("groups", cmd_groups, "показать группы/каналы и их id")
    sp = add("sync", cmd_sync, "забрать новые сообщения и выйти")
    sp.add_argument("--limit", type=int, help="взять последние N сообщений")
    add("watch", cmd_watch, "следить за группой в реальном времени")
    sp = add("list", cmd_list, "открытые задания по дедлайну")
    sp.add_argument("--all", action="store_true", help="включая выполненные")
    sp.add_argument("--subject", help="фильтр по предмету")
    sp = add("soon", cmd_soon, "задания с дедлайном в ближайшие дни")
    sp.add_argument("days", nargs="?", type=int, default=7)
    sp = add("show", cmd_show, "полный текст задания")
    sp.add_argument("id", type=int)
    for name, st, h in (("done", "done", "выполнено"), ("doing", "doing", "в работе"),
                        ("reopen", "new", "вернуть в новые")):
        sp = add(name, _setter(st), h)
        sp.add_argument("id", type=int)
    sp = add("add", cmd_add, "добавить задание вручную")
    sp.add_argument("title")
    sp.add_argument("-d", "--deadline")
    sp.add_argument("-s", "--subject")
    sp = add("deadline", cmd_deadline, "поменять дедлайн ('-' — убрать)")
    sp.add_argument("id", type=int)
    sp.add_argument("when", nargs="+")
    sp = add("subject", cmd_subject, "задать предмет")
    sp.add_argument("id", type=int)
    sp.add_argument("name", nargs="+")
    add("subjects", cmd_subjects, "проставить предметы по subjects.json тем, у кого их нет")
    sp = add("promote", cmd_promote, "сделать заданием сообщение по его номеру")
    sp.add_argument("msg_id", type=int)
    sp = add("rm", cmd_rm, "удалить задание")
    sp.add_argument("id", type=int)
    return p


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args()
    a.fn(a)
