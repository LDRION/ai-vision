"""
Многошаговый агент: снимок → решение Siberi → человеческое действие →
ожидание реакции экрана → снова снимок… до «done» / «fail» / лимита шагов.

Самокоррекция:
  • Siberi видит историю шагов с реакцией экрана на каждый («экран НЕ
    изменился», «окно: «Входящие»») и лестницу способов в промте: не
    сработал клик — наведение, перетаскивание, удержание, клавиатура…
  • Действие, которое уже FAIL_LIMIT раза ничего не дало, не исполняется
    (ОТКЛОНЕНО) — Siberi вынуждена сменить способ, а не биться в стену.
  • Подсказки пользователя человеческим языком («зажми и тащи правее») —
    главнее догадок и уроков; застряли — спрашиваем подсказку (ask_hint)
    и продолжаем с ней.
  • После задачи — разбор опыта (memory.Memory): уроки, в том числе
    из подсказок, сохраняются по приложению и подмешиваются в следующие
    задачи там же — объяснили один раз, дальше агент знает сам.

Страховки:
  • цель или активное окно изменились, пока Siberi думала, — действие
    отменяется, решение заново;
  • фокус ушёл в другое окно посреди ввода — клавиатура обрывается (motion.play);
  • STUCK_LIMIT шагов подряд без прогресса — стоп;
  • пользователь двинул мышь — стоп (RealEnv);
  • Siberi не видит картинку — стоп, вслепую не действуем.
"""

import json
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .agent import NotSeen, decide, decide_text
from .detect import pair_values
from .glyphs import add_sets
from .motion import Aborted, build_events, save_rec
from .prompt import STEP_SYSTEM, STEP_TEXT_SYSTEM, step_prompt, step_prompt_text, zone
from .screen import describe_change, target_moved

MAX_HISTORY = 12
STUCK_LIMIT = 5             # с запасом на перебор способов: клик → наведение → перетаскивание…
FAIL_LIMIT = 2              # столько раз действие ничего не дало — больше его не исполняем
GRID = 100
GRID_MODAL = 50             # всплывающее окно показываем вырезом — сетка мельче


