import json
import tempfile
import unittest
from pathlib import Path

from tracker import db, importer

EXPORT = {
    "name": "Магистратура УлГТУ", "type": "private_supergroup", "id": 777,
    "messages": [
        {"id": 1, "type": "service", "date": "2026-09-01T09:00:00", "action": "create_group"},
        {"id": 2, "type": "message", "date": "2026-10-20T09:00:00", "from_id": "user1",
         "text": "Всем привет!"},
        {"id": 3, "type": "message", "date": "2026-10-20T10:00:00", "from_id": "user2",
         "text": ["Предмет: Базы данных\nЛР 2, сдать до ", {"type": "bold", "text": "15.11"}]},
        {"id": 4, "type": "message", "date": "2026-10-21T10:00:00", "from_id": "user2",
         "text": "", "file": "files/ДЗ_4.pdf", "file_name": "ДЗ_4.pdf"},
        {"id": 5, "type": "message", "date": "2026-10-22T10:00:00", "from_id": "user2",
         "text": "Реферат до 3 декабря", "file": "(File not included. Change data exporting settings)"},
    ],
}


class Import(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old = db.DATA
        db.DATA = Path(self._dir.name)
        self._url, db.DATABASE_URL = db.DATABASE_URL, ""
        self.path = Path(self._dir.name) / "result.json"
        self.path.write_text(json.dumps(EXPORT, ensure_ascii=False), encoding="utf-8")
        self.con = db.connect()

    def tearDown(self):
        self.con.close()
        db.DATA, db.DATABASE_URL = self._old, self._url
        self._dir.cleanup()

    def test_import(self):
        total, new, _, _ = importer.import_export(self.con, self.path)
        self.assertEqual((total, new), (4, 3))  # служебное не считается; «Всем привет» — не задание
        tasks = {t["msg_id"]: t for t in db.list_tasks(self.con)}
        self.assertEqual(tasks[3]["subject"], "Базы данных")
        self.assertTrue(tasks[3]["deadline"].startswith("2026-11-15"))
        self.assertIn("ДЗ_4.pdf", tasks[4]["body"])
        self.assertEqual(tasks[5]["link"], "https://t.me/c/777/5")
        self.assertNotIn("File not included", tasks[5]["body"])

    def test_reimport_is_idempotent_and_keeps_status(self):
        importer.import_export(self.con, self.path)
        tid = db.list_tasks(self.con)[0]["id"]
        db.update(self.con, tid, status="done")
        _, new, _, _ = importer.import_export(self.con, self.path.parent)  # и по папке
        self.assertEqual(new, 0)
        self.assertEqual(len(db.list_tasks(self.con, include_done=True)), 3)
        self.assertEqual(db.get_task(self.con, tid)["status"], "done")
        self.assertEqual(db.get_meta(self.con, "last_msg_id"), "5")

    def test_all_chats_export_needs_chat_name(self):
        data = {"chats": {"list": [EXPORT, {"name": "Другой", "id": 1, "messages": []}]}}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(SystemExit):
            importer.import_export(self.con, self.path)
        total, _, _, _ = importer.import_export(self.con, self.path, "магистр")
        self.assertEqual(total, 4)

    def test_past_deadlines_are_archived_only_on_first_import(self):
        old = dict(EXPORT, messages=[
            {"id": 1, "type": "message", "date": "2020-01-01T10:00:00", "from_id": "u",
             "text": "Реферат до 10.01.2020"},
            {"id": 2, "type": "message", "date": "2030-01-01T10:00:00", "from_id": "u",
             "text": "Реферат до 10.01.2030"}])
        self.path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        *_, archived = importer.import_export(self.con, self.path)
        self.assertEqual(archived, 1)
        self.assertEqual([t["msg_id"] for t in db.list_tasks(self.con)], [2])
        tid = db.list_tasks(self.con, include_done=True)[0]["id"]
        db.update(self.con, tid, status="new")  # пользователь вернул задание
        *_, archived = importer.import_export(self.con, self.path)  # повторный импорт не трогает
        self.assertEqual(archived, 0)
        self.assertEqual(db.get_task(self.con, tid)["status"], "new")


if __name__ == "__main__":
    unittest.main()
