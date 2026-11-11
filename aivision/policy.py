"""
Ритм компании — «без фанатизма». Сколько чего можно в сутки, чтобы система
не заспамила соцсети и не перегрелась. Например: максимум 1 пост и 1 видео в
день. Счётчики ведём по журналу действий в company/state/policy.json.

  allow(action)   → можно ли сейчас (не превышен ли дневной лимит)
  record(action)  → отметить выполненное действие
  limits          → дневные лимиты по типам действий (правятся под себя)
"""

import json
import time
from typing import Dict

from . import paths

# Дневные лимиты по типам действий. 0 — запрещено, отсутствие ключа — без лимита.
LIMITS: Dict[str, int] = {
    'publish': 1,       # публикация поста/видео в соцсети/площадку
    'video': 1,         # готовый ролик
    'email_campaign': 1,
}

_FILE = lambda: paths.STATE / 'policy.json'


def _today() -> str:
    return time.strftime('%Y-%m-%d')


def _load() -> dict:
    try:
        return json.loads(_FILE().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(data: dict):
    paths.STATE.mkdir(parents=True, exist_ok=True)
    _FILE().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def count_today(action: str) -> int:
    data = _load()
    day = data.get(_today(), {})
    return int(day.get(action, 0))


def remaining(action: str) -> int:
    limit = LIMITS.get(action)
    if limit is None:
        return 10 ** 9                      # без лимита
    return max(0, limit - count_today(action))


def allow(action: str) -> bool:
    return remaining(action) > 0


def record(action: str, n: int = 1):
    data = _load()
    data.setdefault(_today(), {})
    data[_today()][action] = count_today(action) + n
    # не копим историю вечно — держим только последние ~14 дней
    days = sorted(data)
    for d in days[:-14]:
        data.pop(d, None)
    _save(data)


# Какие задачи считаются публикацией/видео — чтобы лимит ловился автоматически.
def action_of(task: str) -> str:
    low = (task or '').lower()
    if any(w in low for w in ('выложи', 'опубликуй', 'запость', 'постни', 'загрузи на youtube',
                              'publish', 'post ')):
        return 'publish'
    if any(w in low for w in ('смонтируй', 'собери ролик', 'видео', 'шортс')):
        return 'video'
    return ''
