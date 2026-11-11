"""
Сценарии лаунчера: сохранённые задачи агента со своими настройками.

Сценарий — это цель («зайди в почту и открой последнее письмо»), подсказки,
выбор браузера (или «по умолчанию»), стартовый URL и параметры запуска
(лимит шагов, скорость, амплитуда шума, уточнение zoom). Пользователь копит
их много; запускается по одному за раз.

Хранится локально в scenarios/scenarios.json (в git не попадает: цели и
подсказки — личные). Схема простая и расширяемая: незнакомые поля не трогаем.
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from .paths import SCENARIOS as STORE

DEFAULT_BROWSER = ''          # пусто = браузер по умолчанию из настроек лаунчера


@dataclass
class Scenario:
    name: str = 'Новый сценарий'
    goal: str = ''
    hints: List[str] = field(default_factory=list)
    browser: str = DEFAULT_BROWSER       # имя браузера или '' (по умолчанию)
    start_url: str = ''                  # открыть перед запуском; '' — ничего не открывать
    incognito: bool = False              # приватное окно: без закладок и истории в снимках
    max_steps: int = 15
    speed: float = 1.0                   # множитель скорости движения
    noise: float = 1.0                   # множитель бокового шума траектории (0 — ровно)
    refine: bool = True                  # шаг уточнения zoom для целей без метки
    monitor: int = 0                     # 0 — авто (см. launcher), иначе номер монитора mss
    learn: bool = True                   # читать и пополнять опыт
    record: bool = True                  # писать видео экрана для таймлайна
    fast_replay: bool = True             # проигрывать рецепт без пошагового разбора
    mode: str = ''                       # '' — обычный экран (поле оставлено для совместимости сохранённых сценариев)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    last_run: Optional[str] = None       # папка последнего прогона (runs/<время>)

    def touch(self):
        self.updated = time.time()


class Store:
    """Загрузка/сохранение списка сценариев + настройки лаунчера (браузер по умолчанию)."""

    def __init__(self, path: Path = STORE):
        self.path = Path(path)
        self.scenarios: List[Scenario] = []
        self.default_browser: str = ''
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return
        self.default_browser = data.get('default_browser', '')
        fields = Scenario.__dataclass_fields__
        self.scenarios = [Scenario(**{k: v for k, v in s.items() if k in fields})
                          for s in data.get('scenarios', [])]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'default_browser': self.default_browser,
                   'scenarios': [asdict(s) for s in self.scenarios]}
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    def get(self, sid: str) -> Optional[Scenario]:
        return next((s for s in self.scenarios if s.id == sid), None)

    def add(self, scenario: Optional[Scenario] = None) -> Scenario:
        s = scenario or Scenario()
        self.scenarios.append(s)
        self.save()
        return s

    def update(self, scenario: Scenario):
        scenario.touch()
        self.save()

    def remove(self, sid: str):
        self.scenarios = [s for s in self.scenarios if s.id != sid]
        self.save()

    def move(self, sid: str, delta: int) -> bool:
        """Сдвинуть сценарий в списке на delta (−1 вверх, +1 вниз). → удалось ли."""
        i = next((k for k, s in enumerate(self.scenarios) if s.id == sid), None)
        j = None if i is None else i + delta
        if i is None or j is None or not (0 <= j < len(self.scenarios)):
            return False
        self.scenarios[i], self.scenarios[j] = self.scenarios[j], self.scenarios[i]
        self.save()
        return True

    def duplicate(self, sid: str) -> Optional[Scenario]:
        src = self.get(sid)
        if not src:
            return None
        clone = Scenario(**{**asdict(src), 'id': uuid.uuid4().hex[:8],
                            'name': f'{src.name} (копия)', 'last_run': None,
                            'created': time.time(), 'updated': time.time()})
        return self.add(clone)


if __name__ == '__main__':
    store = Store()
    print(f'{len(store.scenarios)} сценариев, браузер по умолчанию: {store.default_browser or "(системный)"}')
    for s in store.scenarios:
        print(f'  [{s.id}] {s.name} · {s.browser or "по умолчанию"} · {s.max_steps} шагов')
