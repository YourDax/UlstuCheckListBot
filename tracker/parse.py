"""Разбор текста сообщения: похоже ли на задание, какой дедлайн, какой предмет."""
import re
from datetime import datetime, timedelta

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
WEEKDAYS = [
    ("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3),
    ("пятниц", 4), ("суббот", 5), ("воскресень", 6),
]
DEFAULT_TIME = (23, 59)
_L = r"[а-яёa-z]"  # «буква»: подчёркивание и цифры словом не считаем

_TIME = r"(?:[ ,]*(?:в\s*)?(\d{1,2}):(\d{2}))?"
_NUM = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?!\d)" + _TIME)
_TXT = re.compile(
    r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?" + _TIME, re.I)
_REL = re.compile(r"\b(сегодня|завтра|послезавтра)\b" + _TIME, re.I)
_WD = re.compile(
    r"\b(?:(?:до|к|в|во|на)\s+)?(понедельник\w*|вторник\w*|сред[аыуе]\b|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*)" + _TIME, re.I)

# слова перед датой, которые делают её дедлайном
_CUE_BEFORE = re.compile(
    rf"(?<!{_L})(до|к|дедлайн|срок|сдать|сдача|сдаём|сдаем|не позже|отправить)\W{{0,3}}$", re.I)
# после «сегодня/завтра/в пятницу» — глагол, говорящий, что это срок, а не расписание
_CUE_AFTER = re.compile(
    r"^\W{0,12}(?:уже\s+|ещё\s+|еще\s+)?(нужно|надо|необходимо|отправ\w+|сдав\w+|сдать|сдаём|сдаем|высыла\w+)",
    re.I)

# однозначные признаки задания (лекции, пары, лабы «вообще» сюда не входят — это расписание)
_STRONG = re.compile(
    r"задани|сдать|сдача|сдаём|сдаем|отправить|загрузить|загружать|выполнить|подобрать|"
    r"реферат|курсов\w*\s+(?:работ|проект)|тем\w*\s+курсов|(?<![а-яёa-z])дз(?![а-яёa-z])|домашн|"
    r"контрольн|зач[её]т|экзамен|дедлайн|отч[её]т|эссе|коллоквиум|"
    r"лабораторн\w*[\s_]+работ\w*[\s_]*№?\s*\d|(?<![а-яёa-z])л/р\s*№?\s*\d", re.I)
_EVENT = re.compile(r"лекци|конференц|zoom|созвон|телемост|подключа", re.I)
_FILE_LINE = re.compile(r"^\[файл:.*\]$", re.M)


def _hm(h, m):
    return (int(h), int(m)) if h is not None else DEFAULT_TIME


def _build(y, mo, d, hm, ref):
    """Дата без года: берём год сообщения, а если дата уже прошла — следующий."""
    h, mi = hm
    try:
        if y is not None:
            if not 2020 <= y <= 2100:
                return None
            return datetime(y, mo, d, h, mi)
        dt = datetime(ref.year, mo, d, h, mi)
        if dt.date() < ref.date() - timedelta(days=1):
            dt = datetime(ref.year + 1, mo, d, h, mi)
        return dt
    except ValueError:
        return None


def _year(s):
    if s is None:
        return None
    y = int(s)
    return y + 2000 if y < 100 else y


def find_deadline(text, ref, explicit=False):
    """Возвращает datetime дедлайна или None. ref — время публикации сообщения.

    explicit=True — текст целиком и есть дата, берём первую найденную.

    Дата считается дедлайном, если перед ней стоит «до / сдать / срок / дедлайн …»
    (для «завтра», «в пятницу» — ещё если после идёт «нужно отправить»). Дата без таких слов
    берётся только в сообщении с явным признаком задания и без признаков события."""
    text = _FILE_LINE.sub("", text)  # даты в именах файлов — не сроки
    found = []  # (позиция, конец, datetime, абсолютная ли дата)
    for m in _NUM.finditer(text):
        dt = _build(_year(m[3]), int(m[2]), int(m[1]), _hm(m[4], m[5]), ref)
        if dt:
            found.append((m.start(), m.end(), dt, True))
    for m in _TXT.finditer(text):
        dt = _build(_year(m[3]), MONTHS[m[2].lower()], int(m[1]), _hm(m[4], m[5]), ref)
        if dt:
            found.append((m.start(), m.end(), dt, True))
    for m in _REL.finditer(text):
        shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[m[1].lower()]
        h, mi = _hm(m[2], m[3])
        day = (ref + timedelta(days=shift)).replace(hour=h, minute=mi, second=0, microsecond=0)
        found.append((m.start(), m.end(), day, False))
    for m in _WD.finditer(text):
        wd = next(n for stem, n in WEEKDAYS if m[1].lower().startswith(stem))
        h, mi = _hm(m[2], m[3])
        day = (ref + timedelta(days=(wd - ref.weekday()) % 7 or 7)).replace(
            hour=h, minute=mi, second=0, microsecond=0)
        found.append((m.start(1), m.end(), day, False))  # позиция слова: «до» остаётся перед ним
    found.sort(key=lambda x: x[0])
    if explicit:  # дату ввели вручную («/add … | 20.11 18:00») — слова «до/сдать» не требуются
        return found[0][2] if found else None
    for pos, end, dt, absolute in found:
        if _CUE_BEFORE.search(text[max(0, pos - 14):pos]):
            return dt
        if not absolute and _CUE_AFTER.search(text[end:end + 30]):  # «сегодня нужно отправить»
            return dt
    if _STRONG.search(text) and not _EVENT.search(text):
        for _, _, dt, absolute in found:
            if absolute:
                return dt
    return None


def looks_like_task(text, deadline=None):
    return deadline is not None or bool(_STRONG.search(text))


def guess_subject(text):
    m = re.search(r"(?:предмет|дисциплина)\s*[:\-–]\s*(.+)", text, re.I)
    if m:
        return m[1].strip()[:60]
    m = re.search(r"#([\w]+)", text)
    return m[1].replace("_", " ") if m else None


def make_title(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # строка «Предмет: …» или одни хештеги — не заголовок, если есть что-то ещё
    useful = [l for l in lines
              if not re.match(r"(предмет|дисциплина)\s*[:\-–]", l, re.I)
              and not re.fullmatch(r"(#\w+\s*)+", l)]
    for line in useful or lines:
        return line if len(line) <= 100 else line[:97] + "..."
    return "(без названия)"
