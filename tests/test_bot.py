import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from tracker import bot, config, db

OWNER = 42
HTML = """<html><body><div class="history">
<div class="message service" id="message1"><div class="body details">Группа создана</div></div>
<div class="message default clearfix" id="message2"><div class="body">
  <div class="pull_right date details" title="20.10.2026 10:00:00 GMT+04:00">10:00</div>
  <div class="from_name">Юлия</div><div class="text">Реферат до 15.11<br>по МНП</div></div></div>
<div class="message default clearfix joined" id="message3"><div class="body">
  <div class="pull_right date details" title="20.10.2026 10:05:00 GMT+04:00">10:05</div>
  <div class="media_wrap clearfix"><div class="media clearfix pull_left media_file"><div class="body">
    <div class="title bold">Лабораторная работа 2.pdf</div></div></div></div></div></div>
<div class="message default clearfix" id="message4"><div class="body">
  <div class="pull_right date details" title="20.10.2026 11:00:00 GMT+04:00">11:00</div>
  <div class="from_name">Антон</div><div class="text">всем привет</div></div></div>
</div></body></html>"""


class FakeApi:
    def __init__(self):
        self.sent, self.edits, self.answers, self.files = [], [], [], {}

    def send(self, chat, text, keyboard=None):
        self.sent.append((chat, text, keyboard))

    def edit(self, chat, mid, text, keyboard=None):
        self.edits.append((chat, mid, text, keyboard))

    def answer(self, cid, text=""):
        self.answers.append(text)

    def download(self, file_id):
        return self.files[file_id]


def msg(text="", uid=1, user=OWNER, chat_type="private", **extra):
    m = {"message_id": uid, "date": 1790000000, "chat": {"id": user, "type": chat_type},
         "from": {"id": user}, "text": text}
    m.update(extra)
    return {"update_id": uid, "message": m}


def click(data, uid=100, mid=7, user=OWNER):
    return {"update_id": uid, "callback_query": {
        "id": "cb", "from": {"id": user}, "data": data,
        "message": {"message_id": mid, "chat": {"id": user, "type": "private"}}}}


