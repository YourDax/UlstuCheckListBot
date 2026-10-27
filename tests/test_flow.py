import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tracker import db, tg


def fake_msg(msg_id, text, date=None, reply_to=None):
    return SimpleNamespace(id=msg_id, message=text, file=None, sender_id=1, reply_to=reply_to,
                           date=date or datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc))


ENTITY = SimpleNamespace(id=123, username=None)


class Flow(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old = db.DATA
        db.DATA = Path(self._dir.name)
        self._url, db.DATABASE_URL = db.DATABASE_URL, ""
        self.con = db.connect()

    def tearDown(self):
        self.con.close()
        db.DATA, db.DATABASE_URL = self._old, self._url
        self._dir.cleanup()

    def test_new_task_then_edit_keeps_single_row(self):
        card = tg.handle_message(self.con, ENTITY, fake_msg(10, "Лабораторная 2 по БД, сдать до 20.11"))
        self.assertIsNotNone(card)
        self.assertIn("https://t.me/c/123/10", card)
        # правка сообщения: дедлайн сдвинули, нового задания не появилось
        again = tg.handle_message(self.con, ENTITY, fake_msg(10, "Лабораторная 2 по БД, сдать до 25.11"))
        self.assertIsNone(again)
        rows = db.list_tasks(self.con)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["deadline"].startswith("2026-11-25"))

    def test_chat_is_not_a_task_but_can_be_promoted(self):
        self.assertIsNone(tg.handle_message(self.con, ENTITY, fake_msg(11, "Всем привет!")))
        self.assertEqual(db.list_tasks(self.con), [])
        self.assertIsNotNone(db.get_message(self.con, 11))

    def test_forum_topic_gives_subject(self):
        topics = {5: "МНП(Долинина)", 6: "Объявления"}
        direct = SimpleNamespace(forum_topic=True, reply_to_top_id=None, reply_to_msg_id=5)
        in_thread = SimpleNamespace(forum_topic=True, reply_to_top_id=5, reply_to_msg_id=99)
        general = SimpleNamespace(forum_topic=True, reply_to_top_id=None, reply_to_msg_id=6)
        tg.handle_message(self.con, ENTITY, fake_msg(20, "Реферат до 20.11", reply_to=direct), topics)
        tg.handle_message(self.con, ENTITY, fake_msg(21, "Сдать отчёт до 21.11", reply_to=in_thread), topics)
        tg.handle_message(self.con, ENTITY, fake_msg(22, "Сдать эссе до 22.11", reply_to=general), topics)
        tg.handle_message(self.con, ENTITY, fake_msg(23, "Сдать лабу до 23.11"), topics)  # без темы
        by_msg = {t["msg_id"]: t["subject"] for t in db.list_tasks(self.con)}
        # и прямое сообщение в теме, и ответ внутри неё; имя берётся из subjects.json по фамилии
        self.assertEqual(by_msg[20], "Методология научного познания")
        self.assertEqual(by_msg[21], "Методология научного познания")
        self.assertIsNone(by_msg[22])  # «Объявления» — не предмет
        self.assertIsNone(by_msg[23])

    def test_manual_deadline_survives_edit(self):
        tg.handle_message(self.con, ENTITY, fake_msg(12, "Реферат до 20.11"))
        tid = db.list_tasks(self.con)[0]["id"]
        db.update(self.con, tid, deadline="2026-11-01 10:00", manual_deadline=1)
        tg.handle_message(self.con, ENTITY, fake_msg(12, "Реферат до 30.11"))
        self.assertEqual(db.get_task(self.con, tid)["deadline"], "2026-11-01 10:00")

    def test_reminders_fire_once_per_stage(self):
        now = datetime(2026, 10, 27, 12, 0)
        db.add_manual(self.con, "Скоро", now + timedelta(hours=10), None)
        db.add_manual(self.con, "Через неделю", now + timedelta(days=7), None)
        due = db.due_reminders(self.con, now)
        self.assertEqual([t["title"] for t, _ in due], ["Скоро"])
        db.update(self.con, due[0][0]["id"], reminded=due[0][1])
        self.assertEqual(db.due_reminders(self.con, now), [])
        # за 3 часа — второе напоминание
        later = now + timedelta(hours=8)
        self.assertEqual(len(db.due_reminders(self.con, later)), 1)


if __name__ == "__main__":
    unittest.main()
