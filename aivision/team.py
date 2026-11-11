"""
Оргструктура команды ИИ: отделы, руководители (лиды), сотрудники.

Строится из плоского списка агентов (agents/*.md) по полям department и
reports_to. Главный оркестратор (ты) делегирует лиду отдела, лид — своим
сотрудникам. Иерархия задаётся теми же манифестами — отдельного формата нет.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .agents import Agent, load_agents


@dataclass
class Department:
    name: str
    lead: Optional[Agent] = None
    members: List[Agent] = field(default_factory=list)

    def roster(self) -> List[Agent]:
        return ([self.lead] if self.lead else []) + self.members


def build_departments(agents: Optional[List[Agent]] = None) -> List[Department]:
    """Сгруппировать агентов по отделам; лид отдела — агент с инструментом orchestrate."""
    agents = agents if agents is not None else load_agents()
    by_dept: Dict[str, Department] = {}
    for a in agents:
        dept = a.department or 'Без отдела'
        d = by_dept.setdefault(dept, Department(name=dept))
        if a.is_lead and d.lead is None:
            d.lead = a
        else:
            d.members.append(a)
    return [by_dept[k] for k in sorted(by_dept)]


def org_chart(agents: Optional[List[Agent]] = None) -> str:
    """Текстовая схема команды — её же можно показать оркестратору как контекст."""
    lines = []
    for d in build_departments(agents):
        lead = f' — лид: {d.lead.name}' if d.lead else ''
        lines.append(f'▸ Отдел «{d.name}»{lead}')
        for m in d.members:
            caps = ', '.join(m.capabilities) or '—'
            lines.append(f'    · {m.name}: {m.description} [{caps}]')
    return '\n'.join(lines)


def department_of(agent: Agent, agents: Optional[List[Agent]] = None) -> Optional[Department]:
    for d in build_departments(agents):
        if agent in d.roster():
            return d
    return None
