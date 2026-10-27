"""Telegram-бот @magchecklistbot: разбор апдейтов Bot API (webhook на Vercel).

Ввод-вывод только через `Api` и базу, поэтому всё тестируется с подменным Api.
Бот приватный: отвечает одному владельцу (OWNER_ID), остальных игнорирует."""
import json
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, importer, parse, subjects, view
from .ingest import ingest

MAX_LEN = 4000  # лимит Telegram — 4096 символов
MAX_IMPORT_BYTES = 8 * 1024 * 1024

HELP = """📚 Трекер заданий магистратуры

📱 Главное — приложение: /app (или кнопка внизу). Ниже — команды для чата.

Команды:
/list [предмет] — открытые задания по дедлайну
/soon [дней] — дедлайны в ближайшие дни (по умолчанию 7)
/all [предмет] — вместе с выполненными
/t13 или /show 13 — карточка задания с кнопками
/done 13, /doing 13, /reopen 13 — статус (можно несколько номеров)
/add Название | дедлайн | предмет — добавить вручную
   пример: /add Лаба 4 | 20.11 18:00 | Методы оптимизации
/deadline 13 05.12 18:00 — поменять срок («-» — убрать)
/subject 13 Название предмета
/rm 13 — удалить (спрошу подтверждение)
/find слово — поиск по сообщениям группы
/promote 123 — сделать заданием найденное сообщение
/subjects — проставить предметы по subjects.json
/digest — сводка сейчас

Как добавлять задания:
• перешлите мне сообщение из группы — оно станет заданием;
• пришлите файл экспорта Telegram Desktop (messages.html или result.json) — разберу всю историю."""

APP_BUTTON = {"text": "📱 Открыть приложение", "web_app": {"url": config.APP_URL}}
MAIN_KB = {"keyboard": [[APP_BUTTON], ["📋 Список", "⏰ Скоро"], ["🗂 Все", "❓ Помощь"]], "resize_keyboard": True}
APP_INLINE = {"inline_keyboard": [[{"text": "📱 Открыть мои лабы", "web_app": {"url": config.APP_URL}}]]}

WELCOME = """Привет! Здесь все лабы и задания со сроками.

Откройте приложение: там видно, что просрочено, что горит и что без срока. Статус и срок меняются одним касанием.

Кнопка «📱 Открыть приложение» всегда внизу, а ещё — слева от поля ввода."""
BUTTONS = {"📋 Список": "list", "⏰ Скоро": "soon", "🗂 Все": "all", "❓ Помощь": "help"}


class ApiError(RuntimeError):
    pass


class Api:
    """Минимальный клиент Bot API на urllib (без внешних зависимостей)."""

    def __init__(self, token):
        self.token = token

    def call(self, method, **params):
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.load(r)["result"]
        except urllib.error.HTTPError as e:  # текст ошибки без URL: в нём токен
            try:
                desc = json.load(e).get("description", "")
            except Exception:
                desc = ""
            raise ApiError(f"{method}: HTTP {e.code} {desc}") from None
        except urllib.error.URLError as e:
            raise ApiError(f"{method}: {e.reason}") from None

    def send(self, chat_id, text, keyboard=None):
        chunks = _chunks(text)
        for i, part in enumerate(chunks):
            params = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if keyboard and i == len(chunks) - 1:  # клавиатура — под последним куском
                params["reply_markup"] = keyboard
            self.call("sendMessage", **params)

    def edit(self, chat_id, message_id, text, keyboard=None):
        params = {"chat_id": chat_id, "message_id": message_id, "text": text[:MAX_LEN],
                  "disable_web_page_preview": True}
        if keyboard:
            params["reply_markup"] = keyboard
        try:
            self.call("editMessageText", **params)
        except ApiError as e:
            if "not modified" not in str(e):
                raise

    def answer(self, callback_id, text=""):
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def download(self, file_id):
        path = self.call("getFile", file_id=file_id)["file_path"]
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{path}",
                                        timeout=20) as r:
                return r.read(MAX_IMPORT_BYTES + 1)
        except urllib.error.URLError as e:
            raise ApiError(f"скачивание файла: {getattr(e, 'reason', e)}") from None


