"""
Где хранить пользовательские данные (сценарии, опыт, прогоны, .env).

В обычном запуске — в корне проекта. В собранном exe (PyInstaller) __file__
указывает внутрь временной распаковки, писать туда нельзя, поэтому данные
кладём рядом с самим exe. Один источник правды для всех модулей.
"""

import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, 'frozen', False):               # запущены из собранного exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent   # корень проекта


BASE = base_dir()
RUNS = BASE / 'runs'
SCENARIOS = BASE / 'scenarios' / 'scenarios.json'
EXPERIENCE = BASE / 'experience' / 'lessons.json'
ICONS = BASE / 'experience' / 'icons'          # словарь опознанных иконок (вырезки экрана — локально)
ENV_FILE = BASE / '.env'

# ── Оркестрация ──────────────────────────────────────────────────────────────
# Определения (что за агенты, их роли) — в git, версионируются.
AGENTS = BASE / 'agents'
# Реестр ссылок и данных проектов (каналы, аккаунты, панели) — в git, БЕЗ секретов.
RESOURCES = BASE / 'resources'
# Операции (входящие, задачи, отчёты, состояние) — рантайм, 24/7, вне git.
COMPANY = BASE / 'company'
INBOX = COMPANY / 'inbox'           # новые запросы на вход
TASKS = COMPANY / 'tasks'           # активные задачи: по папке на задачу
REPORTS = COMPANY / 'reports'       # отчёты агентов по задачам
OUTBOX = COMPANY / 'outbox'         # готовое для человека
KNOWLEDGE = COMPANY / 'knowledge'   # общая база знаний, которую агенты читают
STATE = COMPANY / 'state'           # рантайм-состояние (очереди, курсоры, правки промтов)
OVERRIDES = STATE / 'overrides.json'
