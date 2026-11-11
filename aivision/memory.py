"""
Опыт агента — уроки из прошлых задач, по приложениям. Так система дообучает
сама себя: после задачи Siberi разбирает историю шагов (что не сработало, что
сработало вместо этого) и формулирует короткие правила. В следующих задачах
уроки того приложения, чьё окно сейчас активно, подмешиваются в промт —
агент сразу берёт рабочий способ вместо перебора.

Хранится локально в experience/lessons.json (не в git: там названия окон).
"""

import json
import re
import time
from pathlib import Path
from typing import Callable, List

from . import siberi
from .agent import parse_json
from .prompt import REFLECT_SYSTEM, reflect_prompt

from .paths import EXPERIENCE as DEFAULT_PATH
SIMILAR = 0.6
MAX_APP, MAX_GENERAL = 6, 3


def _words(text: str) -> set:
    return set(re.findall(r'[a-zа-яё0-9]+', text.lower().replace('ё', 'е')))


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= SIMILAR


class Memory:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.lessons: List[dict] = []
        if self.path.exists():
            try:
                self.lessons = json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                self.lessons = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.lessons, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def relevant(self, title: str) -> List[str]:
        """Уроки приложения, чьё имя есть в заголовке окна, + немного общих."""
        t = (title or '').lower()
        rank = lambda l: (l.get('confirmed', 1), l.get('ts', 0))
        app = sorted((l for l in self.lessons if l['app'] and l['app'].lower() in t), key=rank, reverse=True)
        general = sorted((l for l in self.lessons if not l['app']), key=rank, reverse=True)
        return [l['text'] for l in app[:MAX_APP] + general[:MAX_GENERAL]]

    def add(self, lessons: List[dict]) -> List[dict]:
        """Новые — дописываем; похожий урок того же приложения — подтверждаем, а не дублируем."""
        added = []
        for les in lessons:
            text, app = les['text'].strip(), (les.get('app') or '').strip()
            same = next((l for l in self.lessons if l['app'].lower() == app.lower()
                         and _similar(l['text'], text)), None)
            if same:
                same['confirmed'] = same.get('confirmed', 1) + 1
                same['ts'] = time.time()
            else:
                item = {'app': app, 'text': text, 'confirmed': 1, 'ts': time.time()}
                self.lessons.append(item)
                added.append(item)
        if lessons:
            self._save()
        return added

    def reflect(self, goal: str, status: str, history: List[str], answer: str,
                log: Callable[[str], None] = print, hints: List[str] = (),
                titles: List[str] = ()) -> List[dict]:
        """Разбор задачи Siberi → уроки [{'app', 'text'}] (уже сохранённые)."""
        known = list(dict.fromkeys(t for title in titles for t in self.relevant(title)))
        try:
            raw = siberi.ask(REFLECT_SYSTEM, reflect_prompt(goal, status, history, answer, hints, titles, known))
            items = parse_json(raw).get('lessons') or []
        except Exception as e:
            log(f'  ⚠️ разбор опыта не удался: {e}')
            return []
        lessons = []
        for it in items[:3]:
            if isinstance(it, str):
                it = {'app': '', 'text': it}
            text = str(it.get('text') or '').strip()
            if 10 <= len(text) <= 240:
                lessons.append({'app': str(it.get('app') or '').strip()[:60], 'text': text})
        self.add(lessons)
        return lessons