def _chunks(text, size=MAX_LEN):
    """Режет длинный ответ по пустым строкам/переводам строк, чтобы влезть в лимит Telegram."""
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for block in text.split("\n"):
        while len(block) > size:  # одна строка длиннее лимита — режем жёстко
            if cur:
                out.append(cur)
                cur = ""
            out.append(block[:size])
            block = block[size:]
        if len(cur) + len(block) + 1 > size:
            out.append(cur)
            cur = block
        else:
            cur = f"{cur}\n{block}" if cur else block
    if cur:
        out.append(cur)
    return out


# ---------- оформление ----------

def item(t):
    """Два коротких поля на строку — удобно читать с телефона; /tN открывает карточку."""
    title = t["title"] if len(t["title"]) <= 70 else t["title"][:67] + "..."
    return f"/t{t['id']} {view.STATUS[t['status']]} {view.when(t['deadline'])} · {view.rel(t['deadline'])}\n   {title}"


def grouped(tasks):
    """Задания по предметам (порядок — по первому дедлайну в группе)."""
    if not tasks:
        return "Заданий нет."
    groups = {}
    for t in tasks:
        groups.setdefault(t["subject"] or "Без предмета", []).append(t)
    parts = []
    for name, items in groups.items():
        parts.append(f"📚 {name} ({len(items)})\n" + "\n".join(item(t) for t in items))
    return "\n\n".join(parts)


def card_keyboard(t):
    row = []
    if t["status"] != "doing":
        row.append({"text": "▶ В работе", "callback_data": f"st:{t['id']}:doing"})
    if t["status"] != "done":
        row.append({"text": "✓ Готово", "callback_data": f"st:{t['id']}:done"})
    else:
        row.append({"text": "↩ Вернуть", "callback_data": f"st:{t['id']}:new"})
    return {"inline_keyboard": [row, [{"text": "🗑 Удалить", "callback_data": f"rm:{t['id']}"}]]}


def _ts(ts):
    return datetime.fromtimestamp(ts, config.TZ).replace(tzinfo=None)


