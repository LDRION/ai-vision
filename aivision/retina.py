"""
Клиент Screen Retina — живого индекса экрана.

Retina постоянно держит дерево UI Automation и OCR всех окон с координатами и
отвечает за десятки миллисекунд. Свой обход UIA (detect.uia_elements) стоит
~4 с на каждом шаге — его и заменяем. Запускает её сам AiVision (aivision.services:
встроенная копия aivision/vendor/retina на порту 8766). Если Retina не отвечает,
всё работает по-старому: функции возвращают None, вызывающий берёт прежний путь.

    AV_RETINA=0          — не обращаться к Retina вовсе
    AV_RETINA_URL=...    — внешняя Retina вместо встроенной (например, http://127.0.0.1:8765)
    AV_RETINA_EXCLUDE=…  — окна, где не искать (через «;»), по умолчанию свои окна «AI Vision»:
                           в логе лаунчера стоят те самые подписи, которые ищем
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

from .redact import redact

TIMEOUT = 2.0
DOWN_FOR = 10.0              # после отказа не дёргаем Retina столько секунд

# Роли Retina → роли AiVision (те же, что у detect.uia_elements: модель видит привычный список).
ROLES = {
    'btn': 'кнопка', 'splitbtn': 'кнопка', 'link': 'ссылка', 'edit': 'поле ввода',
    'combo': 'список', 'check': 'флажок', 'radio': 'переключатель', 'menuitem': 'пункт меню',
    'tab': 'вкладка', 'item': 'элемент списка', 'treeitem': 'элемент дерева',
}
BROWSERS = {'chrome.exe', 'msedge.exe', 'browser.exe', 'opera.exe', 'brave.exe', 'vivaldi.exe',
            'firefox.exe', 'code.exe', 'electron.exe'}
THIN_TREE = 40               # меньше целей у окна-браузера — дерево ещё не построено

_down_until = 0.0
_last_fg: Tuple[str, str] = ('', '')
last_source = ''             # 'retina' | 'uia' — откуда взяты элементы последнего вызова


def enabled() -> bool:
    return os.environ.get('AV_RETINA', '1') != '0' and time.time() >= _down_until


def _base() -> str:
    from .services import retina_url
    return retina_url()


def _get(path: str, params: Optional[dict] = None, timeout: float = TIMEOUT,
         use: bool = True) -> Optional[dict]:
    """use=False — просто посмотреть (вкладка «Зрение»): зрение не будим и не держим включённым."""
    global _down_until
    if not enabled():
        return None
    if use:
        from .services import touch
        touch()
    url = _base() + path + ('?' + urllib.parse.urlencode(params, doseq=True) if params else '')
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        if use:
            _down_until = time.time() + DOWN_FOR
        return None


def exclude() -> List[str]:
    return [x.strip() for x in os.environ.get('AV_RETINA_EXCLUDE', 'AI Vision').split(';') if x.strip()]


def available() -> bool:
    return _get('/stats', timeout=0.5) is not None


def convert(items: List[dict], size: Tuple[int, int], offset: Tuple[int, int]) -> List[dict]:
    """Цели Retina (координаты всего виртуального экрана) → элементы AiVision на этом мониторе.

    Фильтры те же, что у detect.uia_elements: только окно в фокусе и панель задач,
    только интерактивные роли, рамка целиком на мониторе, не больше четверти экрана.
    """
    W, H = size
    ox, oy = offset
    out = []
    for e in items:
        role = ROLES.get(e.get('role'))
        if not role or e.get('source') != 'uia' or not e.get('visible', True):
            continue
        taskbar = e.get('process', '').lower() == 'explorer.exe' and not e.get('title')
        if not (e.get('fg') or taskbar):
            continue
        name = ' '.join((e.get('name') or '').split())[:80]
        if not name and role != 'поле ввода':
            continue
        x, y, w, h = e['rect']
        box = [x - ox, y - oy, x + w - ox, y + h - oy]
        if w < 4 or h < 4 or box[0] < 0 or box[1] < 0 or box[2] > W or box[3] > H:
            continue
        if w * h > 0.25 * W * H:
            continue
        el = {'text': name, 'role': role, 'box': box}
        if taskbar:
            el['taskbar'] = True                 # кнопка панели задач: у открытой программы — сворачивает
        if e.get('value'):
            el['value'] = e['value']
        if e.get('state'):
            el['state'] = e['state']
        out.append(el)
    return out


_raw: dict = {}               # area → (время, ответ /elements): один шаг не спрашивает дважды
RAW_TTL = 0.25


def _elements_raw(size: Tuple[int, int], offset: Tuple[int, int]) -> Optional[dict]:
    global _last_fg
    area = f'{offset[0]},{offset[1]},{size[0]},{size[1]}'
    hit = _raw.get(area)
    if hit and time.time() - hit[0] < RAW_TTL:
        return hit[1]
    d = _get('/elements', {'area': area, 'exclude': exclude()})
    if d is None:
        return None
    fg = d.get('foreground') or {}
    key = (fg.get('process', ''), fg.get('title', ''))
    n_fg = sum(1 for e in d.get('elements', []) if e.get('fg'))
    # Новое окно браузера/Electron: дерево доступности строится лениво — перечитываем.
    if key[0].lower() in BROWSERS and (key != _last_fg or n_fg < THIN_TREE):
        d = _get('/elements', {'area': area, 'refresh': 1, 'exclude': exclude()}) or d
    _last_fg = key
    _raw[area] = (time.time(), d)
    return d


def elements(size: Tuple[int, int], offset: Tuple[int, int]) -> Optional[List[dict]]:
    """Интерактивные элементы монитора из Retina или None, если Retina недоступна."""
    d = _elements_raw(size, offset)
    return None if d is None else convert(d.get('elements', []), size, offset)


def unnamed(size: Tuple[int, int], offset: Tuple[int, int]) -> List[dict]:
    """Кнопки-иконки без подписи окна в фокусе: [{'role', 'box'}] в координатах монитора."""
    d = _elements_raw(size, offset)
    if d is None:
        return []
    W, H = size
    ox, oy = offset
    out = []
    for e in d.get('elements', []):
        role = ROLES.get(e.get('role'))
        if not role or role == 'поле ввода' or e.get('source') != 'uia' or (e.get('name') or '').strip():
            continue
        if not e.get('fg') or not e.get('visible', True):
            continue
        x, y, w, h = e['rect']
        box = [x - ox, y - oy, x + w - ox, y + h - oy]
        if box[0] < 0 or box[1] < 0 or box[2] > W or box[3] > H:
            continue
        out.append({'role': role, 'box': box})
    return out


def texts(region: Tuple[int, int, int, int]) -> Optional[List[Tuple[str, List[int]]]]:
    """Все подписи (UIA и OCR) в области экрана: [(текст, [x, y, w, h])]. None — Retina нет."""
    d = _get('/elements', {'area': ','.join(str(int(v)) for v in region), 'exclude': exclude()})
    if d is None:
        return None
    return [(e['name'], e['rect']) for e in d.get('elements', []) if (e.get('name') or '').strip()]


# ── Рефлекс: найти цель по подписи и дождаться реакции экрана ─────────────────
FIND_MIN = 0.8               # ниже — не уверены: пусть решает разбор/модель, не кликаем наугад
_TO_RETINA = {}
for _r, _a in ROLES.items():
    _TO_RETINA.setdefault(_a, set()).add(_r)


def pick(matches: List[dict], role: str = '', min_score: float = FIND_MIN) -> Optional[dict]:
    """Лучшая цель из ответа /find. Две равные — нужна роль, чтобы их различить; иначе None."""
    good = [m for m in matches if m.get('score', 0) >= min_score]
    if not good:
        return None
    if role and role in _TO_RETINA:
        with_role = [m for m in good if m.get('role') in _TO_RETINA[role]]
        good = with_role or good
    top = good[0]['score']
    tied = [m for m in good if top - m['score'] < 0.03]
    return tied[0] if len(tied) == 1 else None


def lookup(text: str, area: Tuple[int, int, int, int], role: str = '') -> Tuple[Optional[dict], str]:
    """Цель по подписи в области (x, y, w, h) — координаты всего экрана.

    → (цель, 'ok') | (None, 'ambiguous') — несколько равных | (None, 'none') | (None, 'down').
    """
    d = _get('/find', {'q': text, 'limit': 5, 'area': ','.join(str(int(v)) for v in area),
                       'exclude': exclude()})
    if d is None:
        return None, 'down'
    matches = d.get('matches', [])
    m = pick(matches, role)
    if m:
        return m, 'ok'
    good = [x for x in matches if x.get('score', 0) >= FIND_MIN]
    return None, 'ambiguous' if len(good) >= 2 else 'none'


def find(text: str, area: Tuple[int, int, int, int], role: str = '') -> Optional[dict]:
    """Цель по подписи или None (не нашли, неоднозначно, Retina недоступна)."""
    return lookup(text, area, role)[0]


def settled(area: Tuple[int, int, int, int], quiet_ms: int = 300, timeout_ms: int = 2500,
            expect: bool = True) -> Optional[dict]:
    """Ждать, пока область успокоится; None — Retina недоступна (вызывающий ждёт по-старому)."""
    return _get('/settled', {'area': ','.join(str(int(v)) for v in area), 'quiet': quiet_ms,
                             'timeout': timeout_ms, 'expect': int(expect)},
                timeout=timeout_ms / 1000 + 2)


# ── Что изменилось на экране после действия (журнал событий Retina) ─────────────
CHANGE_LINES = 8


def _post(path: str, payload: dict, timeout: float = TIMEOUT) -> Optional[dict]:
    if not enabled():
        return None
    req = urllib.request.Request(_base() + path, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def register_own() -> bool:
    """Попросить Retina не видеть окна AiVision: их лог с подписями — не события экрана."""
    return _post('/exclude', {'prefixes': exclude()}) is not None


def mark() -> Optional[int]:
    """Номер последнего события Retina — точка отсчёта перед действием. None — журнала нет."""
    d = _get('/events', {'since': 10 ** 12}, timeout=1.0)
    return d.get('last') if d and 'last' in d else None


def changes(since: int, limit: int = CHANGE_LINES) -> List[str]:
    """Изменения экрана после события since, строками для модели.

    Заголовок окна вместо всего его списка (keyframe), без фоновых окон (@proc: часы,
    трей) и без движения (видео) — только реакция на действие.
    """
    d = _get('/events', {'since': since}, timeout=1.5)
    if not d or 'last' not in d:
        return []
    out: List[str] = []
    for ev in d.get('events', []):
        if ev.get('kind') == 'motion':
            continue
        parts = (ev.get('text') or '').splitlines()
        if ev.get('kind') == 'window':
            parts = parts[:1]
        for ln in parts:
            ln = ln.strip()
            if ln and not ln.startswith('@') and ln not in out:
                out.append(redact(ln)[:120])
    return out[-limit:]


# ── Лента для вкладки «Зрение» ────────────────────────────────────────────────
def recent() -> Optional[List[str]]:
    """Последние события Retina с временем («[12:03:07] + btn «Отправить»»); None — Retina нет."""
    d = _get('/events', timeout=0.5, use=False)
    return None if d is None else [redact(e) for e in d.get('events', []) if isinstance(e, str)]


def feed(since: int) -> Optional[Tuple[int, List[dict]]]:
    """События после since: (номер последнего, [{'seq', 'kind', 'text'}]); None — Retina нет."""
    d = _get('/events', {'since': since}, timeout=0.5, use=False)
    if not d or 'last' not in d:
        return None
    return d['last'], [dict(e, text=redact(e.get('text') or '')) for e in d.get('events', [])]
