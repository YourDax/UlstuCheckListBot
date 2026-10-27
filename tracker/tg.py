"""Чтение группы через Telethon (аккаунт пользователя, только чтение)."""
import asyncio

from telethon import TelegramClient, events, functions

from . import config as cfg
from . import db, view
from .ingest import ingest


def client():
    if not cfg.API_ID or not cfg.API_HASH:
        raise SystemExit("Заполните TG_API_ID и TG_API_HASH в .env (см. README).")
    cfg.DATA.mkdir(exist_ok=True)
    return TelegramClient(str(cfg.DATA / "session"), cfg.API_ID, cfg.API_HASH)


async def resolve_group(c):
    if not cfg.GROUP:
        raise SystemExit("Заполните TG_GROUP в .env (название, @username или id; список: groups).")
    key = cfg.GROUP.strip()
    async for d in c.iter_dialogs():
        uname = getattr(d.entity, "username", None)
        if (str(d.id) == key or (uname and "@" + uname.lower() == key.lower())
                or key.lower() in (d.name or "").lower()):
            return d.entity
    raise SystemExit(f"Группа «{key}» не найдена среди ваших диалогов.")


def _link(entity, msg_id):
    uname = getattr(entity, "username", None)
    return f"https://t.me/{uname}/{msg_id}" if uname else f"https://t.me/c/{entity.id}/{msg_id}"


def topic_id(msg):
    """Номер темы форума, в которой написано сообщение (None — общая тема или не форум)."""
    r = getattr(msg, "reply_to", None)
    if r is None or not getattr(r, "forum_topic", False):
        return None
    return r.reply_to_top_id or r.reply_to_msg_id


async def load_topics(c, entity):
    """{id темы: название} для группы-форума; для обычной группы пусто."""
    if not getattr(entity, "forum", False):
        return {}
    try:
        res = await c(functions.messages.GetForumTopicsRequest(
            peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
    except Exception as e:  # без тем теряем только автоматический предмет — не падаем
        print(f"Не удалось получить темы форума: {e}")
        return {}
    return {t.id: t.title for t in res.topics if hasattr(t, "title")}


async def _fresh_topics(c, entity, topics, msg):
    """Подгружает темы, если в сообщении встретилась неизвестная (создали новую)."""
    tid = topic_id(msg)
    if tid and tid not in topics:
        topics.update(await load_topics(c, entity))
        topics.setdefault(tid, None)  # удалённая тема — не перезапрашивать на каждое сообщение
    return topics


def handle_message(con, entity, msg, topics=None):
    """Сохраняет сообщение и, если оно похоже на задание, заводит/обновляет задание.
    Возвращает строку задания, если оно НОВОЕ, иначе None."""
    text = msg.message or ""
    if msg.file is not None and getattr(msg.file, "name", None):
        text += f"\n[файл: {msg.file.name}]"
    posted = msg.date.astimezone(cfg.TZ).replace(tzinfo=None)
    topic = (topics or {}).get(topic_id(msg))
    return ingest(con, msg.id, text, posted, str(msg.sender_id or ""), _link(entity, msg.id), topic)


async def sync(c, entity, con, limit=None):
    """Догружает сообщения: с последнего обработанного или за последние `limit`."""
    last = db.get_meta(con, "last_msg_id")
    if last and limit is None:
        msgs = [m async for m in c.iter_messages(entity, min_id=int(last))]
    else:
        msgs = [m async for m in c.iter_messages(entity, limit=limit or cfg.BACKFILL)]
    msgs.sort(key=lambda m: m.id)
    topics = await load_topics(c, entity)
    new = []
    for m in msgs:
        await _fresh_topics(c, entity, topics, m)
        card = handle_message(con, entity, m, topics)
        if card:
            new.append(card)
    if msgs:
        db.set_meta(con, "last_msg_id", max(int(last or 0), msgs[-1].id))
    return len(msgs), new


async def _notify(c, text):
    if cfg.NOTIFY:
        await c.send_message("me", text)


async def _reminder_loop(c, con):
    while True:
        for t, mask in db.due_reminders(con):
            await _notify(c, f"⏰ Скоро дедлайн\n{view.card(t)}")
            db.update(con, t["id"], reminded=mask)
            print(f"напоминание: #{t['id']} {t['title']}")
        await asyncio.sleep(600)


async def watch(c, entity, con):
    count, new = await sync(c, entity, con)
    print(f"Догружено сообщений: {count}, новых заданий: {len(new)}")
    for card in new:
        await _notify(c, f"🆕 Новое задание\n{card}")

    topics = await load_topics(c, entity)
    if topics:
        print("Темы форума: " + ", ".join(t for t in topics.values() if t))

    async def on_msg(event):
        await _fresh_topics(c, entity, topics, event.message)
        card = handle_message(con, entity, event.message, topics)
        db.set_meta(con, "last_msg_id", max(int(db.get_meta(con, "last_msg_id") or 0),
                                            event.message.id))
        if card:
            print("Новое задание:\n" + card + "\n")
            await _notify(c, f"🆕 Новое задание\n{card}")

    c.add_event_handler(on_msg, events.NewMessage(chats=entity))
    c.add_event_handler(on_msg, events.MessageEdited(chats=entity))
    print(f"Слежу за «{getattr(entity, 'title', entity.id)}». Ctrl+C — выход.")
    reminders = asyncio.create_task(_reminder_loop(c, con))
    try:
        await c.run_until_disconnected()
    finally:
        reminders.cancel()
