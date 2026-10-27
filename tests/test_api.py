import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from wsgiref.simple_server import WSGIRequestHandler, make_server

import app as vercel_app
from tracker import bot, config, web


class Quiet(WSGIRequestHandler):
    def log_message(self, *a):
        pass


class Serve:
    """Поднимает WSGI-приложение (то, что запускает Vercel) на свободном порту."""

    def __enter__(self):
        self.srv = make_server("127.0.0.1", 0, vercel_app.app, handler_class=Quiet)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def call(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        finally:
            e.close()


TG = "X-Telegram-Bot-Api-Secret-Token"


class Webhook(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "WEBHOOK_SECRET", "s3cret")
        p.start()
        self.addCleanup(p.stop)

    def test_rejects_missing_or_wrong_secret(self):
        with Serve() as s, mock.patch.object(bot, "process_update") as proc:
            body = json.dumps({"update_id": 1}).encode()
            self.assertEqual(call(s.url + "/api/webhook", "POST", body=body)[0], 401)
            self.assertEqual(call(s.url + "/api/webhook", "POST", {TG: "nope"}, body)[0], 401)
            proc.assert_not_called()

    def test_refuses_everything_when_secret_not_configured(self):
        with mock.patch.object(config, "WEBHOOK_SECRET", ""), Serve() as s:
            self.assertEqual(call(s.url + "/api/webhook", "POST", {TG: ""}, b"{}")[0], 401)

    def test_valid_update_is_processed(self):
        with Serve() as s, mock.patch.object(bot, "process_update") as proc:
            body = json.dumps({"update_id": 5, "message": {}}).encode()
            self.assertEqual(call(s.url + "/api/webhook", "POST", {TG: "s3cret"}, body)[0], 200)
            proc.assert_called_once_with({"update_id": 5, "message": {}})

    def test_errors_still_return_200_so_telegram_does_not_retry(self):
        with Serve() as s, mock.patch.object(bot, "process_update", side_effect=RuntimeError("x")), \
                mock.patch.object(bot, "notify_error") as notify, mock.patch("traceback.print_exc"):
            code, _ = call(s.url + "/api/webhook", "POST", {TG: "s3cret"}, b'{"update_id": 1}')
            self.assertEqual(code, 200)
            notify.assert_called_once()

    def test_bad_json_and_health_check(self):
        with Serve() as s:
            self.assertEqual(call(s.url + "/api/webhook", "POST", {TG: "s3cret"}, b"{oops")[0], 400)
            self.assertEqual(call(s.url + "/api/webhook")[1], b"magchecklistbot")  # проверка «жив ли», без данных
            self.assertEqual(call(s.url + "/nope")[0], 404)

    def test_oversized_body_is_rejected(self):
        big = b"x" * (web.MAX_BODY + 50)
        env = {"PATH_INFO": "/api/webhook", "REQUEST_METHOD": "POST", "CONTENT_LENGTH": str(len(big)),
               "HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": "s3cret", "wsgi.input": io.BytesIO(big)}
        out = {}
        vercel_app.app(env, lambda status, headers: out.update(status=status))
        self.assertTrue(out["status"].startswith("413"))


class Cron(unittest.TestCase):
    def test_requires_bearer_secret(self):
        with mock.patch.object(config, "CRON_SECRET", "cr0n"), Serve() as s:
            self.assertEqual(call(s.url + "/api/cron")[0], 401)
            self.assertEqual(call(s.url + "/api/cron", headers={"Authorization": "Bearer wrong"})[0], 401)

    def test_unset_secret_locks_endpoint(self):
        with mock.patch.object(config, "CRON_SECRET", ""), Serve() as s:
            self.assertEqual(call(s.url + "/api/cron", headers={"Authorization": "Bearer "})[0], 401)

    def test_runs_with_secret_and_validates_mode(self):
        with mock.patch.object(config, "CRON_SECRET", "cr0n"), mock.patch.object(config, "OWNER_ID", 42), \
                mock.patch.object(bot, "run_cron", return_value=2) as run, \
                mock.patch.object(web.db, "connect"), Serve() as s:
            auth = {"Authorization": "Bearer cr0n"}
            code, body = call(s.url + "/api/cron?mode=digest", headers=auth)
            self.assertEqual((code, json.loads(body)["sent"]), (200, 2))
            self.assertEqual(run.call_args.args[-1], "digest")
            self.assertEqual(call(s.url + "/api/cron?mode=evil", headers=auth)[0], 400)

    def test_without_owner_does_nothing(self):
        with mock.patch.object(config, "CRON_SECRET", "cr0n"), mock.patch.object(config, "OWNER_ID", 0), \
                mock.patch.object(bot, "run_cron") as run, Serve() as s:
            code, _ = call(s.url + "/api/cron", headers={"Authorization": "Bearer cr0n"})
            self.assertEqual(code, 200)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