def _save_action(sd: Path, act: dict, noise: float, offset, img_size, start, label: str):
    """
    Структура действия шага (в координатах экрана) для ручной правки в «Схеме»:
    тип, точка/рамка, текст, клавиши, шум, сдвиг монитора и размер снимка.
    Отдельно от action.rec (это уже готовые события) — тут исходные параметры.
    """
    mark = act.get('mark')
    data = {
        'action': act['action'], 'x': act.get('x'), 'y': act.get('y'),
        'box': act.get('box'), 'to': _clean_to(act.get('to')), 'points': act.get('points'),
        'text': act.get('text', ''), 'keys': act.get('keys', ''),
        'scroll': act.get('scroll', 0), 'seconds': act.get('seconds', 0),
        'source': act.get('source'), 'why': act.get('why', ''), 'label': label,
        'mark': {'id': mark['id'], 'text': mark['text'], 'role': mark.get('role', '')} if mark else None,
        'noise': noise, 'offset': list(offset), 'img_size': list(img_size),
        'start': list(start), 'edited': False,
    }
    (sd / 'action.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def _clean_to(to: Optional[dict]) -> Optional[dict]:
    if not to:
        return None
    return {'x': to.get('x'), 'y': to.get('y'), 'box': to.get('box')}


def _shift(t: Optional[dict], ox: int, oy: int) -> Optional[dict]:
    if not t or t.get('x') is None:
        return t
    box = t.get('box')
    return dict(t, x=t['x'] + ox, y=t['y'] + oy,
                box=[box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy] if box else None)


def _to_screen(res: dict, offset) -> dict:
    """Координаты скриншота → экрана (сдвиг монитора): точка, цель drag, точки штриха."""
    ox, oy = offset
    out = _shift(res, ox, oy)
    if res.get('to'):
        out = dict(out, to=_shift(res['to'], ox, oy))
    if res.get('points'):
        out = dict(out, points=[[p[0] + ox, p[1] + oy] for p in res['points']])
    return out


def _target_text(t: Optional[dict]) -> str:
    if not t:
        return ''
    m = t.get('mark')
    if m:
        text = ' '.join(str(m['text']).split())[:40]      # перенос в подписи порвал бы строку лога
        return f"[{m['id']}] {m.get('role', 'текст')} «{text}»"
    return f"({t['x']}, {t['y']})" if t.get('x') is not None else ''


def describe_action(res: dict) -> str:
    a = res['action']
    tgt = _target_text(res)
    s = f'{a} {tgt}'.rstrip()
    if a == 'drag':
        s += f" → {_target_text(res.get('to'))}"
    if a == 'type':
        s += f" ← ввод «{res['text']}»"
    if res.get('keys') and a in ('key', 'type', 'cast'):
        s += f" клавиши {res['keys']}"
    if a == 'scroll':
        s += f" на {res['scroll']}"
    if a in ('wait', 'long_press'):
        s += f" {res['seconds'] or (2 if a == 'wait' else 1):g} с"
    if a == 'stroke' and res.get('points'):
        pts = res['points']
        s += f" {len(pts)} точек ({pts[0][0]},{pts[0][1]})→({pts[-1][0]},{pts[-1][1]})"
    return s


def signature(res: dict) -> str:
    """Отпечаток действия для учёта неудач: тот же способ по той же цели (координаты — с допуском)."""
    def key(t):
        if not t:
            return ''
        if t.get('mark'):
            # Место — часть отпечатка: две «Управление» (на панели и под затемнением) — разные цели
            b = t['mark'].get('box') or (0, 0, 0, 0)
            return f"m:{t['mark']['text']}@{(b[0] + b[2]) // 80},{(b[1] + b[3]) // 80}"
        if t.get('x') is not None:
            return f"p:{t['x'] // 25},{t['y'] // 25}"
        return ''
    # Штрихи повторяться могут (линия за линией) — считаем каждый уникальным
    if res['action'] == 'stroke' and res.get('points'):
        return f"stroke:{res['points'][0]}:{res['points'][-1]}:{len(res['points'])}"
    return '|'.join([res['action'], key(res), key(res.get('to')), res.get('text') or '', res.get('keys') or ''])


NEW_SHARE_MAX = 0.6          # новым стало больше этой доли — это новая страница, а не панель поверх
LOOP_SIMILAR = 0.85          # экраны похожи настолько — считаем, что вернулись в то же состояние
LOOP_LIMIT = 3               # столько раз ходили по кругу — просим подсказку / сдаёмся


def _bucket(e: dict) -> tuple:
    x1, y1, x2, y2 = e['box']
    return e.get('role'), e.get('text'), (x1 + x2) // 60, (y1 + y2) // 60


def mark_new(elements: List[dict], before: Optional[set]) -> int:
    """Пометить элементы, которых не было на прошлом шаге (e['new']): открылась панель, меню,
    диалог. Две одинаковые подписи — новая лежит на открывшейся панели, старая под ней.
    Новым стало почти всё — это переход на другую страницу, тогда не помечаем. → сколько новых."""
    for e in elements:
        e.pop('new', None)
    if not before:
        return 0
    fresh = [e for e in elements if (e.get('text') or '').strip() and _bucket(e) not in before]
    labeled = [e for e in elements if (e.get('text') or '').strip()]
    if not fresh or len(fresh) > NEW_SHARE_MAX * max(len(labeled), 1):
        return 0
    for e in fresh:
        e['new'] = True
    return len(fresh)


def screen_state(title: str, elements: List[dict]) -> tuple:
    """Отпечаток экрана для поиска циклов: заголовок + подписи элементов управления + слова надписей.
    Строки OCR шумят от кадра к кадру, отдельные слова от 4 букв — нет; а без них во всплывающем
    окне (там почти одни надписи) любые два экрана выглядели бы одинаково."""
    import re
    marks = set()
    for e in elements:
        text = (e.get('text') or '').strip()
        if not text:
            continue
        if e.get('role') == 'значки':
            marks.add(('значки',))                      # число значков плавает — важно, что набор есть
        elif e.get('role') not in (None, 'текст'):
            marks.add((e.get('role'), text))
        else:
            marks.update(re.findall(r'\w{4,}', text.lower()))
    return title or '', frozenset(marks)


def same_screen(a: tuple, b: tuple) -> bool:
    if a[0] != b[0]:
        return False
    if not a[1] and not b[1]:
        return True
    return len(a[1] & b[1]) / max(len(a[1] | b[1]), 1) >= LOOP_SIMILAR


def _went_back(states: list, state: tuple) -> Optional[int]:
    """Шаг вернул назад: с экрана, впервые увиденного позже, на экран, увиденный раньше.
    → номер шага, где этот экран был впервые, или None. Повтор пройденного пути ВПЕРЁД
    (снова открыть панель после ошибки) — не возврат: виновато то, что вело назад."""
    if not states or same_screen(states[-1][1], state):
        return None                       # экран не менялся — это «не сработало», а не цикл
    def first(st):
        return next((k for k, s in states if same_screen(s, st)), None)
    now_first, prev_first = first(state), first(states[-1][1])
    if now_first is None or prev_first is None or prev_first <= now_first:
        return None
    return now_first


NAME_SIMILAR = 0.5           # похожесть имён (в латинице), с которой «Otklocal» путают с «Откликс»
FAIL_PUSHBACK = 1            # столько раз «fail» без веской причины возвращаем: исправь, а не сдавайся
HARD_FAIL = ('парол', 'код из', 'смс', 'sms', 'капч', 'captcha', 'оплат', 'платеж', 'платёж', 'нет прав',
             'нет доступа', 'доступ запрещ', 'заблокир', 'двухфактор', 'подтвержд', 'не поддерживает',
             'не предусмотрен', 'это канал', 'а не групп', 'у канала нет', 'у каналов нет')
_LATIN = str.maketrans({'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
                        'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
                        'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
                        'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'u',
                        'я': 'ya', '0': 'o'})


def _words(s: str) -> List[str]:
    import re
    return re.sub(r'[^a-z0-9]+', ' ', s.lower().translate(_LATIN)).split()


def _goal_names(goal: str) -> List[str]:
    """Названия в «кавычках» из самой задачи (без контекста и чек-листа)."""
    import re
    return [q.strip() for q in re.findall(r'«([^«»\n]{2,60})»', goal.split('\n\n')[0]) if len(_words(q)) <= 4]


def _name_match(name: str, text: str):
    """Подпись text — это name (True), похожее, но ДРУГОЕ имя (False) или вовсе не о нём (None).
    Сравниваем в латинице: OCR и транслит путают «Откликс» и «Otklocal», окончания не в счёт."""
    from difflib import SequenceMatcher
    from os.path import commonprefix
    nw, tw = _words(name), _words(text)
    if not nw or not tw:
        return None
    k = len(nw)
    want = ' '.join(nw)
    near = None
    for i in range(len(tw) - k + 1):
        got = ' '.join(tw[i:i + k])
        common = len(commonprefix([want, got]))
        if got == want or common >= max(4, len(want) - 2):
            return True                          # то же имя (или с другим окончанием)
        if common >= 3 and SequenceMatcher(None, want, got).ratio() >= NAME_SIMILAR:
            near = False
    return near


def name_warnings(goal: str, elements: List[dict]) -> List[str]:
    """Названия из задачи, которых на экране нет, но есть похожие подписи: модель кликает похожее."""
    out = []
    for name in _goal_names(goal):
        verdicts = [(e, _name_match(name, e.get('text') or '')) for e in elements]
        if any(v for _, v in verdicts):
            continue
        alike = [e for e, v in verdicts if v is False]
        if alike:
            shown = ', '.join(f"[{e['id']}] «{e['text']}»" if e.get('id') else f"«{e['text']}»" for e in alike[:3])
            out.append(f'«{name}» из цели на экране НЕТ. {shown} — похожее, но ДРУГОЕ название, не кликай его. '
                       f'Найди «{name}» поиском программы (поле «Поиск») или прокруткой списка.')
    return out


def _wrong_name(goal: str, res: dict) -> Optional[str]:
    """Клик по подписи, похожей на название из задачи, но другой. → это название или None."""
    text = (res.get('mark') or {}).get('text') or ''
    if res['action'] not in ('click', 'double_click') or not text:
        return None
    names = _goal_names(goal)
    if any(_name_match(nm, text) for nm in names):
        return None
    return next((nm for nm in names if _name_match(nm, text) is False), None)


def _soft_fail(reason: str) -> bool:
    """«fail» без веской причины (пароль, капча, оплата, нет прав) — скорее ошибка пути, её можно исправить."""
    low = (reason or '').lower()
    return not any(w in low for w in HARD_FAIL)


BACK_WORDS = ('закрыть', 'отмена', 'отменить', 'назад', 'выйти', 'скрыть', 'close', 'cancel', 'back',
              '×', '✕', '✖', '←', 'x')
BACK_KEYS = ('esc', 'escape', 'alt+left', 'browser_back')


def _deliberate_back(res: dict) -> bool:
    """Шаг — нарочный уход назад: «Закрыть», «Отмена», «Назад», крестик, Esc. Тогда в цикле виноват
    не он, а шаг, который завёл туда, откуда пришлось уходить (тупик)."""
    if res['action'] == 'key':
        return (res.get('keys') or '').strip().lower() in BACK_KEYS
    if res['action'] not in ('click', 'double_click'):
        return False
    label = ' '.join(((res.get('mark') or {}).get('text') or '').lower().replace('ё', 'е').split())
    return any(label == w or label.startswith(w + ' ') or label.endswith(' ' + w) for w in BACK_WORDS)


def _led_into(states: list, acts: dict, state: tuple, n: int):
    """Действие, которым в последний раз ушли с экрана state (до шага n). → (sig, desc, zone, back) или None."""
    for k, st in reversed(states):
        if k < n - 1 and k in acts and same_screen(st, state):
            return acts[k]
    return None


GAME_SETTLE = 0.3            # игра: столько ждём реакции на действие, дальше — следующий снимок
MOVED_RETRIES = 1           # столько раз решаем заново, если цель сдвинулась; дальше — экран «живой»
TEXT_MIN_LABELED = 6         # меньше подписанных элементов — экран текстом не описать
LOOK_COOLDOWN = 2            # модель попросила скриншот — столько шагов сразу со скриншотом


def _text_ok(env, elements) -> bool:
    """Шаг можно решить без скриншота: Retina ведёт журнал экрана и подписей достаточно."""
    import os
    if os.environ.get('AV_TEXT_STEPS', '1') == '0' or not hasattr(env, 'mark'):
        return False
    labeled = sum(1 for e in elements if e.get('role') not in (None, 'текст') and (e.get('text') or '').strip())
    return labeled >= TEXT_MIN_LABELED and env.mark() is not None


def _focus_named_app(goal: str, env, log) -> Optional[str]:
    """Задача называет программу, которая уже открыта («Открой Telegram…») — сразу выводим
    её окно вперёд, на каком бы мониторе оно ни было. Без модели и без панели задач.
    Названо несколько открытых программ — не угадываем, решает модель."""
    if not hasattr(env, 'focus_window'):
        return None
    import re as _re
    from pathlib import Path as _P
    from . import windows
    head = goal.split('\n\n')[0]                       # сама задача, без контекста и чек-листа
    words = set(_re.sub(r'[^\w]+', ' ', head.lower().replace('ё', 'е')).split())
    apps = {}
    for w in windows.list_windows():
        for nm in {windows.app_name(w).lower(), _P(w['process']).stem.lower()}:
            parts = nm.replace('ё', 'е').split()
            if len(nm) >= 4 and parts and all(x in words for x in parts):
                apps.setdefault(windows.app_name(w), w)
    if len(apps) != 1:
        return None
    name = next(iter(apps))
    desc = env.focus_window(name)
    if not desc:
        return None
    log(f'  🪟 «{name}» названа в задаче — вывел её окно вперёд: {desc}')
    return f'focus «{name}» → окно выведено вперёд: {desc}'


def _icons_after_type(res: dict, after) -> str:
    """Ввод в поле из значков (эмодзи-реакции): попали ли значки в поле. Многие такие поля
    с клавиатуры значков не принимают — Ctrl+A и Delete их стирают, а набранное пропадает."""
    mark = res.get('mark') or {}
    if res['action'] != 'type' or mark.get('role') != 'значки':
        return ''
    from .glyphs import find_sets
    x1, y1, x2, y2 = mark['box']
    got = sum(s['count'] for s in find_sets(after)
              if s['box'][0] < x2 and s['box'][2] > x1 and s['box'][1] < y2 and s['box'][3] > y1)
    want = sum(1 for ch in res.get('text') or '' if ch not in '\ufe0f\u200d' and not '\U0001F3FB' <= ch <= '\U0001F3FF')
    if not got:
        return ('поле значков ОПУСТЕЛО: набранные значки в него НЕ попали — поле не принимает их с клавиатуры. '
                'Добавляй через панель выбора: ⊕ или смайлик у края поля (hover или click), в панели кликай '
                'нужные значки по одному')
    if res.get('clear') and got > max(want + 3, 2 * want):
        return (f'в поле по-прежнему ~{got} значков — набор НЕ заменился. Удаляй лишние: клик в поле, '
                f'ctrl+a и delete (или backspace по одному), потом добавь нужные')
    return ''


BROWSER_PROCS = ('chrome', 'msedge', 'firefox', 'browser', 'opera', 'brave', 'vivaldi')
OPEN_WORDS = ('открой', 'открыть', 'зайди', 'перейди', 'загляни', 'посмотри')


def _goal_url(goal: str) -> Optional[str]:
    """Адрес, который задача велит открыть: из её текста (с поправкой по реестру проектов) или,
    если ссылки в тексте нет, — единственная подходящая строка реестра («Метрика Откликса»)."""
    from . import resources
    head = goal.split('\n\n')[0]
    if not any(w in head.lower() for w in OPEN_WORDS):
        return None
    urls = resources.URL_RE.findall(resources.fix_urls(head))
    if urls:
        return urls[0]
    links = resources.relevant(head)
    return resources.URL_RE.findall(links[0])[0] if len(links) == 1 else None


def _open_url(goal: str, env, log) -> Optional[str]:
    """Задача называет адрес — открываем его сами: окно браузера вперёд, ctrl+l, адрес, enter.
    Без модели: она путает сервисы (yandex.ru вместо Метрики) и вводит адрес по кругу."""
    if not hasattr(env, 'focus_window'):
        return None
    url = _goal_url(goal)
    if not url:
        return None
    from . import windows
    br = next((w for w in windows.list_windows() if Path(w['process']).stem.lower() in BROWSER_PROCS), None)
    if br is None:
        return None                                    # браузер не запущен — пусть решает модель
    name = windows.app_name(br)
    desc = env.focus_window(name)
    if not desc:
        return None
    before, _ = env.grab()
    for act in ({'action': 'key', 'x': None, 'y': None, 'keys': 'ctrl+l'},
                {'action': 'type', 'x': None, 'y': None, 'text': url, 'keys': 'enter'}):
        env.perform(build_events(env.cursor(), act))
    env.settle(before)
    log(f'  🌐 адрес из задачи — открыл сам, без модели: {url} ({name})')
    return f'открыт адрес из задачи: {url} — {name}, ctrl+l → адрес → enter. Дальше работай на этой странице'


QUIET_ROLES = ('поле ввода', 'значки')


def _quiet_ok(res: dict, elements: List[dict]) -> bool:
    """Клик в поле ввода (или в поле из значков) экран почти не меняет — только ставит курсор.
    «Экран не изменился» после него — не провал, а готовность вводить или стирать."""
    if res['action'] not in ('click', 'double_click'):
        return False
    mark = res.get('mark') or {}
    if mark:
        return mark.get('role') in QUIET_ROLES
    x, y = res.get('x'), res.get('y')
    return x is not None and any(e.get('role') in QUIET_ROLES and e['box'][0] <= x <= e['box'][2]
                                 and e['box'][1] <= y <= e['box'][3] for e in elements)


def _in_modal(box: List[int], res: dict) -> bool:
    """Точка действия (и точка «куда» у drag) — внутри всплывающего окна."""
    pts = [(res['x'], res['y'])] + ([(res['to']['x'], res['to']['y'])] if res.get('to') else [])
    return all(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in pts)


def _reflex(goal: str, env, log, noise: float) -> Optional[dict]:
    """Цель из простых команд («нажми «Войти»») — исполняем без модели (aivision.reflex)."""
    from . import reflex
    if not reflex.enabled() or not hasattr(env, 'find'):
        return None
    ops = reflex.parse(goal)
    if not ops:
        return None
    log(f'  ⚡ рефлекс: {len(ops)} простых действий — без модели')
    return reflex.run(ops, env, log=log, noise=noise)


def run_task(goal: str, env, max_steps: int = 15, refine: bool = True,
             run_dir: Optional[Path] = None, log: Callable[[str], None] = print,
             memory=None, hints: Sequence[str] = (),
             ask_hint: Optional[Callable[[str, str], Optional[str]]] = None,
             noise: float = 1.0,
             on_step: Optional[Callable[[int, str, str], None]] = None,
             role: str = '', command: Optional[str] = None,
             system_prompt: Optional[str] = None, game: Optional[dict] = None) -> dict:
    """
    system_prompt — свой системный промт вместо общего.
    game — игровой режим ({'cast_click': bool}): экран живой, элементов (OCR, UIA) нет —
    только скриншот; без рефлекса, текстовых шагов, всплывающих окон и ожидания «тишины».
    command — сама команда без приписок (контекст оркестратора, чек-лист): по ней пробуем
    рефлекс; не задана — пробуем по goal.
    memory — aivision.memory.Memory: уроки подмешиваются в шаги, по итогам задачи пополняются.
    hints — подсказки пользователя человеческим языком («зажми карточку и тащи правее»).
    ask_hint(status, reason) → подсказка или None: вызывается, когда агент застрял,
    сдался или кончились шаги — с подсказкой работа продолжается.
    → {'status': done|fail|stuck|limit|aborted|blind, 'answer', 'steps', 'history', 'seconds',
       'lessons', 'hints'}
    """
    # Роль агента-специалиста (из манифеста) впереди общего системного промта движка
    system = system_prompt or (f'{role.strip()}\n\n{STEP_SYSTEM}' if role.strip() else STEP_SYSTEM)
    system_text = f'{role.strip()}\n\n{STEP_TEXT_SYSTEM}' if role.strip() else STEP_TEXT_SYSTEM
    look_cooldown = 0
    moved = 0                           # подряд «цель сдвинулась, пока думала» — решали заново
    in_game = game is not None          # игровой режим (game={} — тоже он)
    live = in_game                      # экран живой (игра, видео): цель не перепроверяем
    before_els: Optional[set] = None    # элементы прошлого шага — что «новое» на этом
    states: list = []                   # (шаг, отпечаток экрана) — для поиска циклов
    from .modal import ModalTracker, inside
    modals = ModalTracker()             # открытые всплывающие окна (затемнение вокруг)
    step_img = None                     # снимок начала прошлого шага — с ним сравниваем затемнение
    last_act = None                     # (отпечаток, описание, зона, нарочно назад) действия прошлого шага
    acts: dict = {}                     # шаг → его действие (как last_act): чем уходили с экрана
    dead_ends: set = set()              # действия, которые заводили в тупик (оттуда уходили назад)
    loops: dict = {}                    # отпечаток действия → сколько раз оно возвращало назад
    loop_total = 0
    head = command or goal              # названия в «кавычках» берём из самой команды
    name_refused: set = set()           # клики по «похожему имени», которые уже раз отклонили
    pushed = 0                          # сколько раз вернули преждевременный «fail»
    t_start = time.time()
    hints = list(hints)
    history = []
    titles = []
    failed = {}
    still = 0
    n, budget = 0, max_steps

    def finish(status, answer, learn=True):
        out = {'status': status, 'answer': answer, 'steps': n, 'history': history,
               'seconds': round(time.time() - t_start), 'lessons': [], 'hints': hints}
        if learn and memory is not None and status not in ('aborted', 'blind') \
                and (len(history) >= 2 or status != 'done' or hints):
            log('  📚 разбираю опыт задачи — уроки на будущее…')
            out['lessons'] = memory.reflect(goal, status, history, answer, log=log, hints=hints,
                                            titles=titles)
            for les in out['lessons']:
                log(f"  📚 урок [{les['app'] or 'общий'}]: {les['text']}")
        if run_dir:
            (run_dir / 'result.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
        return out

    def help_or_finish(status, reason):
        """Не вышло — спрашиваем подсказку; None — продолжаем с ней."""
        nonlocal still, budget
        hint = ask_hint(status, reason) if ask_hint and status in ('fail', 'stuck', 'limit') else None
        if not hint:
            return finish(status, reason)
        hints.append(hint)
        history.append(f'{n}. ПОДСКАЗКА пользователя: {hint}')
        log(f'  🙋 подсказка: {hint}')
        still = 0
        failed.clear()
        budget = max(budget, n + max(5, max_steps // 2))
        if hasattr(env, 'resume'):
            env.resume()
        return None

    from . import control
    img, offset = env.grab()
    try:
        pre = None if in_game else _open_url(goal, env, log) or _focus_named_app(command or goal, env, log)
    except Aborted as e:
        return finish('aborted', str(e))
    if pre:
        n += 1
        history.append(f'{n}. {pre}')
        img, offset = env.grab()
    rx = None if in_game else _reflex(command if command is not None else goal, env, log, noise)
    if rx:
        for line in rx['history']:
            n += 1
            history.append(f'{n}. {line}')
        if rx['status'] == 'done':
            return finish('done', 'выполнено рефлексом, без модели: '
                          + '; '.join(h.split(': ', 1)[-1] for h in rx['history']), learn=False)
        if rx['status'] == 'aborted':
            return finish('aborted', rx['reason'])
        if rx['done']:
            history.append(f'{n}. рефлекс остановился ({rx["reason"]}) — дальше решаешь ты')
            img, offset = env.grab()
    while True:
        while n < budget:
            if control.stopped():
                return finish('aborted', 'остановлено пользователем')
            n += 1
            sd = None
            if run_dir:
                sd = run_dir / f'step_{n:02d}'
                sd.mkdir(parents=True, exist_ok=True)
                img.save(sd / 'screen.png')
            t0 = time.time()
            # поля из значков (эмодзи) — по пикселям; числа панелей — к своим подписям
            elements = [] if in_game else pair_values(add_sets(img, env.elements(img, offset)))
            t_el = time.time() - t0
            title, window = env.title(), env.window()
            if title and title not in titles:
                titles.append(title)
            mark_new(elements, before_els)
            before_els = {_bucket(e) for e in elements}
            # Всплывающее окно: всё вокруг затемнено — работаем только внутри, клик мимо его закроет
            was_open = len(modals.stack)
            box = None if in_game else modals.update(step_img, img)   # серый экран смерти — не диалог
            step_img = img
            if len(modals.stack) > was_open:
                log(f'  🗔 открылось всплывающее окно — работаю только в нём (x {box[0]}–{box[2]}, y {box[1]}–{box[3]})')
            elif len(modals.stack) < was_open:
                log('  🗔 всплывающее окно закрылось' + (' — снова в предыдущем' if box else ''))
            if box:
                elements = [e for e in elements if inside(box, e['box'])]
            notes = ([f'ОТКРЫТО ВСПЛЫВАЮЩЕЕ ОКНО: x {box[0]}–{box[2]}, y {box[1]}–{box[3]}, '
                      f'зона {zone(box, img.size)}; всё вокруг затемнено. В списке только его элементы, '
                      f'на скриншоте — только оно (крупнее); линейки по-прежнему в пикселях экрана.']
                     if box else []) + [f'ВНИМАНИЕ: {w}' for w in name_warnings(head, elements)]
            # Ходим по кругу: экран снова тот же, что был несколько шагов назад, —
            # действия между ними ни к чему не привели, последнее вернуло назад.
            state = screen_state(title, elements)
            back = _went_back(states, state)
            states.append((n, state))
            fwd = None
            if back is not None and last_act and last_act[3]:
                fwd = _led_into(states, acts, state, n)       # сам ушёл назад — виноват вход в тупик
            if fwd:
                fsig, fdesc, fzone, _ = fwd
                ldesc = last_act[1]
                loops[fsig] = loops.get(fsig, 0) + 1
                dead_ends.add(fsig)
                failed[fsig] = max(failed.get(fsig, 0), FAIL_LIMIT if loops[fsig] >= 2 else 1)
                if loops[fsig] >= 2:
                    loop_total += 1
                    still += 1
                    note = (f'ЦИКЛ: «{fdesc}» ({fzone}) уже {loops[fsig]} раза приводило туда, откуда ты сам уходил '
                            f'назад («{ldesc}»). Это тупик — больше его не открывай. Ищи цель в другом месте этого '
                            f'экрана или другим путём; если у этого объекта нужной настройки нет вовсе (например, это '
                            f'канал, а не группа), — fail с объяснением.')
                else:
                    note = (f'ТУПИК: «{fdesc}» ({fzone}) привело туда, откуда ты ушёл назад («{ldesc}») — нужного '
                            f'там нет. Снова туда не ходи: ищи цель в другом месте этого экрана или другим путём.')
                history.append(f'{n - 1}. {note}')
                log(f'  ⟲ тупик: «{fdesc}» — оттуда пришлось вернуться ({ldesc}), '
                    + ('больше туда не иду' if loops[fsig] >= 2 else 'ищу в другом месте'))
            elif back is not None and last_act:
                lsig, ldesc, lzone, _ = last_act
                loops[lsig] = loops.get(lsig, 0) + 1
                loop_total += 1
                failed[lsig] = max(failed.get(lsig, 0), FAIL_LIMIT if loops[lsig] >= 2 else 1)
                look_cooldown = LOOK_COOLDOWN          # слоёв (затемнение, панель) в тексте не видно
                still += 1
                note = (f'ЦИКЛ: экран вернулся к тому же состоянию, что на шаге {back} — шаги {back}–{n - 1} '
                        f'ни к чему не привели. Действие «{ldesc}» ({lzone}) вернуло назад: скорее всего, '
                        f'это не та цель (например, такая же кнопка под затемнением за открытой панелью). '
                        f'Его больше не повторяй — выбери другой элемент (смотри пометку [новое]) или другой подход.')
                history.append(f'{n - 1}. {note}')
                log(f'  ⟲ цикл: экран как на шаге {back} — «{ldesc}» вернуло назад, '
                    + ('больше его не делаю' if loops[lsig] >= 2 else 'пробую иначе'))
            if back is not None and last_act:
                if loop_total >= LOOP_LIMIT:
                    r = help_or_finish('stuck', f'хожу по кругу: {loop_total} раза вернулась к уже пройденному экрану')
                    if r:
                        return r
                    states.clear()
                    loops.clear()
                    loop_total = 0
            cx, cy = env.cursor()
            cursor = (cx - offset[0], cy - offset[1])
            lessons = memory.relevant(title) if memory is not None else []
            wins = env.open_windows() if hasattr(env, 'open_windows') else ''
            user = step_prompt(goal, n, budget, history[-MAX_HISTORY:], title, img.size,
                               GRID_MODAL if box else GRID, elements, lessons, hints, cursor,
                               windows=wins, notes=notes)
            if sd:
                (sd / 'prompt.txt').write_text(system + '\n\n---\n\n' + user, encoding='utf-8')
            from . import retina
            log(f'\n  ── шаг {n}/{budget} · «{title or "?"}» · {len(elements)} элементов'
                f' за {t_el:.1f} с ({retina.last_source or "?"})'
                + (f' · уроков: {len(lessons)}' if lessons else '')
                + (f' · подсказок: {len(hints)}' if hints else ''))
            if on_step:
                on_step(n, 'Siberi думает', 'think')    # на дорожке записи — отрезок раздумий

            res = None
            # в окне бывают не текстовые поля (сетка значков, цвета) — пока оно открыто, смотрим на картинку
            use_text = look_cooldown == 0 and not box and not in_game and _text_ok(env, elements)
            look_cooldown = max(0, look_cooldown - 1)
            if use_text:
                user_t = step_prompt_text(goal, n, budget, history[-MAX_HISTORY:], title, elements,
                                          img.size, lessons, hints, windows=wins, notes=notes)
                if sd:
                    (sd / 'prompt_text.txt').write_text(system_text + '\n\n---\n\n' + user_t, encoding='utf-8')
                try:
                    res = decide_text(system_text, user_t, img, elements, goal, run_dir=sd, log=log)
                except ValueError as e:
                    log(f'  ⚠️ текстовый шаг: {e} — беру скриншот')
                if res is None:
                    look_cooldown = LOOK_COOLDOWN
            try:
                if res is None:
                    res = decide(img, system, user, elements, goal, step=GRID_MODAL if box else GRID,
                                 refine=refine, run_dir=sd, log=log, cursor=cursor, region=box)
            except NotSeen as e:
                return finish('blind', str(e))
            except ValueError as e:
                history.append(f'{n}. ответ без цели для действия — {e}')
                log(f'  ⚠️ {e}')
                continue
            if res['check'] and n > 1:
                log(f"  ✔️ {res['check']}")
            log(f"  💬 {res['why']}")

            if res['action'] == 'done':
                return finish('done', res['answer'] or res['why'])
            if res['action'] in ('fail', 'none'):
                reason = res['answer'] or res['why']
                # Сдаётся без веской причины — чаще всего свернула не туда: пусть вернётся и исправит
                if not ask_hint and pushed < FAIL_PUSHBACK and budget - n >= 3 and _soft_fail(reason):
                    pushed += 1
                    log(f'  ⤴ рано сдаваться: {reason} — возвращаюсь и пробую иначе')
                    history.append(f'{n}. fail ОТКЛОНЁН — {reason}. Шагов ещё {budget - n}. Ошибся путём — исправь: '
                                   f'вернись назад («Отмена», крестик, esc, «Назад», закрой панель) и найди верную цель '
                                   f'(поиск программы, прокрутка, другой раздел). fail — только если цель недостижима '
                                   f'в принципе: пароль, капча, оплата, нет прав, поиск ничего не нашёл')
                    still += 1
                    continue
                log(f'  🏳 не выходит: {reason}')
                history.append(f"{n}. fail — {reason}")
                r = help_or_finish('fail', reason)
                if r:
                    return r
                img, offset = env.grab()
                continue

            # Переключение на программу — фокусом окна, а не кликом по панели задач:
            # у активного окна клик по его кнопке на панели его сворачивает.
            taskbar = res['action'] in ('click', 'double_click') and (res.get('mark') or {}).get('taskbar')
            if res['action'] == 'focus' or (taskbar and hasattr(env, 'focus_window')):
                name = res['text'] if res['action'] == 'focus' else res['mark']['text']
                try:
                    desc = env.focus_window(name) if hasattr(env, 'focus_window') else None
                except Aborted as e:
                    return finish('aborted', str(e))
                if desc or res['action'] == 'focus':
                    if desc:
                        how = ' — вместо клика по панели задач (он свернул бы окно)' if taskbar else ''
                        last_act = None                  # экран сменило переключение окна, а не клик
                        log(f'  🪟 переключился на окно: {desc}{how}')
                        history.append(f'{n}. focus «{name}» → окно выведено вперёд: {desc}')
                        still = 0
                    else:
                        log(f'  🪟 окна «{name}» нет среди открытых')
                        history.append(f'{n}. focus «{name}» → такого окна нет среди открытых. '
                                       f'Программа не запущена — запусти её (панель задач, меню «Пуск»)')
                        still += 1
                    img, offset = env.grab()
                    continue

            act, sig = describe_action(res), signature(res)
            if failed.get(sig, 0) >= FAIL_LIMIT:
                if loops.get(sig) and sig in dead_ends:
                    log(f'  🚫 {act} — уже {loops[sig]} раза приводило в тупик, отклоняю')
                    history.append(f'{n}. {act} — ОТКЛОНЕНО: уже {loops[sig]} раза приводило в тупик (ты сам '
                                   f'уходил оттуда назад). Нужного там нет — ищи в другом месте или fail с объяснением')
                elif loops.get(sig):
                    log(f'  🚫 {act} — уже {loops[sig]} раза возвращало назад, отклоняю')
                    history.append(f'{n}. {act} — ОТКЛОНЕНО: уже {loops[sig]} раза возвращало назад (ЦИКЛ). '
                                   f'Это не та цель: выбери другой элемент (смотри [новое]) или другой подход')
                else:
                    log(f'  🚫 {act} — уже {failed[sig]} раза без результата, отклоняю')
                    history.append(f'{n}. {act} — ОТКЛОНЕНО: уже {failed[sig]} раза не дало результата. '
                                   f'Выбери ДРУГОЙ способ (hover, drag, double_click, клавиатура…)')
                still += 1
                if still >= STUCK_LIMIT:
                    r = help_or_finish('stuck', f'{STUCK_LIMIT} шагов подряд без прогресса')
                    if r:
                        return r
                    img, offset = env.grab()
                continue
            if env.window() != window:
                log('  ↻ активное окно сменилось, пока Siberi думала, — решаю заново')
                history.append(f'{n}. {act} — НЕ выполнено: сменилось активное окно')
                img, offset = env.grab()
                continue
            wrong = _wrong_name(head, res)
            if wrong and sig not in name_refused:
                name_refused.add(sig)                  # настоит ещё раз — значит, OCR исказил верное имя
                log(f'  🚫 {act} — это не «{wrong}» из задачи, а похожее имя; не выполняю')
                history.append(f'{n}. {act} — НЕ выполнено: «{res["mark"]["text"]}» — не «{wrong}», а похожее, '
                               f'но ДРУГОЕ название. Найди именно «{wrong}»: поле «Поиск» программы (type с '
                               f'названием) или прокрутка списка')
                still += 1
                continue
            if box and res['x'] is not None and not _in_modal(box, res):
                log(f'  🗔 {act} — мимо всплывающего окна: клик туда закрыл бы его, не выполняю')
                history.append(f'{n}. {act} — НЕ выполнено: точка вне всплывающего окна (клик по затемнённому '
                               f'фону закрыл бы его). Цель ищи внутри окна; закрыть — «Отмена», крестик или Esc')
                still += 1
                continue
            if box and res.get('also'):
                res['also'] = [a for a in res['also'] if a.get('x') is None or inside(box, [a['x'], a['y']] * 2)]
            if res['x'] is not None and not live:
                now, now_offset = env.grab()
                if target_moved(img, now, res['x'], res['y'], res['box']):
                    moved += 1
                    if moved <= MOVED_RETRIES:
                        log('  ↻ область цели изменилась, пока Siberi думала, — решаю заново')
                        history.append(f'{n}. {act} — НЕ выполнено: экран изменился, пока ты думал')
                        img, offset = now, now_offset
                        continue
                    # Меняется каждый раз — это игра или видео: ждать «тишины» бесполезно, действуем
                    live = True
                    log('  🎞 экран всё время в движении (игра, видео) — дальше действую сразу, не перепроверяя')
                    history.append(f'{n}. экран всё время в движении (игра, видео): действия выполняются сразу. '
                                   f'Выбирай устойчивые цели — мини-карту, кнопки интерфейса, а не движущиеся объекты')
                else:
                    moved = 0

            log(f"  ▶ {act}  [{res['source'] or '—'}] · {time.time() - t0:.0f} с на решение")
            if on_step:
                on_step(n, act, 'act')       # момент начала действия — метка на дорожке записи
            start = env.cursor()
            seq = env.mark() if hasattr(env, 'mark') else None
            if in_game and not game.get('cast_click', True):   # быстрый каст: клавиша при курсоре на цели, без ЛКМ
                for a in [res] + list(res.get('also') or []):
                    if a['action'] == 'cast':
                        a['quick'] = True
            screen_act = _to_screen(res, offset)
            events = build_events(start, screen_act, noise=noise)
            if sd and events:
                save_rec(sd / 'action.rec', events, start)
                _save_action(sd, screen_act, noise, offset, img.size, start, act)
            try:
                if res['action'] == 'wait':
                    time.sleep(min(10.0, res['seconds'] or 2.0))
                env.perform(events)
                for sub in (res.get('also') or []):            # серия действий на том же экране за один шаг
                    ev = build_events(env.cursor(), _to_screen(sub, offset), noise=noise)
                    if ev:
                        env.perform(ev)
                    log(f'     + {describe_action(sub)}')
                    history.append(f'{n}. + {describe_action(sub)}')
            except Aborted as e:
                return finish('aborted', str(e))

            if in_game:                               # в игре «тишины» не бывает — смотрим сразу
                time.sleep(GAME_SETTLE)
                after, offset = env.grab()
            else:
                after, offset = env.settle(img)
            change = describe_change(img, after)
            new_title = env.title()
            if new_title and new_title != title:
                change += f', окно: «{new_title}»'
            seen = env.changes(seq) if seq is not None else []
            if seen:
                change += ' · на экране: ' + '; '.join(seen)
            icons = _icons_after_type(res, after)
            if icons:
                change += f' · {icons}'
            quiet = change.startswith('экран НЕ') and _quiet_ok(res, elements)
            if quiet:
                change = ('экран заметно не изменился — для клика в поле это нормально: курсор стоит в поле, '
                          'дальше вводи или стирай (backspace — значок/символ слева от курсора, delete — справа)'
                          + change[len('экран НЕ изменился'):])
            history.append(f'{n}. {act} → {change}')
            log(f'  👀 {change}')
            mbox = (res.get('mark') or {}).get('box')
            last_act = (sig, act, f'зона {zone(mbox, img.size)}' if mbox else 'по координатам',
                        _deliberate_back(res))
            acts[n] = last_act

            # Ничего не произошло — только если и картинка та же, и Retina ничего не заметила (фокус и т.п.)
            no_effect = change.startswith('экран НЕ') and not seen and res['action'] != 'wait'
            if no_effect:
                failed[sig] = failed.get(sig, 0) + 1
            still = still + 1 if no_effect else 0
            img = after
            if still >= STUCK_LIMIT:
                r = help_or_finish('stuck', f'{STUCK_LIMIT} шагов подряд без прогресса')
                if r:
                    return r
                img, offset = env.grab()

        r = help_or_finish('limit', f'не уложилась в {budget} шагов')
        if r:
            return r
        img, offset = env.grab()