"""
Общая память команды — company/knowledge/*.md. Общий контекст, на который
опираются все агенты и планировщик: бренд, тон, аудитория, прошлые решения.
Так команда действует согласованно, а не каждый по-своему.

  context()                 → краткая выжимка знаний для подмешивания в промт
  remember(title, text)     → добавить/обновить документ знаний
  record_decision(text)     → записать стратегическое решение (журнал решений)
"""

import re
import time
from pathlib import Path
from typing import List, Tuple

from . import paths

DECISIONS = 'decisions'          # накопительный журнал решений оркестратора


def _slug(title: str) -> str:
    s = re.sub(r'[^\w]+', '-', (title or 'doc').strip().lower(), flags=re.UNICODE).strip('-')
    return s or 'doc'


def documents() -> List[Tuple[str, str]]:
    """Все документы знаний: [(имя, текст)], по имени."""
    d = paths.KNOWLEDGE
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob('*.md')):
        try:
            out.append((p.stem, p.read_text(encoding='utf-8').strip()))
        except OSError:
            pass
    return out


def context(max_chars: int = 2000) -> str:
    """Выжимка знаний для промта: заголовки + содержимое, обрезано до лимита."""
    docs = documents()
    if not docs:
        return ''
    parts = []
    used = 0
    for name, text in docs:
        block = f'### {name}\n{text}'
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)]
        parts.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return 'ЗНАНИЯ КОМАНДЫ (общий контекст):\n' + '\n\n'.join(parts)


def remember(title: str, text: str, append: bool = False) -> Path:
    """Сохранить документ знаний. append=True — дописать к существующему."""
    paths.KNOWLEDGE.mkdir(parents=True, exist_ok=True)
    p = paths.KNOWLEDGE / f'{_slug(title)}.md'
    if append and p.exists():
        with open(p, 'a', encoding='utf-8') as f:
            f.write('\n' + text.strip() + '\n')
    else:
        p.write_text(f'# {title}\n\n{text.strip()}\n', encoding='utf-8')
    return p


def record_decision(text: str):
    """Записать стратегическое решение в общий журнал решений."""
    remember(DECISIONS, f'- {time.strftime("%Y-%m-%d %H:%M")} {text.strip()}', append=True)
