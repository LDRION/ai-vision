"""
Реестр ссылок и данных проектов — «полочки», откуда агенты берут, куда идти и чем
оперировать: сайты, Telegram-каналы и боты, соцсети, панели метрик, кабинеты.
Файлы `resources/<проект>.md` версионируются в git. СЕКРЕТОВ ЗДЕСЬ НЕТ — токены,
пароли и ключи живут в company/ (вне git) и в .env. Здесь только ссылки, хэндлы,
публичные идентификаторы и заметки «как заведено».

Каждый файл — markdown с YAML-заголовком:

    ---
    project: Откликс
    owners: [Лид маркетинга, SMM-агент]   # какие РОЛИ могут править (оркестратор — всегда)
    approval: true                         # правки требуют одобрения оркестратора/человека
    ---
    # Ссылки и данные — Откликс
    - Сайт: https://…
    - Telegram-канал: @…

  resources()                → [(slug, meta, text)]
  for_project(name)          → текст реестра проекта (или '')
  context(max_chars)         → выжимка для подмешивания в план
  meta(slug)                 → заголовок (owners, approval, project)
  write(slug, text, meta)    → сохранить реестр
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from . import paths

FM = re.compile(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', re.DOTALL)


def _parse(text: str) -> Tuple[dict, str]:
    m = FM.match(text or '')
    if not m:
        return {}, (text or '').strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), m.group(2).strip()


def _slug(name: str) -> str:
    s = re.sub(r'[^\w]+', '-', (name or 'doc').strip().lower(), flags=re.UNICODE).strip('-')
    return s or 'doc'


def resources() -> List[Tuple[str, dict, str]]:
    d = paths.RESOURCES
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob('*.md')):
        try:
            meta, body = _parse(p.read_text(encoding='utf-8'))
        except OSError:
            continue
        out.append((p.stem, meta, body))
    return out


def meta(slug: str) -> dict:
    p = paths.RESOURCES / f'{slug}.md'
    if not p.exists():
        return {}
    m, _ = _parse(p.read_text(encoding='utf-8'))
    return m


def for_project(name: str) -> str:
    low = (name or '').lower()
    for slug, m, body in resources():
        if low and (low in slug.lower() or low in str(m.get('project', '')).lower()):
            return body
    return ''


URL_RE = re.compile(r'https?://[^\s)»"<>]*[^\s)»"<>.,;:!?]')   # точка в конце фразы — не часть ссылки


def _stems(text: str) -> set:
    """Основы слов от 4 букв (первые 5 знаков): «Метрику» и «Метрика», «Откликса» и «Откликс» — одно."""
    return {w[:5] for w in re.findall(r'\w{4,}', (text or '').lower().replace('ё', 'е'))}


def relevant(text: str, limit: int = 4) -> List[str]:
    """Строки реестра со ссылками, о которых говорит текст задачи: «Открой Яндекс Метрику
    Откликса» → «Откликс · Яндекс Метрика: … — https://metrika.yandex.ru/stat/?id=…».
    Нужны ровно эти адреса — модель иначе выдумывает свои (yandex.ru вместо Метрики)."""
    want = _stems(URL_RE.sub(' ', text or ''))           # слова самих ссылок не в счёт
    scored = []
    for slug, m, body in resources():
        project = str(m.get('project') or slug)
        own = bool(_stems(project) & want)
        for line in body.splitlines():
            line = line.strip().lstrip('-* ').strip()
            if not URL_RE.search(line):
                continue
            label = line.split('—')[0].split(':')[0]
            hits = len(_stems(label) & want)
            if hits:
                scored.append((hits + (2 if own else 0), f'{project} · {line}'))
    scored.sort(key=lambda s: -s[0])
    best = scored[0][0] if scored else 0
    return [line for score, line in scored if score == best][:limit]


def _root(url: str) -> str:
    host = re.sub(r'^https?://', '', url).split('/')[0].split(':')[0].lower()
    return '.'.join(host.split('.')[-2:]), host


def fix_urls(task: str) -> str:
    """Ссылка в задаче — «корень» сервиса (https://yandex.ru), а в реестре для него точный адрес
    (https://metrika.yandex.ru/stat/?id=…) — подставляем адрес из реестра."""
    known = [u for line in relevant(task) for u in URL_RE.findall(line)]
    if not known:
        return task

    def swap(m):
        url = m.group(0)
        root, host = _root(url)
        if url in known or host not in (root, f'www.{root}'):
            return url
        return next((k for k in known if _root(k)[0] == root), url)
    return URL_RE.sub(swap, task)


def context(max_chars: int = 1500) -> str:
    """Выжимка реестра для промта планировщика/агентов."""
    docs = resources()
    if not docs:
        return ''
    parts, used = [], 0
    for slug, m, body in docs:
        head = m.get('project') or slug
        block = f'### {head}\n{body}'
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)]
        parts.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return 'ССЫЛКИ И ДАННЫЕ ПРОЕКТОВ (реестр):\n' + '\n\n'.join(parts)


def write(slug: str, text: str, meta: Optional[dict] = None) -> Path:
    paths.RESOURCES.mkdir(parents=True, exist_ok=True)
    p = paths.RESOURCES / f'{_slug(slug)}.md'
    head = ''
    if meta:
        head = '---\n' + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip() + '\n---\n\n'
    p.write_text(head + text.strip() + '\n', encoding='utf-8')
    return p
