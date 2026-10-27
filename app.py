"""Единый вход для Vercel (WSGI): страница Mini App, её API, webhook Telegram и cron."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tracker import web  # noqa: E402

INDEX = ROOT / "webapp" / "index.html"
JSON, TEXT, HTML = "application/json; charset=utf-8", "text/plain; charset=utf-8", "text/html; charset=utf-8"
STATUS_TEXT = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
               413: "Payload Too Large"}


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET")
    query = environ.get("QUERY_STRING", "")
    headers = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}

    def read_body():
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        # читаем на 1 байт больше лимита, чтобы web.* смог отказать «слишком большой запрос»
        return environ["wsgi.input"].read(min(length, web.MAX_BODY + 1))

    ctype = JSON
    if path == "/api/webhook" and method == "POST":
        code, body = web.webhook(headers, read_body())
        ctype = TEXT
    elif path == "/api/cron" and method == "GET":
        code, body = web.cron(headers, query)
    elif path.startswith("/api/app/"):
        code, body = web.app_api(headers, method, path, query, read_body() if method in ("POST", "PUT") else b"")
    elif path == "/" and method == "GET":
        code, body, ctype = 200, INDEX.read_bytes(), HTML
    elif path == "/api/webhook" and method == "GET":
        code, body, ctype = 200, b"magchecklistbot", TEXT  # проверка «жив ли», без данных
    else:
        code, body, ctype = 404, b"not found", TEXT

    start_response(f"{code} {STATUS_TEXT.get(code, 'Error')}", [
        ("Content-Type", ctype), ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")])
    return [body]
