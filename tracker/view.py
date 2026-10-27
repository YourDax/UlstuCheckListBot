from datetime import datetime

from . import config
from .db import ISO

STATUS = {"new": "·", "doing": "▶", "done": "✓"}


def rel(deadline, now=None):
    if not deadline:
        return "без срока"
    now = now or config.now()
    secs = (datetime.strptime(deadline, ISO) - now).total_seconds()
    if secs < 0:
        return "ПРОСРОЧЕНО"
    if secs < 3600:
        return f"через {int(secs // 60)} мин"
    if secs < 86400:
        return f"через {int(secs // 3600)} ч"
    return f"через {int(secs // 86400)} дн"


DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def when(deadline):
    if not deadline:
        return "—"
    d = datetime.strptime(deadline, ISO)
    return f"{d:%d.%m} {DAYS[d.weekday()]} {d:%H:%M}"


def line(t):
    subj = f" [{t['subject']}]" if t["subject"] else ""
    return f"#{t['id']} {STATUS[t['status']]} {when(t['deadline'])} ({rel(t['deadline'])}){subj} {t['title']}"


def table(tasks):
    if not tasks:
        return "Заданий нет."
    return "\n".join(line(t) for t in tasks)


def card(t):
    parts = [line(t)]
    if t["link"]:
        parts.append(t["link"])
    if t["body"] and t["body"].strip() != t["title"]:
        parts += ["", t["body"].strip()]
    return "\n".join(parts)