class Bot:
    def __init__(self, con, api, owner_id):
        self.con, self.api, self.owner = con, api, owner_id

    # ----- вход -----

    def handle(self, update):
        uid = update.get("update_id", 0)
        msg = update.get("message") or update.get("edited_message")
        cb = update.get("callback_query")
        user = (cb or msg or {}).get("from") or {}
        chat = (msg or (cb or {}).get("message") or {}).get("chat") or {}
        if chat.get("type") != "private" or not user.get("id"):
            return
        if not self.owner:  # первый запуск: подсказываем, какой id вписать
            self.api.send(chat["id"], f"Ваш Telegram id: {user['id']}\nВпишите его в переменную OWNER_ID "
                                      "проекта на Vercel и сделайте redeploy — после этого бот заработает.")
            return
        if user["id"] != self.owner:
            return  # чужим не отвечаем
        last = int(db.get_meta(self.con, "last_update_id") or 0)
        if uid and uid <= last:
            return  # повторная доставка того же апдейта
        if uid:
            db.set_meta(self.con, "last_update_id", uid)
        if cb:
            self.on_callback(cb)
        elif update.get("message"):
            self.on_message(msg)

    def say(self, chat, text, keyboard=None):
        self.api.send(chat, text, keyboard)

    # ----- сообщения -----

    def on_message(self, msg):
        chat = msg["chat"]["id"]
        doc = msg.get("document")
        if doc:
            return self.import_file(chat, doc)
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if msg.get("forward_origin") or msg.get("forward_date"):
            return self.add_forwarded(chat, msg, text)
        if not text:
            return self.say(chat, "Пришлите команду, пересланное сообщение или файл экспорта. /help")
        if text in BUTTONS:
            cmd, args = BUTTONS[text], ""
        elif text.startswith("/"):
            head, _, args = text.partition(" ")
            cmd = head[1:].split("@")[0].lower()
            m = re.fullmatch(r"(t|show|done|doing|reopen|rm)_?(\d+)", cmd)  # /t13, /done_13
            if m:
                cmd, args = ("show" if m[1] == "t" else m[1]), m[2]
        else:
            return self.say(chat, "Не понял. Команды: /help", MAIN_KB)
        fn = getattr(self, f"cmd_{cmd}", None)
        if fn is None:
            return self.say(chat, "Такой команды нет. /help")
        fn(chat, args.strip())

    # ----- команды -----

    def cmd_start(self, chat, args):
        self.say(chat, WELCOME, APP_INLINE)
        self.say(chat, "Быстрые кнопки внизу ⬇️ (полный список команд — /help)", MAIN_KB)

    def cmd_app(self, chat, args):
        self.say(chat, "Ваши лабы и задания:", APP_INLINE)

    def cmd_help(self, chat, args):
        self.say(chat, HELP, MAIN_KB)

    def cmd_list(self, chat, args):
        self.say(chat, grouped(db.list_tasks(self.con, subject=args or None)), MAIN_KB)

    def cmd_all(self, chat, args):
        self.say(chat, grouped(db.list_tasks(self.con, include_done=True, subject=args or None)))

    def cmd_soon(self, chat, args):
        days = int(args) if args.isdigit() else 7
        tasks = db.list_tasks(self.con, until=config.now() + timedelta(days=days))
        self.say(chat, f"⏰ Дедлайны на {days} дн.\n\n" + grouped(tasks) if tasks
                 else f"На ближайшие {days} дн. дедлайнов нет 🎉")

    def _task(self, chat, args):
        m = re.fullmatch(r"#?(\d+)", args.strip())
        t = db.get_task(self.con, int(m[1])) if m else None
        if not t:
            self.say(chat, "Укажите номер задания, например /show 13" if not m else f"Задания #{m[1]} нет.")
        return t

    def cmd_show(self, chat, args):
        t = self._task(chat, args)
        if t:
            self.say(chat, view.card(t), card_keyboard(t))

    def _set_status(self, chat, args, status, label):
        ids = re.findall(r"\d+", args)
        if not ids:
            return self.say(chat, f"Укажите номера: /{label} 13 14")
        done, missing = [], []
        for i in ids:
            (done if db.update(self.con, int(i), status=status) else missing).append(i)
        text = (f"Обновил: {', '.join('#' + i for i in done)}" if done else "")
        text += (f"\nНет заданий: {', '.join('#' + i for i in missing)}" if missing else "")
        self.say(chat, text.strip())

    def cmd_done(self, chat, args):
        self._set_status(chat, args, "done", "done")

    def cmd_doing(self, chat, args):
        self._set_status(chat, args, "doing", "doing")

    def cmd_reopen(self, chat, args):
        self._set_status(chat, args, "new", "reopen")

    def cmd_add(self, chat, args):
        parts = [p.strip() for p in args.split("|")]
        if not parts[0]:
            return self.say(chat, "Формат: /add Название | дедлайн | предмет\nНапример: /add Лаба 4 | 20.11 18:00 | Методы оптимизации")
        deadline = None
        if len(parts) > 1 and parts[1]:
            deadline = parse.find_deadline(parts[1], config.now(), explicit=True)
            if not deadline:
                return self.say(chat, f"Не понял дату «{parts[1]}». Примеры: 15.11, 15.11 18:00, завтра, пятница 12:00.")
        subject = parts[2] if len(parts) > 2 and parts[2] else subjects.guess(parts[0])
        tid = db.add_manual(self.con, parts[0], deadline, subject)
        t = db.get_task(self.con, tid)
        self.say(chat, "Добавил ✅\n" + view.card(t), card_keyboard(t))

    def cmd_deadline(self, chat, args):
        m = re.match(r"#?(\d+)\s+(.+)", args)
        if not m:
            return self.say(chat, "Формат: /deadline 13 05.12 18:00 (или «-» чтобы убрать)")
        if not db.get_task(self.con, int(m[1])):
            return self.say(chat, f"Задания #{m[1]} нет.")
        dl = None
        if m[2].strip() not in ("-", "нет"):
            dl = parse.find_deadline(m[2], config.now(), explicit=True)
            if not dl:
                return self.say(chat, f"Не понял дату «{m[2]}». Примеры: 15.11, 15.11 18:00, завтра, пятница 12:00.")
        db.update(self.con, int(m[1]), deadline=db.iso(dl), manual_deadline=1, reminded=0)
        t = db.get_task(self.con, int(m[1]))
        self.say(chat, view.card(t), card_keyboard(t))

    def cmd_subject(self, chat, args):
        m = re.match(r"#?(\d+)\s+(.+)", args)
        if not m:
            return self.say(chat, "Формат: /subject 13 Название предмета")
        if not db.update(self.con, int(m[1]), subject=m[2].strip()):
            return self.say(chat, f"Задания #{m[1]} нет.")
        self.say(chat, view.card(db.get_task(self.con, int(m[1]))))

    def cmd_rm(self, chat, args):
        t = self._task(chat, args)
        if t:
            self.say(chat, f"Удалить навсегда?\n\n{view.line(t)}",
                     {"inline_keyboard": [[{"text": "Да, удалить", "callback_data": f"rmy:{t['id']}"},
                                           {"text": "Отмена", "callback_data": f"rmn:{t['id']}"}]]})

    def cmd_find(self, chat, args):
        if not args:
            return self.say(chat, "Формат: /find слово")
        rows = db.find_messages(self.con, args)
        if not rows:
            return self.say(chat, "Ничего не нашёл.")
        lines = []
        for r in rows:
            snippet = r["text"].replace("\n", " / ")
            lines.append(f"сообщение {r['msg_id']} · {(r['posted_at'] or '')[:10]}\n   {snippet[:140]}")
        self.say(chat, "\n".join(lines) + "\n\nСделать заданием: /promote НОМЕР")

    def cmd_promote(self, chat, args):
        m = re.fullmatch(r"#?(-?\d+)", args)
        msg = db.get_message(self.con, int(m[1])) if m else None
        if not msg:
            return self.say(chat, "Нет такого сообщения. Найдите номер через /find слово")
        posted = datetime.strptime(msg["posted_at"], db.ISO) if msg["posted_at"] else config.now()
        db.upsert_task(self.con, msg["msg_id"], parse.make_title(msg["text"]), msg["text"],
                       subjects.guess(msg["text"]), parse.find_deadline(msg["text"], posted),
                       msg["link"], posted)
        t = self.con.execute("SELECT * FROM tasks WHERE msg_id=?", (msg["msg_id"],)).fetchone()
        self.say(chat, view.card(t), card_keyboard(t))

    def cmd_subjects(self, chat, args):
        mapping = subjects.load()
        if not mapping:
            return self.say(chat, "Файл subjects.json пуст или не найден.")
        set_, left = 0, []
        for t in db.list_tasks(self.con, include_done=True):
            if t["subject"]:
                continue
            s = subjects.match(f"{t['title']}\n{t['body']}", mapping)
            if s:
                db.update(self.con, t["id"], subject=s)
                set_ += 1
            else:
                left.append(t)
        text = f"Предмет проставлен: {set_}. Без предмета: {len(left)}."
        if left:
            text += "\n" + "\n".join(f"/t{t['id']} {t['title'][:60]}" for t in left[:20])
            text += "\n\nЗадать вручную: /subject НОМЕР Название"
        self.say(chat, text)

    def cmd_digest(self, chat, args):
        self.say(chat, digest(self.con) or "Ближайших дедлайнов нет 🎉")

    # ----- добавление заданий -----

    def add_forwarded(self, chat, msg, text):
        doc = msg.get("document")
        if doc and doc.get("file_name"):
            text = f"{text}\n[файл: {doc['file_name']}]".strip()
        if not text:
            return self.say(chat, "В пересланном сообщении нет текста — добавьте задание вручную: /add")
        mid = -int(msg["message_id"])  # отрицательные номера — чтобы не пересечься с номерами группы
        posted = _ts(msg.get("forward_date") or msg["date"])
        ingest(self.con, mid, text, posted, "forward", None)
        if not db.get_task_by_msg(self.con, mid):  # не опознано как задание — вы переслали сами, значит задание
            db.upsert_task(self.con, mid, parse.make_title(text), text, subjects.guess(text),
                           parse.find_deadline(text, posted), None, posted)
        t = db.get_task_by_msg(self.con, mid)
        self.say(chat, "Добавил задание ✅\n" + view.card(t), card_keyboard(t))

    def import_file(self, chat, doc):
        name = doc.get("file_name") or ""
        if not name.lower().endswith((".html", ".htm", ".json")):
            return self.say(chat, "Принимаю файлы экспорта Telegram Desktop: messages.html или result.json.")
        if (doc.get("file_size") or 0) > MAX_IMPORT_BYTES:
            return self.say(chat, "Файл больше 8 МБ — выгрузите экспорт без медиа.")
        data = self.api.download(doc["file_id"])
        if len(data) > MAX_IMPORT_BYTES:
            return self.say(chat, "Файл больше 8 МБ — выгрузите экспорт без медиа.")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / Path(name).name
            path.write_bytes(data)
            try:
                total, new, _, archived = importer.import_export(self.con, path)
            except SystemExit as e:  # так импортёр сообщает «не тот чат / пустой файл»
                return self.say(chat, f"Не получилось разобрать файл: {e}")
        self.say(chat, f"Файл {name}: сообщений {total}, новых заданий {new}, "
                       f"в архиве (дедлайн давно прошёл) {archived}.\n"
                       "Посмотреть: /list. Если экспорт из нескольких файлов (messages2.html…) — "
                       "пришлите и остальные.", MAIN_KB)

    # ----- кнопки под карточкой -----

    def on_callback(self, cb):
        data = cb.get("data", "")
        msg = cb.get("message") or {}
        chat, mid = msg.get("chat", {}).get("id"), msg.get("message_id")
        m = re.fullmatch(r"(st|rm|rmy|rmn):(\d+)(?::(\w+))?", data)
        if not m or not chat:
            return self.api.answer(cb["id"])
        tid = int(m[2])
        t = db.get_task(self.con, tid)
        if not t:
            self.api.answer(cb["id"], "Задания уже нет")
            return self.api.edit(chat, mid, f"Задания #{tid} нет.")
        if m[1] == "st" and m[3] in ("new", "doing", "done"):
            db.update(self.con, tid, status=m[3])
            t = db.get_task(self.con, tid)
            self.api.answer(cb["id"], {"new": "Вернул", "doing": "В работе ▶", "done": "Готово ✓"}[m[3]])
            self.api.edit(chat, mid, view.card(t), card_keyboard(t))
        elif m[1] == "rm":
            self.api.answer(cb["id"])
            self.api.edit(chat, mid, f"Удалить навсегда?\n\n{view.line(t)}",
                          {"inline_keyboard": [[{"text": "Да, удалить", "callback_data": f"rmy:{tid}"},
                                                {"text": "Отмена", "callback_data": f"rmn:{tid}"}]]})
        elif m[1] == "rmy":
            db.delete(self.con, tid)
            self.api.answer(cb["id"], "Удалено")
            self.api.edit(chat, mid, f"🗑 Удалено: {t['title'][:80]}")
        elif m[1] == "rmn":
            self.api.answer(cb["id"], "Оставил")
            self.api.edit(chat, mid, view.card(t), card_keyboard(t))
        else:
            self.api.answer(cb["id"])


