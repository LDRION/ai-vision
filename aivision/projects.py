"""
Реестр проектов — что ведёт «единый мозг». Каждый проект: где он живёт (локальная
папка, SSH-хост, URL) и что это. Оркестратор и пульс смотрят по всем проектам,
ищут слабые места, ставят задачи. Хранится в company/projects.json (рантайм).

  projects()              → список проектов
  add(name, kind, where)  → зарегистрировать проект
"""

import json
from dataclasses import asdict, dataclass
from typing import List

from . import paths

STORE = paths.COMPANY / 'projects.json'


@dataclass
class Project:
    name: str
    kind: str = 'local'          # local | ssh | web
    where: str = ''              # путь к папке / host SSH / URL
    note: str = ''


DEFAULTS = [
    Project('antianti', 'local', str(paths.BASE), 'Движок зрения + оркестрация (этот проект)'),
    Project('Откликс', 'ssh', 'otkliks', 'Сервис откликов (hhpilot) — /var/www/hhpilot'),
    Project('siberi.online', 'ssh', 'siberi', 'Мозг Siberi — /var/www/siberi'),
]


def projects() -> List[Project]:
    if not STORE.exists():
        save(DEFAULTS)
        return list(DEFAULTS)
    try:
        data = json.loads(STORE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return list(DEFAULTS)
    return [Project(**{k: v for k, v in p.items() if k in Project.__dataclass_fields__})
            for p in data.get('projects', [])]


def save(items: List[Project]):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({'projects': [asdict(p) for p in items]}, ensure_ascii=False, indent=1),
                     encoding='utf-8')


def add(name: str, kind: str = 'local', where: str = '', note: str = '') -> Project:
    items = projects()
    items = [p for p in items if p.name != name]
    p = Project(name, kind, where, note)
    items.append(p)
    save(items)
    return p


def summary() -> str:
    return '\n'.join(f'- {p.name} [{p.kind}] {p.where or "(не задан)"} — {p.note}' for p in projects())
