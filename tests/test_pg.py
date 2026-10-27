"""Те же сценарии на настоящем Postgres. Запускается только с TEST_PG_URL, и только на базе,
имя которой заканчивается на _test: тест сам стирает таблицы."""
import os
import unittest
from pathlib import Path
import tempfile

from tracker import db
from tests import test_bot, test_flow, test_import, test_webapp

URL = os.environ.get("TEST_PG_URL", "")
SKIP = "нужен TEST_PG_URL=postgresql://…/имя_test"
if URL and not URL.split("?")[0].rstrip("/").endswith("_test"):
    raise SystemExit("TEST_PG_URL должен указывать на базу с именем *_test — тест стирает таблицы")


def fresh_pg():
    con = db.open_pg(URL)
    for t in ("tasks", "messages", "meta"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.raw.commit()
    con.close()
    return db.open_pg(URL)


def _use_pg(self):
    self.con.close()
    db.DATABASE_URL = URL
    self.con = fresh_pg()


@unittest.skipUnless(URL, SKIP)
class FlowPG(test_flow.Flow):
    def setUp(self):
        super().setUp()
        _use_pg(self)


@unittest.skipUnless(URL, SKIP)
class ImportPG(test_import.Import):
    def setUp(self):
        super().setUp()
        _use_pg(self)


@unittest.skipUnless(URL, SKIP)
class BotPG(test_bot.BotCase):
    def make_con(self):
        return fresh_pg()


@unittest.skipUnless(URL, SKIP)
class WebappPG(test_webapp.ApiCase):
    def make_con(self):
        return fresh_pg()


@unittest.skipUnless(URL, SKIP)
class CopyAll(unittest.TestCase):
    def test_sqlite_to_pg_keeps_ids_and_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            src = db.open_sqlite(Path(d) / "t.db")
            db.add_manual(src, "Первое", None, "Физика")
            second = db.add_manual(src, "Второе", None, None)
            db.update(src, second, status="done", subject="ИИС")
            db.save_message(src, 7, None, "u", "текст", None)
            db.set_meta(src, "last_msg_id", 7)
            dst = fresh_pg()
            self.assertEqual(db.copy_all(src, dst), {"tasks": 2, "messages": 1, "meta": 1})
            self.assertEqual(db.copy_all(src, dst)["tasks"], 2)  # повтор безопасен
            rows = {t["id"]: t for t in db.list_tasks(dst, include_done=True)}
            self.assertEqual(rows[second]["status"], "done")
            self.assertEqual(rows[second]["subject"], "ИИС")
            self.assertEqual(db.get_meta(dst, "last_msg_id"), "7")
            new_id = db.add_manual(dst, "Новое после переноса", None, None)  # счётчик сдвинут
            self.assertEqual(new_id, second + 1)
            src.close(); dst.close()


if __name__ == "__main__":
    unittest.main()
