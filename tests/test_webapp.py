import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

import app as vercel_app
from tracker import config, db, webapp

TOKEN = "123456:TEST-token"
OWNER = 42


def sign(token=TOKEN, user_id=OWNER, auth_date=None):
    """initData так, как его подписывает Telegram."""
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH1",
              "user": json.dumps({"id": user_id, "first_name": "Лео"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class Verify(unittest.TestCase):
    def test_valid_signature_returns_owner(self):
        self.assertEqual(webapp.verify_init_data(sign(), TOKEN, OWNER), OWNER)

    def test_rejects_wrong_token_tampering_stale_and_strangers(self):
        good = sign()
        self.assertIsNone(webapp.verify_init_data(good, "999:other", OWNER))
        self.assertIsNone(webapp.verify_init_data(good.replace("AAH1", "AAH2"), TOKEN, OWNER))
        self.assertIsNone(webapp.verify_init_data(sign(auth_date=int(time.time()) - 3 * 86400), TOKEN, OWNER))
        self.assertIsNone(webapp.verify_init_data(sign(user_id=7), TOKEN, OWNER))  # подпись верна, но не владелец
        for empty in ("", "hash=abc", "user=%7B%7D"):
            self.assertIsNone(webapp.verify_init_data(empty, TOKEN, OWNER))

    def test_not_configured_means_no_access(self):
        self.assertIsNone(webapp.verify_init_data(sign(), "", OWNER))
        self.assertIsNone(webapp.verify_init_data(sign(), TOKEN, 0))


class Title(unittest.TestCase):
    def test_file_only_title_loses_brackets_and_extension(self):
        self.assertEqual(webapp.display_title({"title": "[файл: Лабораторная работа 1.pdf]"}), "Лабораторная работа 1")
        self.assertEqual(webapp.display_title({"title": "Реферат до 15.11"}), "Реферат до 15.11")


class ApiCase(unittest.TestCase):
    def make_con(self):
        return db.open_sqlite(Path(self._dir.name) / "t.db")

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.con = self.make_con()

    def tearDown(self):
        self.con.close()
        self._dir.cleanup()

    def call(self, method, path, body=None, query=""):
        code, payload = webapp.handle(self.con, method, path, query, json.dumps(body).encode() if body is not None else b"")
        return code, payload

    def test_list_hides_done_unless_asked_and_computes_days(self):
        now = config.now()
        db.add_manual(self.con, "Просрочено", now - timedelta(days=2), "ИИС")
        db.add_manual(self.con, "Через 5 дней", now + timedelta(days=5), None)
        done = db.add_manual(self.con, "Сдано", None, None)
        db.update(self.con, done, status="done")
        code, data = self.call("GET", "/api/app/tasks")
        self.assertEqual(code, 200)
        titles = {t["title"]: t for t in data["tasks"]}
        self.assertEqual(set(titles), {"Просрочено", "Через 5 дней"})
        self.assertTrue(titles["Просрочено"]["overdue"])
        self.assertEqual(titles["Просрочено"]["days"], -2)
        self.assertEqual(titles["Через 5 дней"]["days"], 5)
        self.assertIn("ИИС", data["subjects"])
        _, everything = self.call("GET", "/api/app/tasks", query="all=1")
        self.assertEqual(len(everything["tasks"]), 3)

    def test_create_validates_and_defaults_time(self):
        self.assertEqual(self.call("POST", "/api/app/tasks", {"title": "   "})[0], 400)
        self.assertEqual(self.call("POST", "/api/app/tasks", {"title": "X", "deadline": "когда-нибудь"})[0], 400)
        code, data = self.call("POST", "/api/app/tasks", {"title": " Лаба   4 ", "subject": "Физика", "deadline": "2026-11-20"})
        self.assertEqual(code, 200)
        self.assertEqual(data["task"]["title"], "Лаба 4")  # пробелы схлопнуты
        self.assertEqual(data["task"]["deadline"], "2026-11-20 23:59")
        self.assertEqual(data["task"]["subject"], "Физика")

    def test_update_status_deadline_subject_title(self):
        tid = db.add_manual(self.con, "Лаба", None, None)
        path = f"/api/app/tasks/{tid}"
        self.assertEqual(self.call("POST", path, {"status": "doing"})[1]["task"]["status"], "doing")
        self.assertEqual(self.call("POST", path, {"status": "bogus"})[0], 400)
        task = self.call("POST", path, {"deadline": "2026-12-05T18:30"})[1]["task"]
        self.assertEqual(task["deadline"], "2026-12-05 18:30")
        row = db.get_task(self.con, tid)
        self.assertEqual((row["manual_deadline"], row["reminded"]), (1, 0))  # ручной срок, напоминания заново
        self.assertIsNone(self.call("POST", path, {"deadline": ""})[1]["task"]["deadline"])
        self.assertEqual(self.call("POST", path, {"deadline": "32.13"})[0], 400)
        task = self.call("POST", path, {"subject": "ИИС", "title": "Лаба 5"})[1]["task"]
        self.assertEqual((task["subject"], task["title"]), ("ИИС", "Лаба 5"))
        self.assertIsNone(self.call("POST", path, {"subject": ""})[1]["task"]["subject"])
        self.assertEqual(self.call("POST", path, {"title": " "})[0], 400)

    def test_deleted_overdue_and_unknown(self):
        tid = db.add_manual(self.con, "Лаба", config.now() - timedelta(days=1), None)
        done = self.call("POST", f"/api/app/tasks/{tid}", {"status": "done"})[1]["task"]
        self.assertFalse(done["overdue"])  # сданное просроченным не считается
        self.assertEqual(self.call("DELETE", f"/api/app/tasks/{tid}")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/app/tasks/{tid}")[0], 404)
        self.assertEqual(self.call("POST", "/api/app/tasks/999", {"status": "done"})[0], 404)
        self.assertEqual(self.call("GET", "/api/app/nothing")[0], 404)
        self.assertEqual(webapp.handle(self.con, "POST", "/api/app/tasks", "", b"{oops")[0], 400)


class Wsgi(unittest.TestCase):
    """Страница и API через настоящий WSGI-вход, как на Vercel."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._old = (db.DATA, db.DATABASE_URL)
        db.DATA, db.DATABASE_URL = Path(self._dir.name), ""
        for name, val in (("BOT_TOKEN", TOKEN), ("OWNER_ID", OWNER)):
            p = mock.patch.object(config, name, val)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        db.DATA, db.DATABASE_URL = self._old
        self._dir.cleanup()

    def request(self, method, path, body=b"", init=None, query=""):
        env = {"PATH_INFO": path, "REQUEST_METHOD": method, "QUERY_STRING": query,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        if init is not None:
            env["HTTP_X_TELEGRAM_INIT_DATA"] = init
        out = {}
        chunks = vercel_app.app(env, lambda status, headers: out.update(status=int(status[:3]), headers=dict(headers)))
        return out["status"], out["headers"], b"".join(chunks)

    def test_index_page_is_served(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Лабы и задания".encode(), body)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_api_requires_valid_telegram_signature(self):
        self.assertEqual(self.request("GET", "/api/app/tasks")[0], 401)
        self.assertEqual(self.request("GET", "/api/app/tasks", init="hash=bad")[0], 401)
        self.assertEqual(self.request("GET", "/api/app/tasks", init=sign(user_id=7))[0], 401)
        self.assertEqual(self.request("POST", "/api/app/tasks", b'{"title":"x"}', init=sign(token="1:zzz"))[0], 401)

    def test_owner_can_work_through_wsgi(self):
        init = sign()
        status, headers, body = self.request("POST", "/api/app/tasks", json.dumps({"title": "Лаба", "deadline": "2026-12-01"}).encode(), init)
        self.assertEqual(status, 200)
        tid = json.loads(body)["task"]["id"]
        self.assertIn("application/json", headers["Content-Type"])
        status, _, body = self.request("GET", "/api/app/tasks", init=init)
        self.assertEqual([t["id"] for t in json.loads(body)["tasks"]], [tid])
        self.assertEqual(self.request("DELETE", f"/api/app/tasks/{tid}", init=init)[0], 200)
        self.assertEqual(json.loads(self.request("GET", "/api/app/tasks", init=init)[2])["tasks"], [])

    def test_unknown_paths_and_methods(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("PUT", "/api/app/tasks", init=sign())[0], 405)


if __name__ == "__main__":
    unittest.main()
