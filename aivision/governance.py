"""
Управление доступом и одобрениями — «всё по полочкам, но под контролем».

Три правила:
1. Главный оркестратор видит и правит всё. Лиды отделов — управленцы.
2. Документы-реестры (resources/*.md) правят только РОЛИ из их `owners`; остальным —
   нельзя (или через одобрение, если у документа `approval: true`).
3. Серьёзные действия нижних агентов (создать бота, выдать права, привязать аккаунт,
   настроить платежи/домен, опубликовать от имени бренда) оркестратор обязан завизировать —
   они уходят на одобрение человека, а не делаются молча.

  can_edit(role, slug)     → можно ли этой роли править документ-реестр
  needs_approval(slug)     → правка документа требует одобрения
  needs_signoff(task)      → задача серьёзная и требует визы оркестратора/человека
  review(agent, task)      → вердикт по шагу нижнего агента: 'allow' | 'approve'
"""

import re
from typing import Optional

ORCHESTRATOR = 'Оркестратор'

# Серьёзные действия: настройка аккаунтов, права, учётки, платежи, домены, публикация
# от имени бренда. Такое делаем только с явной визой человека.
SIGNOFF = re.compile(
    r'(созда\w*\s+бот|заведи\w*\s+бот|нов\w+\s+бот|'
    r'выдай?\w*\s+прав|права\s+доступа|администратор|админк|сдела\w+\s+админ|'
    r'привяж\w+\s+аккаунт|подключ\w+\s+аккаунт|нов\w+\s+аккаунт|регистрац|зарегистрир|'
    r'api[-\s]?ключ|api[-\s]?key|токен|oauth|доступы|учёт\w+\s+запис|учетн\w+\s+запис|'
    r'домен|\bdns\b|'
    r'настрой\w*\s+канал|настрой\w*\s+telegram|настрой\w*\s+\bvk\b)',
    re.IGNORECASE)


def can_edit(role: str, slug: str) -> bool:
    """Может ли роль править документ-реестр resources/<slug>.md."""
    from . import resources
    if role == ORCHESTRATOR:
        return True
    owners = resources.meta(slug).get('owners') or []
    if not owners:                                      # у документа нет владельцев → только оркестратор
        return False
    return role in owners


def needs_approval(slug: str) -> bool:
    """Правка этого документа-реестра требует одобрения оркестратора/человека."""
    from . import resources
    return bool(resources.meta(slug).get('approval'))


def needs_signoff(task: str) -> bool:
    return bool(SIGNOFF.search(task or ''))


def review(agent, task: str) -> str:
    """
    Оркестраторский разбор шага нижнего агента. Лид/оркестратор действуют сами;
    серьёзные действия остальных — на визу ('approve'), прочее — 'allow'.
    """
    is_lead = getattr(agent, 'is_lead', False) or getattr(agent, 'name', '') == ORCHESTRATOR
    if is_lead:
        return 'allow'
    return 'approve' if needs_signoff(task) else 'allow'
