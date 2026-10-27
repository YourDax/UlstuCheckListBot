import unittest
from datetime import datetime

from tracker import parse

REF = datetime(2026, 10, 27, 10, 0)  # вторник


class Deadline(unittest.TestCase):
    def check(self, text, expected, ref=REF):
        self.assertEqual(parse.find_deadline(text, ref), expected, text)

    def test_numeric(self):
        self.check("Сдать лабу до 15.11", datetime(2026, 11, 15, 23, 59))
        self.check("до 15.11.2026 18:30", datetime(2026, 11, 15, 18, 30))
        self.check("срок 1/12/26", datetime(2026, 12, 1, 23, 59))

    def test_text_month(self):
        self.check("Реферат до 3 декабря в 9:00", datetime(2026, 12, 3, 9, 0))

    def test_year_rollover(self):
        self.check("до 10.01", datetime(2027, 1, 10, 23, 59))

    def test_relative(self):
        self.check("Презентацию сдать завтра 12:00", datetime(2026, 10, 28, 12, 0))
        self.check("Сегодня уже нужно отправить!", datetime(2026, 10, 27, 23, 59))
        self.check("Завтра лекция в 10:00", None)  # расписание, не срок
        self.check("сдать сегодня", datetime(2026, 10, 27, 23, 59))

    def test_weekday(self):
        self.check("Отчёт до пятницы", datetime(2026, 10, 30, 23, 59))
        self.check("к вторнику", datetime(2026, 11, 3, 23, 59))  # не сегодня

    def test_cue_preferred(self):
        self.check("Лекция была 20.10, сдать до 5.11", datetime(2026, 11, 5, 23, 59))

    def test_explicit_input_needs_no_cue(self):
        for text, want in (("20.11 18:00", datetime(2026, 11, 20, 18, 0)),
                           ("завтра 18:00", datetime(2026, 10, 28, 18, 0)),
                           ("пятница 12:00", datetime(2026, 10, 30, 12, 0))):
            self.assertEqual(parse.find_deadline(text, REF, explicit=True), want, text)
        self.assertIsNone(parse.find_deadline("когда-нибудь", REF, explicit=True))

    def test_none_and_invalid(self):
        self.check("Всем привет, завтра пар не будет?".replace("завтра", "потом"), None)
        self.check("версия 31.02", None)
        self.check("[файл: Список группы 09.04.03.xls]", None)  # дата в имени файла
        self.check("Реферат 20.11", datetime(2026, 11, 20, 23, 59))  # без «до», но явно задание


class Task(unittest.TestCase):
    def test_detect(self):
        self.assertTrue(parse.looks_like_task("Лабораторная работа №3 по физике"))
        self.assertFalse(parse.looks_like_task("Завтра на лабораторные подключаются в 10:00"))
        self.assertFalse(parse.looks_like_task("Лекция 11.09.2026 19:30-21:00"))
        self.assertTrue(parse.looks_like_task("что-то", deadline=REF))
        self.assertFalse(parse.looks_like_task("Всем привет, как дела?"))

    def test_subject(self):
        self.assertEqual(parse.guess_subject("Предмет: Базы данных\nЛР 2"), "Базы данных")
        self.assertEqual(parse.guess_subject("#теория_графов ДЗ 4"), "теория графов")


if __name__ == "__main__":
    unittest.main()
