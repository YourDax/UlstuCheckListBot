import json
import tempfile
import unittest
from pathlib import Path

from tracker import subjects

MAP = {
    "Методология": ["Долинин", "методологи"],
    "ИИС": ["Чекин", "ИИС"],
}


class Subjects(unittest.TestCase):
    def test_match_by_teacher_case_insensitive(self):
        self.assertEqual(subjects.match("пары ДОЛИНИНОЙ будут очно", MAP), "Методология")  # окончания и регистр
        self.assertEqual(subjects.match("Практика у Долининой завтра", MAP), "Методология")
        self.assertIsNone(subjects.match("Пары Дудрина", MAP))

    def test_most_hits_wins_and_first_on_tie(self):
        self.assertEqual(subjects.match("Чекина: ИИС и методология", MAP), "ИИС")  # 2 против 1
        self.assertEqual(subjects.match("Чекина и Долинина", MAP), "Методология")  # 1:1 — первый

    def test_explicit_subject_beats_file(self):
        self.assertEqual(subjects.guess("Предмет: Физика\nДолинина", MAP), "Физика")
        self.assertEqual(subjects.guess("Долинина, задание 3", MAP), "Методология")
        self.assertIsNone(subjects.guess("Всем привет", MAP))

    def test_topic_title_becomes_subject(self):
        self.assertEqual(subjects.topic_subject("МНП(Долинина)", MAP), "Методология")  # по фамилии
        self.assertEqual(subjects.topic_subject("Биотехнологии (Ленгесова Н А)", MAP),
                         "Биотехнологии (Ленгесова Н А)")  # нет в файле — берём заголовок
        for general in ("Объявления", "Расписание", "Чат", None, ""):
            self.assertIsNone(subjects.topic_subject(general, MAP))

    def test_priority_explicit_then_topic_then_keywords(self):
        self.assertEqual(subjects.guess("Предмет: Физика", MAP, topic="МНП(Долинина)"), "Физика")
        self.assertEqual(subjects.guess("Чекина: сдать лабу", MAP, topic="МНП(Долинина)"), "Методология")
        self.assertEqual(subjects.guess("Чекина: сдать лабу", MAP, topic="Объявления"), "ИИС")

    def test_missing_or_broken_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(subjects.load(Path(d) / "нет.json"), {})
            bad = Path(d) / "bad.json"
            bad.write_text("{ не json", encoding="utf-8")
            self.assertEqual(subjects.load(bad), {})
            ok = Path(d) / "ok.json"
            ok.write_text(json.dumps({"A": ["x"], "B": "не список"}), encoding="utf-8")
            self.assertEqual(subjects.load(ok), {"A": ["x"]})


if __name__ == "__main__":
    unittest.main()
