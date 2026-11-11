"""
Агенты-специалисты для оркестрации. Каждый агент описан markdown-манифестом
в agents/<имя>.md: YAML-заголовок (имя, что умеет, права, какими инструментами
пользуется) + тело = системная роль (кто он и как работает). Оркестратор
(aivision.orchestrator) по запросу выбирает подходящего агента и раскладывает
задачу на подзадачи между ними.

Формат манифеста:

    ---
    name: Браузерный агент
    description: Задачи в вебе — открыть сайт, заполнить форму, написать сообщение
    capabilities: [screen, browser]      # что умеет: screen (видит экран), hearing (Groq), …
    tools: [scenario]                    # чем пользуется: scenario — наш движок управления экраном
    app: Google Chrome                   # предпочитаемое приложение (необязательно)
    ---
    Ты специалист по задачам в браузере. …системная роль…
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

from .paths import AGENTS as AGENTS_DIR
from .paths import OVERRIDES                      # правки системных промтов на лету (от оркестратора)


@dataclass
class Agent:
    name: str
    description: str = ''
    capabilities: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    app: str = ''
    department: str = ''                  # к какому отделу относится
    reports_to: str = ''                  # имя руководителя (лид отдела / главный оркестратор)
    prompt: str = ''                      # тело манифеста — системная роль
    source: Optional[Path] = None

    @property
    def slug(self) -> str:
        return (self.source.stem if self.source else _slugify(self.name)).strip()

    @property
    def is_lead(self) -> bool:
        return 'orchestrate' in self.tools

    def summary(self) -> str:
        caps = ', '.join(self.capabilities) or '—'
        dept = f' · отдел «{self.department}»' if self.department else ''
        return f'{self.name}: {self.description} [умеет: {caps}]{dept}'

    def effective_prompt(self) -> str:
        """Роль с учётом правки от оркестратора (если задана для этого агента)."""
        ov = _load_overrides().get(self.slug)
        if not ov:
            return self.prompt
        if ov.get('replace'):
            return ov['text']
        return f'{self.prompt}\n\n[Указание оркестратора]: {ov["text"]}'


def _slugify(name: str) -> str:
    s = re.sub(r'[^\w]+', '-', (name or 'agent').strip().lower(), flags=re.UNICODE).strip('-')
    return s or 'agent'


def _split_front_matter(text: str):
    """markdown с YAML-заголовком между --- → (meta: dict, body: str)."""
    text = text.lstrip('﻿')
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), parts[2].strip()
    return {}, text.strip()


def _as_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.replace(',', ' ').split() if s.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def parse_agent(text: str, source: Optional[Path] = None) -> Agent:
    meta, body = _split_front_matter(text)
    name = str(meta.get('name') or (source.stem if source else 'Агент')).strip()
    return Agent(
        name=name, description=str(meta.get('description', '')).strip(),
        capabilities=_as_list(meta.get('capabilities')), tools=_as_list(meta.get('tools')),
        app=str(meta.get('app', '')).strip(), department=str(meta.get('department', '')).strip(),
        reports_to=str(meta.get('reports_to', '')).strip(), prompt=body, source=source,
    )


def to_markdown(spec: dict) -> str:
    """Спецификация агента (словарь) → текст манифеста .md с YAML-заголовком."""
    meta = {k: spec[k] for k in ('name', 'description', 'capabilities', 'tools', 'app',
                                 'department', 'reports_to') if spec.get(k)}
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
    body = str(spec.get('prompt', '')).strip()
    return f'---\n{front}\n---\n{body}\n'


def write_agent(spec: dict, folder: Path = AGENTS_DIR) -> Path:
    """Создать/обновить манифест агента из спецификации. Так оркестратор заводит сотрудников."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    slug = spec.get('slug') or _slugify(spec.get('name', 'agent'))
    path = folder / f'{slug}.md'
    path.write_text(to_markdown(spec), encoding='utf-8')
    return path


def load_agent(path: Path) -> Agent:
    return parse_agent(Path(path).read_text(encoding='utf-8'), Path(path))


def load_agents(folder: Path = AGENTS_DIR) -> List[Agent]:
    """Все агенты из папки (agents/*.md), по имени."""
    folder = Path(folder)
    if not folder.exists():
        return []
    agents = []
    for p in sorted(folder.glob('*.md')):
        try:
            agents.append(load_agent(p))
        except OSError:
            continue
    agents.sort(key=lambda a: a.name.lower())
    return agents


def find_agent(name: str, agents: List[Agent]) -> Optional[Agent]:
    n = (name or '').strip().lower()
    return next((a for a in agents if a.name.lower() == n or a.slug.lower() == n), None)


# ── Правки системных промтов на лету (оркестратор меняет роль под обстоятельства) ──
# Путь можно подменить (в т.ч. для тестов): set_override_path(...).
_overrides_path = OVERRIDES


def set_override_path(path: Path):
    global _overrides_path
    _overrides_path = Path(path)


def _load_overrides() -> dict:
    try:
        return json.loads(_overrides_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def set_override(slug: str, text: str, replace: bool = False):
    """
    Задать правку системного промта агента. replace=False — дописать указание к роли,
    True — заменить роль целиком. Пустой text — снять правку.
    """
    data = _load_overrides()
    if text and text.strip():
        data[slug] = {'text': text.strip(), 'replace': bool(replace)}
    else:
        data.pop(slug, None)
    _overrides_path.parent.mkdir(parents=True, exist_ok=True)
    _overrides_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def clear_override(slug: str):
    set_override(slug, '')