# ---------- расписание: сводка и напоминания ----------

def digest(con, days=7):
    now = config.now()
    open_ = db.list_tasks(con)
    overdue = [t for t in open_ if t["deadline"] and datetime.strptime(t["deadline"], db.ISO) < now]
    soon = [t for t in db.list_tasks(con, until=now + timedelta(days=days)) if t not in overdue]
    nodl = [t for t in open_ if not t["deadline"]]
    if not (overdue or soon):
        return ""
    parts = [f"☀️ Сводка · дедлайны на {days} дн."]
    if overdue:
        parts.append(f"⚠️ Просрочено ({len(overdue)}):\n" + "\n".join(item(t) for t in overdue))
    if soon:
        parts.append(grouped(soon))
    if nodl:
        parts.append(f"Ещё {len(nodl)} без срока — /list")
    return "\n\n".join(parts)


def run_cron(con, api, owner_id, mode="all"):
    """mode: reminders — напоминания за сутки/за 3 часа; digest — утренняя сводка; all — оба."""
    sent = 0
    if mode in ("reminders", "all"):
        for t, mask in db.due_reminders(con):
            api.send(owner_id, "⏰ Скоро дедлайн\n" + view.card(t), card_keyboard(t))
            db.update(con, t["id"], reminded=mask)
            sent += 1
    if mode in ("digest", "all"):
        text = digest(con)
        if text:
            api.send(owner_id, text)
            sent += 1
    return sent


def notify_error(update):
    """Коротко сообщает владельцу, что апдейт не обработан (детали — в логах Vercel)."""
    try:
        user = ((update.get("message") or update.get("callback_query") or {}).get("from") or {}).get("id")
        if user and user == config.OWNER_ID:
            Api(config.BOT_TOKEN).send(user, "⚠️ Что-то пошло не так. Попробуйте ещё раз; "
                                             "если повторится — смотрите логи на Vercel.")
    except Exception:
        pass


def process_update(update):
    """Точка входа для api/webhook.py: свой коннект к базе на каждый апдейт."""
    con = db.connect()
    try:
        Bot(con, Api(config.BOT_TOKEN), config.OWNER_ID).handle(update)
    finally:
        con.close()