class BotCase(unittest.TestCase):
    def make_con(self):
        return db.open_sqlite(Path(self._dir.name) / "t.db")

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.con = self.make_con()
        self.api = FakeApi()
        self.bot = bot.Bot(self.con, self.api, OWNER)
        self._uid = 1000

    def tearDown(self):
        self.con.close()
        self._dir.cleanup()

    def press(self, data):
        self._uid += 1
        self.bot.handle(click(data, uid=self._uid))

    def say(self, text, **kw):
        self._uid += 1  # у каждого апдейта свой update_id, как у настоящего Telegram
        self.bot.handle(msg(text, uid=self._uid, **kw))
        return self.api.sent[-1][1] if self.api.sent else ""

    # ---- доступ ----

    def test_owner_not_configured_shows_id_only(self):
        b = bot.Bot(self.con, self.api, 0)
        b.handle(msg("/list", user=555))
        self.assertIn("555", self.api.sent[-1][1])
        self.assertIn("OWNER_ID", self.api.sent[-1][1])

    def test_stranger_and_group_chats_are_ignored(self):
        self.bot.handle(msg("/list", user=7))
        self.bot.handle(msg("/list", uid=2, chat_type="supergroup"))
        self.assertEqual(self.api.sent, [])

    def test_duplicate_update_is_processed_once(self):
        update = msg("/help", uid=500)
        self.bot.handle(update)
        self.bot.handle(update)
        self.assertEqual(len(self.api.sent), 1)

    def test_start_points_to_app_and_help_lists_commands(self):
        self.say("/start")
        first, second = self.api.sent[-2], self.api.sent[-1]
        self.assertIn("web_app", str(first[2]))            # inline-кнопка открытия приложения
        self.assertEqual(second[2], bot.MAIN_KB)            # постоянная клавиатура
        self.assertIn("web_app", str(bot.MAIN_KB["keyboard"][0]))
        self.assertNotIn("/add", first[1])                  # приветствие короткое, без простыни команд
        self.say("/help")
        self.assertIn("/add", self.api.sent[-1][1])
        self.say("/app")
        self.assertIn("web_app", str(self.api.sent[-1][2]))

    # ---- задания ----

    def test_add_list_done_flow(self):
        out = self.say("/add Лаба 4 | завтра 18:00 | Физика")
        self.assertIn("Добавил", out)
        self.assertIn("Физика", self.say("/list"))
        self.assertIn("Лаба 4", self.say("📋 Список"))  # кнопка = команда
        self.say("/done 1")
        self.assertEqual(self.say("/list"), "Заданий нет.")
        self.assertIn("✓", self.say("/all"))

    def test_add_without_title_or_bad_date(self):
        self.assertIn("Формат", self.say("/add"))
        self.assertIn("Не понял дату", self.say("/add X | когда-нибудь"))
        self.assertEqual(db.list_tasks(self.con), [])

    def test_task_card_and_status_buttons(self):
        self.say("/add Лаба 4")
        self.say("/t1")
        chat, text, kb = self.api.sent[-1]
        self.assertIn("Лаба 4", text)
        self.assertIn("st:1:done", str(kb))
        self.press("st:1:doing")
        self.assertEqual(db.get_task(self.con, 1)["status"], "doing")
        self.assertIn("▶", self.api.edits[-1][2])
        self.assertNotIn("st:1:doing", str(self.api.edits[-1][3]))  # кнопка «в работе» исчезла

    def test_delete_needs_confirmation(self):
        self.say("/add Лаба 4")
        self.say("/rm 1")
        self.assertIn("rmy:1", str(self.api.sent[-1][2]))
        self.assertIsNotNone(db.get_task(self.con, 1))  # пока не удалено
        self.press("rmn:1")
        self.assertIsNotNone(db.get_task(self.con, 1))
        self.press("rmy:1")
        self.assertIsNone(db.get_task(self.con, 1))

    def test_deadline_and_subject_commands(self):
        self.say("/add Лаба 4")
        self.say("/deadline 1 20.11 18:00")
        self.assertEqual(db.get_task(self.con, 1)["deadline"][5:], "11-20 18:00")
        self.assertEqual(db.get_task(self.con, 1)["manual_deadline"], 1)
        self.say("/deadline 1 -")
        self.assertIsNone(db.get_task(self.con, 1)["deadline"])
        self.say("/subject 1 Методы оптимизации")
        self.assertEqual(db.get_task(self.con, 1)["subject"], "Методы оптимизации")
        self.assertIn("нет", self.say("/subject 99 X"))

    def test_unknown_id_and_garbage(self):
        self.assertIn("нет", self.say("/show 99"))
        self.assertIn("Не понял", self.say("привет"))
        self.assertIn("нет", self.say("/выдумка"))
        self.assertIn("Нет заданий", self.say("/done 5"))

    def test_find_and_promote(self):
        db.save_message(self.con, 77, config.now(), "u", "Принести справку до пятницы", None)
        self.assertIn("77", self.say("/find справк"))
        self.say("/promote 77")
        self.assertEqual(db.list_tasks(self.con)[0]["msg_id"], 77)

    # ---- пересланное и файлы ----

    def test_forwarded_message_becomes_task_even_if_not_recognized(self):
        self.bot.handle(msg("Принести справку в деканат", uid=300, forward_origin={"type": "user"},
                            forward_date=1790000000))
        t = db.list_tasks(self.con)[0]
        self.assertLess(t["msg_id"], 0)
        self.assertIn("справку", t["title"])
        self.assertIn("Добавил", self.api.sent[-1][1])

    def test_html_export_upload(self):
        self.api.files["f1"] = HTML.encode()
        self.bot.handle(msg(uid=400, document={"file_id": "f1", "file_name": "messages.html",
                                               "file_size": len(HTML)}))
        self.assertIn("сообщений 3", self.api.sent[-1][1])
        tasks = {t["msg_id"]: t for t in db.list_tasks(self.con, include_done=True)}
        self.assertEqual(set(tasks), {2, 3})  # «всем привет» — не задание
        self.assertEqual(tasks[2]["deadline"][:10], "2026-11-15")
        self.assertIn("МНП", tasks[2]["body"])  # <br> стал переводом строки внутри текста
        self.assertIn("Лабораторная работа 2.pdf", tasks[3]["body"])

    def test_other_files_are_refused(self):
        self.bot.handle(msg(uid=401, document={"file_id": "x", "file_name": "photo.jpg"}))
        self.assertIn("messages.html", self.api.sent[-1][1])
        self.bot.handle(msg(uid=402, document={"file_id": "x", "file_name": "big.html",
                                               "file_size": 50 * 1024 * 1024}))
        self.assertIn("8 МБ", self.api.sent[-1][1])

    # ---- вспомогательное и расписание ----

    def test_long_answers_are_chunked(self):
        text = "\n".join(f"строка {i} " + "x" * 90 for i in range(200))
        parts = bot._chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= bot.MAX_LEN for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_cron_reminders_fire_once_and_digest(self):
        db.add_manual(self.con, "Скоро сдавать", config.now() + timedelta(hours=10), "ИИС")
        db.add_manual(self.con, "Не скоро", config.now() + timedelta(days=30), None)
        self.assertEqual(bot.run_cron(self.con, self.api, OWNER, "reminders"), 1)
        self.assertIn("Скоро сдавать", self.api.sent[-1][1])
        self.assertEqual(bot.run_cron(self.con, self.api, OWNER, "reminders"), 0)  # второй раз — тишина
        self.assertEqual(bot.run_cron(self.con, self.api, OWNER, "digest"), 1)
        self.assertIn("Скоро сдавать", self.api.sent[-1][1])
        self.assertNotIn("Не скоро", self.api.sent[-1][1])  # вне окна 7 дней

    def test_digest_is_silent_when_nothing_due(self):
        db.add_manual(self.con, "Через месяц", config.now() + timedelta(days=30), None)
        self.assertEqual(bot.run_cron(self.con, self.api, OWNER, "digest"), 0)


if __name__ == "__main__":
    unittest.main()
