"""Общий путь сообщения -> задание. Им пользуются и Telethon, и импорт из экспорта."""
from . import db, parse, subjects, view


def ingest(con, msg_id, text, posted, sender, link, topic=None):
    """Сохраняет сообщение и, если оно похоже на задание, заводит/обновляет его.
    Возвращает карточку задания, если оно НОВОЕ, иначе None."""
    text = (text or "").strip()
    if not text:
        return None
    db.save_message(con, msg_id, posted, sender, text, link)
    deadline = parse.find_deadline(text, posted)
    if not parse.looks_like_task(text, deadline):
        return None
    is_new = db.upsert_task(con, msg_id, parse.make_title(text), text,
                            subjects.guess(text, topic=topic), deadline, link, posted)
    if not is_new:
        return None
    return view.card(con.execute("SELECT * FROM tasks WHERE msg_id=?", (msg_id,)).fetchone())
