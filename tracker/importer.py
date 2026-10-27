"""Импорт истории из экспорта Telegram Desktop: JSON (result.json) или HTML (messages*.html)."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db
from .ingest import ingest

_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2})")


# ---------- JSON ----------

def _flatten(text):
    """В JSON text — строка или список кусков (строки и {'type':..., 'text':...})."""
    if isinstance(text, str):
        return text
    return "".join(p if isinstance(p, str) else p.get("text", "") for p in text)


def _pick_chat(data, name):
    if "messages" in data:  # экспорт одного чата
        return data
    chats = data.get("chats", {}).get("list", [])  # экспорт всех чатов
    hits = [c for c in chats if name.lower() in (c.get("name") or "").lower()] if name else chats
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(repr(c.get("name")) for c in (hits or chats)[:15])
    raise SystemExit("Укажите чат через --chat. " + (f"Найдено: {names}" if names else "Чатов нет."))


def _json_link(chat, msg_id):
    # у супергрупп id в экспорте совпадает с t.me/c/<id>; для личных чатов ссылки нет
    if chat.get("type") in ("private_supergroup", "public_supergroup", "private_channel", "public_channel"):
        return f"https://t.me/c/{chat['id']}/{msg_id}"
    return None


def _read_json(path, chat_name):
    chat = _pick_chat(json.loads(path.read_text(encoding="utf-8")), chat_name)
    for m in chat.get("messages", []):
        if m.get("type") != "message":
            continue
        text = _flatten(m.get("text", ""))
        f = m.get("file_name") or (m["file"] if isinstance(m.get("file"), str)
                                   and not m["file"].startswith("(") else None)
        if f:
            text += f"\n[файл: {Path(f).name}]"
        yield (m["id"], datetime.fromisoformat(m["date"]), str(m.get("from_id", "")),
               text, _json_link(chat, m["id"]))


# ---------- HTML ----------

def _html_files(directory):
    def num(p):  # messages.html, messages2.html, ... в порядке номеров
        m = re.search(r"(\d+)", p.stem)
        return int(m[1]) if m else 1
    return sorted(directory.glob("messages*.html"), key=num)


def _node_text(node):
    for br in node.find_all("br"):
        br.replace_with("\n")
    return node.get_text().strip()


def _read_html(paths):
    from bs4 import BeautifulSoup  # импортируем лениво: нужен только для HTML-экспорта

    sender, posted = "", None
    for path in paths:
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for msg in soup.select("div.message"):
            classes = msg.get("class", [])
            m = re.fullmatch(r"message(\d+)", msg.get("id", ""))
            if "service" in classes or not m:
                continue
            body = msg.find("div", class_="body", recursive=False)
            if body is None:
                continue
            name = body.find("div", class_="from_name", recursive=False)
            if name is not None:  # у «joined»-сообщений автора нет — он тот же, что выше
                sender = name.get_text().strip()
            date = body.find("div", class_="date", recursive=False)
            dm = _DATE.search(date.get("title", "")) if date is not None else None
            if dm:
                d, mo, y, h, mi, s = map(int, dm.groups())
                posted = datetime(y, mo, d, h, mi, s)
            if posted is None:
                continue
            parts = [_node_text(t) for t in msg.select("div.text")]  # свой текст и пересланный
            parts += [f"[файл: {t.get_text().strip()}]" for t in msg.select(".media_file .title")]
            yield int(m[1]), posted, sender, "\n".join(p for p in parts if p), None


# ---------- общий вход ----------

def _source(path, chat_name):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Не найдено: {p}")
    if p.is_dir():
        if (p / "result.json").exists():
            p = p / "result.json"
        else:
            files = _html_files(p)
            if not files:
                raise SystemExit(f"В папке нет result.json и messages*.html: {p}")
            return _read_html(files)
    if p.suffix.lower() == ".html":
        return _read_html([p])
    return _read_json(p, chat_name)


def import_export(con, path, chat_name=None, archive_past=True):
    """Возвращает (сообщений обработано, новых заданий, карточки новых заданий, в архиве).

    archive_past: новые задания, у которых дедлайн прошёл больше суток назад, сразу получают
    статус «выполнено» — история не должна засорять список просрочкой."""
    before = {r["msg_id"] for r in con.execute("SELECT msg_id FROM tasks").fetchall()}
    total, cards, last = 0, [], 0
    for msg_id, posted, sender, text, link in _source(path, chat_name):
        card = ingest(con, msg_id, text, posted, sender, link)
        total += 1
        last = max(last, msg_id)
        if card:
            cards.append(card)
    if last:
        db.set_meta(con, "last_msg_id", max(int(db.get_meta(con, "last_msg_id") or 0), last))
    archived = 0
    if archive_past:
        limit = (config.now() - timedelta(days=1)).strftime(db.ISO)
        for row in con.execute("SELECT msg_id FROM tasks WHERE status='new' AND deadline<?",
                               (limit,)).fetchall():
            msg_id = row["msg_id"]
            if msg_id not in before:
                archived += db.update_by_msg(con, msg_id, status="done")
    return total, len(cards), cards, archived
