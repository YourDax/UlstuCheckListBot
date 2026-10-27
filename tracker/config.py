import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load_env():
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env()

# Часовой пояс группы: сроки в сообщениях — по местному времени, а сервер Vercel живёт по UTC
TZ = ZoneInfo(os.environ.get("TZ_NAME") or "Europe/Ulyanovsk")


def now():
    """Текущее местное время (без tzinfo) — в том же виде, в каком хранятся дедлайны."""
    return datetime.now(TZ).replace(tzinfo=None)


# Telethon (чтение группы под вашим аккаунтом)
API_ID = int(os.environ.get("TG_API_ID") or 0)
API_HASH = os.environ.get("TG_API_HASH", "")
GROUP = os.environ.get("TG_GROUP", "")
BACKFILL = int(os.environ.get("BACKFILL_LIMIT") or 300)
# Уведомления о новых заданиях и дедлайнах в «Избранное» (только для локального watch)
NOTIFY = os.environ.get("NOTIFY_SAVED", "0") == "1"

# База: если задан DATABASE_URL (Postgres, например Neon) — используем её, иначе data/tracker.db
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""

# Telegram-бот на Vercel
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID") or 0)  # числовой id вашего Telegram-аккаунта
APP_URL = os.environ.get("APP_URL") or "https://magchecklistbot.vercel.app"  # адрес Mini App
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
