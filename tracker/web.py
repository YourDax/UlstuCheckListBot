"""HTTP-слой без привязки к серверу: функции принимают заголовки/тело и возвращают (код, тело).
К WSGI их подключает app.py (Vercel), поэтому всё проверяется обычными тестами."""
import hmac
import json
import traceback
from urllib.parse import parse_qs

from . import bot, config, db, webapp

MAX_BODY = 1_000_000
CRON_MODES = {"all", "digest", "reminders"}


def _same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def webhook(headers, body):
    """headers — словарь с именами в нижнем регистре. Всегда отвечаем 200 на валидный запрос,
    иначе Telegram будет бесконечно переотправлять тот же апдейт."""
    secret = headers.get("x-telegram-bot-api-secret-token", "")
    if not config.WEBHOOK_SECRET or not _same(secret, config.WEBHOOK_SECRET):
        return 401, b"unauthorized"
    if len(body) > MAX_BODY:
        return 413, b"too large"
    try:
        update = json.loads(body)
    except ValueError:
        return 400, b"bad json"
    try:
        bot.process_update(update)
    except Exception:
        traceback.print_exc()  # в логи Vercel; токены в тексте исключений не попадают
        bot.notify_error(update)
    return 200, b"ok"


def app_api(headers, method, path, query, body):
    """JSON-API Mini App. Каждый запрос подписан Telegram (initData) и допускается только от владельца."""
    uid = webapp.verify_init_data(headers.get("x-telegram-init-data", ""), config.BOT_TOKEN, config.OWNER_ID)
    if not uid:
        return 401, json.dumps({"error": "unauthorized"}).encode()
    if len(body) > MAX_BODY:
        return 413, json.dumps({"error": "too large"}).encode()
    con = db.connect()
    try:
        code, payload = webapp.handle(con, method, path, query, body)
    finally:
        con.close()
    return code, json.dumps(payload, ensure_ascii=False).encode()


def cron(headers, query_string):
    """Vercel Cron присылает `Authorization: Bearer $CRON_SECRET`."""
    want = f"Bearer {config.CRON_SECRET}"
    if not config.CRON_SECRET or not _same(headers.get("authorization", ""), want):
        return 401, json.dumps({"error": "unauthorized"}).encode()
    mode = parse_qs(query_string).get("mode", ["all"])[0]
    if mode not in CRON_MODES:
        return 400, json.dumps({"error": "mode"}).encode()
    if not config.OWNER_ID:
        return 200, json.dumps({"sent": 0, "note": "OWNER_ID не задан"}).encode()
    con = db.connect()
    try:
        sent = bot.run_cron(con, bot.Api(config.BOT_TOKEN), config.OWNER_ID, mode)
    finally:
        con.close()
    return 200, json.dumps({"sent": sent, "mode": mode}).encode()
