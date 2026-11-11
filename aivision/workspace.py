"""
Рабочее пространство компании ИИ — единый порядок, где всё лежит и как течёт.

Определения (в git): agents/*.md — кто есть кто, роль, отдел, кому подчиняется.
Операции (рантайм, вне git): company/ —
    inbox/      новые запросы на вход (по файлу на запрос)
    tasks/<id>/ активная задача: task.json (цель, исполнитель, статус, зависимости),
                log.md (ход), artifacts/ (файлы шага)
    reports/    отчёты по задачам (итог, что сделано)
    outbox/     готовое для человека
    knowledge/  общая база знаний, которую агенты читают
    state/      рантайм-состояние (правки промтов и пр.)

Каждому агенту workspace.card() говорит ровно: где его роль, где брать задачи,
куда класть отчёт, кому подчиняется и кто рядом по отделу. Один механизм —
чтобы в потоке 24/7 всё шло предсказуемо и без ошибок.
"""

import json
import time
import uuid
from pathlib import Path
from typing import List, Optional

from . import paths
from .agents import Agent, load_agents

DIRS = (paths.INBOX, paths.TASKS, paths.REPORTS, paths.OUTBOX, paths.KNOWLEDGE, paths.STATE)


def ensure():
    """Создать структуру и самоописывающий README, если их ещё нет."""
    paths.AGENTS.mkdir(parents=True, exist_ok=True)
    paths.COMPANY.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
    readme = paths.COMPANY / 'README.md'
    if not readme.exists():
        readme.write_text(_README, encoding='utf-8')


# ── Задачи ───────────────────────────────────────────────────────────────────
def new_task(goal: str, assignee: str = '', parent: str = '', depends_on: Optional[list] = None,
             created_by: str = 'Оркестратор') -> str:
    """Завести задачу → её id. Папка tasks/<id>/ с task.json, log.md, artifacts/."""
    ensure()
    tid = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    d = paths.TASKS / tid
    (d / 'artifacts').mkdir(parents=True, exist_ok=True)
    data = {'id': tid, 'goal': goal, 'assignee': assignee, 'parent': parent,
            'depends_on': depends_on or [], 'status': 'new', 'created_by': created_by,
            'created': time.time(), 'answer': ''}
    _write_task(tid, data)
    (d / 'log.md').write_text(f'# Задача {tid}\n\n- {_ts()} заведена ({created_by}) → {assignee or "не назначен"}\n',
                              encoding='utf-8')
    return tid


def read_task(tid: str) -> Optional[dict]:
    try:
        return json.loads((paths.TASKS / tid / 'task.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def update_task(tid: str, **fields):
    data = read_task(tid) or {'id': tid}
    data.update(fields)
    data['updated'] = time.time()
    _write_task(tid, data)


def log_task(tid: str, line: str):
    p = paths.TASKS / tid / 'log.md'
    with open(p, 'a', encoding='utf-8') as f:
        f.write(f'- {_ts()} {line}\n')


def list_tasks(status: Optional[str] = None) -> List[dict]:
    out = []
    if paths.TASKS.exists():
        for d in sorted(paths.TASKS.glob('*/')):
            t = read_task(d.name)
            if t and (status is None or t.get('status') == status):
                out.append(t)
    return out


_journal_lock = __import__('threading').Lock()


def journal(agent: str, task: str, status: str, answer: str = ''):
    """Единый журнал всех действий команды — company/state/journal.md (потокобезопасно)."""
    ensure()
    line = f'- {_ts()} [{status}] {agent}: {task[:100]}'
    if answer:
        line += f' → {answer[:120]}'
    with _journal_lock, open(paths.STATE / 'journal.md', 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def write_report(tid: str, agent: str, text: str) -> Path:
    ensure()
    p = paths.REPORTS / f'{tid}.md'
    p.write_text(f'# Отчёт по задаче {tid}\n\nИсполнитель: {agent}\nДата: {_ts()}\n\n{text}\n',
                 encoding='utf-8')
    log_task(tid, f'отчёт сдан ({agent})')
    return p


def _write_task(tid: str, data: dict):
    (paths.TASKS / tid).mkdir(parents=True, exist_ok=True)
    (paths.TASKS / tid / 'task.json').write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                                 encoding='utf-8')


def _ts() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ── Где что для конкретного агента ───────────────────────────────────────────
def card(agent: Agent, agents: Optional[List[Agent]] = None) -> str:
    """
    Короткая «карта» агента: где его роль, куда отчитываться, кому подчиняется,
    кто рядом по отделу, где общая база. Подмешивается в его системный промт,
    чтобы он всегда знал своё место в механизме.
    """
    agents = agents if agents is not None else load_agents()
    peers = [a.name for a in agents
             if a.department and a.department == agent.department and a.name != agent.name]
    lines = [
        'ТВОЁ МЕСТО В КОМАНДЕ:',
        f'- Ты: {agent.name}' + (f' (отдел «{agent.department}»)' if agent.department else ''),
        f'- Подчиняешься: {agent.reports_to or "Оркестратор"}',
        f'- Рядом по отделу: {", ".join(peers) if peers else "—"}',
        f'- Отчёт по задаче клади в: company/reports/<id задачи>.md (workspace.write_report)',
        f'- Общая база знаний: company/knowledge/',
        '- Не твоя задача — верни её наверх тому, кому подчиняешься.',
    ]
    return '\n'.join(lines)


_README = """# Рабочее пространство компании ИИ

Определения команды — в `../agents/*.md` (кто есть кто, роль, отдел, кому подчиняется).
Здесь — операции (рантайм, в git не хранятся):

- `inbox/` — новые запросы на вход.
- `tasks/<id>/` — активная задача: `task.json` (цель, исполнитель, статус, зависимости),
  `log.md` (ход работы), `artifacts/` (файлы шага).
- `reports/<id>.md` — отчёты по задачам.
- `outbox/` — готовое для человека.
- `knowledge/` — общая база знаний, которую агенты читают.
- `state/` — рантайм-состояние (напр. `overrides.json` — правки системных промтов от оркестратора).

Поток: запрос → оркестратор ставит задачу (`new_task`) и назначает агента → агент работает
(его роль + «карта места» из `workspace.card`) → отчёт (`write_report`) → оркестратор сводит.
"""
