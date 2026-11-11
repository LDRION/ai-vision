"""
Рецепт сценария — записанная удачная последовательность действий, чтобы в
следующий раз НЕ разбирать каждый шаг заново через Siberi, а проиграть сразу,
подставив новые данные (другой чат, другой человек, другой текст). Если на
каком-то шаге рецепт не сошёлся — управление возвращается в полный пошаговый
разбор (aivision.loop). Самообучение: один раз показали — дальше быстро.

Храним по сценарию в recipes/<id>.json (личное, в git не идёт). Цель замечаем,
чтобы при новом промте понять, что это та же задача с другими параметрами.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from .paths import BASE

RECIPES = BASE / 'recipes'


@dataclass
class Step:
    action: str                       # click / type / key / drag / stroke / scroll / …
    target_text: str = ''             # текст/имя элемента цели — ищем его на экране заново
    target_role: str = ''             # роль элемента (кнопка, ссылка, поле ввода…)
    text: str = ''                    # что печатать (type) — переменная часть
    keys: str = ''
    scroll: int = 0
    seconds: float = 0.0
    points: Optional[list] = None
    to_text: str = ''                 # цель drag «куда», если это элемент
    to_rel: Optional[list] = None     # либо доля экрана [fx, fy] для drag без элемента
    rel: Optional[list] = None        # доля экрана [fx, fy] для действия без метки (сетка)
    change: str = ''                  # как отреагировал экран (ожидание для проверки)


@dataclass
class Recipe:
    scenario_id: str
    goal: str                         # цель, на которой рецепт записан
    steps: List[Step] = field(default_factory=list)
    created: float = 0.0

    def path(self) -> Path:
        return RECIPES / f'{self.scenario_id}.json'

    def save(self):
        RECIPES.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = self.path().with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path())


def load(scenario_id: str) -> Optional[Recipe]:
    p = RECIPES / f'{scenario_id}.json'
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None
    steps = [Step(**{k: v for k, v in s.items() if k in Step.__dataclass_fields__})
             for s in data.get('steps', [])]
    return Recipe(scenario_id=data.get('scenario_id', scenario_id), goal=data.get('goal', ''),
                  steps=steps, created=data.get('created', 0.0))


def delete(scenario_id: str):
    p = RECIPES / f'{scenario_id}.json'
    if p.exists():
        p.unlink()


# Действия, которые осмысленно проигрывать по рецепту (у done/fail/wait/none цели нет)
REPLAYABLE = {'click', 'double_click', 'right_click', 'hover', 'long_press', 'type', 'key',
              'scroll', 'drag', 'stroke'}


def from_run(scenario_id: str, goal: str, run_dir: Path) -> Optional[Recipe]:
    """
    Собрать рецепт из удачного прогона: по шагам step_NN/action.json, в порядке
    выполнения. Каждый шаг — действие + как опознать цель (текст/роль элемента
    или доля экрана) + ожидаемая реакция экрана.
    """
    import time
    steps = []
    for d in sorted(run_dir.glob('step_*')):
        p = d / 'action.json'
        if not p.exists():
            continue
        try:
            a = json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            continue
        if a.get('action') not in REPLAYABLE:
            continue
        mark = a.get('mark') or {}
        w, h = a.get('img_size') or [1, 1]
        rel = None
        if not mark and a.get('x') is not None and w and h:
            rel = [round(a['x'] / w, 4), round(a['y'] / h, 4)]
        to = a.get('to') or {}
        to_rel = None
        if a['action'] == 'drag' and to.get('x') is not None and w and h:
            to_rel = [round(to['x'] / w, 4), round(to['y'] / h, 4)]
        steps.append(Step(
            action=a['action'], target_text=mark.get('text', ''), target_role=mark.get('role', ''),
            text=a.get('text', ''), keys=a.get('keys', ''), scroll=a.get('scroll', 0),
            seconds=a.get('seconds', 0), points=a.get('points'), to_rel=to_rel, rel=rel,
            change=_change_of(d, run_dir),
        ))
    if not steps:
        return None
    r = Recipe(scenario_id=scenario_id, goal=goal, steps=steps, created=time.time())
    r.save()
    return r


def _change_of(step_dir: Path, run_dir: Path) -> str:
    """Реакция экрана на шаг — из result.json (история), по номеру шага."""
    try:
        n = int(step_dir.name.split('_')[1])
        res = json.loads((run_dir / 'result.json').read_text(encoding='utf-8'))
        line = next((h for h in res.get('history', []) if h.strip().startswith(f'{n}.')), '')
        return line.split('→', 1)[1].strip() if '→' in line else ''
    except (ValueError, OSError, IndexError):
        return ''
