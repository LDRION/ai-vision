"""
Тексты команды — генерация через Siberi (тот же мозг, что решает шаги на экране).
Инструмент агента-копирайтера: посты, описания, сценарии, а также ПРОМТЫ для
других агентов — то есть ИИ пишут задания друг другу.

  write(instruction, material='', system='...')  → текст
"""

from . import siberi


def write(instruction: str, material: str = '', system: str = '',
          max_tokens: int = 1536, timeout: float = 120.0) -> str:
    """Сгенерировать текст: system — роль, instruction — задача, material — исходные данные.
    max_tokens оставлен для совместимости: длину задаёт сама задача."""
    user = instruction if not material else f'{instruction}\n\nИСХОДНЫЕ ДАННЫЕ:\n{material}'
    text = (siberi.ask(system.strip(), user, timeout=timeout) or '').strip()
    if not text:
        raise RuntimeError('Siberi вернула пустой текст')
    return text
