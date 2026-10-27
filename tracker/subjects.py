"""Предмет по ключевым словам (фамилия преподавателя, название) из subjects.json."""
import json

from .config import ROOT
from .parse import guess_subject

PATH = ROOT / "subjects.json"


def load(path=None):
    """{предмет: [ключевые слова]}; если файла нет или он битый — пусто."""
    try:
        data = json.loads((path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: [w for w in v if isinstance(w, str) and w] for k, v in data.items()
            if isinstance(v, list)}


def match(text, mapping=None):
    """Предмет, ключевых слов которого в тексте больше всего (при равенстве — первый в файле)."""
    mapping = load() if mapping is None else mapping
    low = text.lower()
    best, best_hits = None, 0
    for subject, words in mapping.items():
        hits = sum(1 for w in words if w.lower() in low)
        if hits > best_hits:
            best, best_hits = subject, hits
    return best


# общие темы форума — это не предметы
GENERAL_TOPICS = {"объявления", "расписание", "чат", "general", "общий", "общее"}


def topic_subject(title, mapping=None):
    """Предмет по названию темы форума: «МНП(Долинина)» -> имя из subjects.json, иначе сам заголовок."""
    title = (title or "").strip()
    if not title or title.lower() in GENERAL_TOPICS:
        return None
    return match(title, mapping) or title


def guess(text, mapping=None, topic=None):
    """Явное «Предмет: …» / #хештег из сообщения, потом тема форума, потом subjects.json."""
    return guess_subject(text) or topic_subject(topic, mapping) or match(text, mapping)
