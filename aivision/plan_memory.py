"""
Память планов оркестрации — самообучение на уровне команды. Удачный план
(кто какие шаги делал и в каком порядке) запоминается по запросу. На похожий
запрос оркестратор берёт готовый план сразу, не тратя обращение к Siberi на
повторное планирование. Это ускоряет и делает поведение стабильным.

Как рецепты для сценариев, только для всей команды. Хранится в
company/state/plans/*.json (рантайм, вне git).

  recall(request, agents)   → план (List[Task]) для похожего запроса или None
  remember(request, steps)  → сохранить удачный план
"""

import json
import re
import time
from pathlib import Path
from typing import List, Optional

from . import paths

SIMILAR = 0.75          # порог похожести запросов (0..1): только явно повторные берём из памяти


def _dir() -> Path:
    return paths.STATE / 'plans'


def _words(text: str) -> set:
    return set(re.findall(r'[a-zа-яё0-9]+', (text or '').lower().replace('ё', 'е')))


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if (wa or wb) else 0.0


def remember(request: str, steps: List) -> Path:
    """Сохранить удачный план задачи."""
    _dir().mkdir(parents=True, exist_ok=True)
    data = {'request': request, 'created': time.time(),
            'steps': [{'agent': (s.agent.name if s.agent else s.agent_name),
                       'task': s.task, 'depends_on': s.depends_on} for s in steps]}
    p = _dir() / f'{int(time.time() * 1000)}.json'
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    return p


def _load_all() -> List[dict]:
    d = _dir()
    out = []
    if d.exists():
        for p in d.glob('*.json'):
            try:
                out.append(json.loads(p.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
    return out


def recall(request: str, agents: List, threshold: float = SIMILAR) -> Optional[List]:
    """Найти план для похожего запроса. → List[Task] или None."""
    from .agents import find_agent
    from .orchestrator import Task
    best, best_score = None, threshold
    for plan in _load_all():
        score = _similar(request, plan.get('request', ''))
        if score >= best_score:
            best, best_score = plan, score
    if not best:
        return None
    steps = []
    for s in best['steps']:
        name = s.get('agent', '')
        steps.append(Task(agent=find_agent(name, agents), agent_name=name,
                          task=s.get('task', ''), depends_on=s.get('depends_on', []) or []))
    # все агенты плана должны существовать сейчас — иначе план устарел
    if any(s.agent is None for s in steps):
        return None
    return steps
