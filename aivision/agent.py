"""
Скриншот → разметка → Siberi → точка действия (+ уточнение zoom для целей без метки).
"""

import json
import random
import re
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PIL import Image

from . import siberi
from .detect import find_elements
from .overlay import compose, zoom_view
from .prompt import LOCATE_SYSTEM, REFINE_SYSTEM, locate_prompt, refine_prompt

ACTIONS = {'click', 'double_click', 'right_click', 'type', 'key', 'scroll', 'wait', 'done', 'fail', 'none',
           'drag', 'hover', 'long_press', 'stroke', 'focus', 'cast'}
# cast — навести курсор на цель, нажать клавишу навыка (keys) и ЛКМ по цели: способности в играх
POINT_ACTIONS = {'click', 'double_click', 'right_click', 'type', 'drag', 'hover', 'long_press', 'cast'}
VISION_RETRIES = 3


class NotSeen(RuntimeError):
    """Siberi ответила, не увидев картинку (проверочный код не совпал)."""


def parse_json(text: str) -> dict:
    """
    Первый JSON-объект из ответа (модели любят оборачивать в ```json). Терпим к
    типичным огрехам LLM: пропущенное значение после двоеточия, висячие запятые.
    """
    text = re.sub(r'```(?:json)?', '', text)
    start = text.find('{')
    if start < 0:
        raise ValueError(f'В ответе нет JSON: {text[:200]!r}')
    body = text[start:]
    try:
        return json.JSONDecoder().raw_decode(body)[0]
    except json.JSONDecodeError:
        return json.JSONDecoder().raw_decode(_repair_json(body))[0]


def _repair_json(s: str) -> str:
    """Чиним частые поломки: «"k":,» → «"k": null,», висячие запятые перед ] и }."""
    s = re.sub(r':\s*(?=[,}\]])', ': null', s)       # пропущено значение после двоеточия
    s = re.sub(r',\s*(?=[}\]])', '', s)              # запятая перед закрытием
    return s


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean_points(points, size) -> Optional[list]:
    """Список [[x,y],…] → целые точки внутри кадра; соседние дубли убираем."""
    if not isinstance(points, (list, tuple)):
        return None
    w, h = size
    out = []
    for p in points:
        if isinstance(p, dict):
            x, y = _num(p.get('x')), _num(p.get('y'))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = _num(p[0]), _num(p[1])
        else:
            continue
        if x is None or y is None:
            continue
        x, y = int(max(0, min(w - 1, x))), int(max(0, min(h - 1, y)))
        if not out or (x, y) != tuple(out[-1]):
            out.append([x, y])
    return out


def resolve_point(ans: dict, elements: List[dict], size):
    """Метка → центр её рамки; иначе x/y. None, если точки нет или она вне экрана."""
    mark = ans.get('mark')
    if mark is not None:
        el = next((e for e in elements if str(e['id']) == str(mark).strip('[]')), None)
        if el:
            x1, y1, x2, y2 = el['box']
            return (x1 + x2) / 2, (y1 + y2) / 2, 'mark', el['box']
    x, y = _num(ans.get('x')), _num(ans.get('y'))
    if x is None or y is None:
        return None
    if not (0 <= x < size[0] and 0 <= y < size[1]):
        return None
    return x, y, 'grid', None


SUB_ACTIONS = {'click', 'double_click', 'type', 'key', 'hover', 'right_click', 'cast'}


def _sub_actions(raw, elements: List[dict], size) -> Optional[list]:
    """Доп. действия «also» — серия простых действий на ТОМ ЖЕ экране (без zoom-уточнения).
    Каждое → {action, x, y, box, source, mark, text, keys} в координатах скриншота."""
    if not isinstance(raw, (list, tuple)):
        return None
    subs = []
    for a in list(raw)[:12]:
        if not isinstance(a, dict):
            continue
        act = str(a.get('action') or 'click').lower()
        if act not in SUB_ACTIONS:
            continue
        sub = {'action': act, 'text': a.get('text') or '', 'keys': a.get('keys') or '',
               'clear': bool(a.get('clear')), 'why': a.get('why') or '',
               'x': None, 'y': None, 'box': None, 'source': None, 'mark': None}
        if act == 'key':
            if sub['keys']:
                subs.append(sub)
            continue
        pt = resolve_point(a, elements, size)
        if pt is None:
            continue
        x, y, source, box = pt
        sub.update({'x': int(round(x)), 'y': int(round(y)), 'box': box, 'source': source,
                    'mark': next((e for e in elements if e['box'] == box), None) if box else None})
        subs.append(sub)
    return subs or None


def ask_seen(system: str, user: str, render: Callable[[str], Image.Image], tag: str,
             run_dir: Optional[Path], log: Callable[[str], None],
             blind_ok: Callable[[dict], bool] = lambda ans: False,
             extra: Sequence[Image.Image] = ()) -> dict:
    """
    Запрос с картинкой и проверкой, что модель её увидела: render(code) рисует
    разметку с кодом, ответ без этого кода — слепой. Слепой ответ принимаем,
    только если blind_ok(ans) (выбор номера из списка — рамка всё равно точная),
    иначе повторяем с новым кодом. В ответ добавляется ans['_seen'].
    extra — ещё картинки следом (до 3): увеличенные вырезы мелкого — весь экран мозг
    получает ужатым, и цифры высотой ~10 px в нём не прочесть (угадывает).
    """
    extra_urls = [siberi.image_to_data_url(im) for im in list(extra)[:3]]
    def _check(raw, code, attempt, name):
        if run_dir:
            (run_dir / f'{tag}_{name}{attempt}.txt').write_text(raw, encoding='utf-8')
        try:
            ans = parse_json(raw)
        except ValueError:
            return None
        ans['_seen'] = re.sub(r'\D', '', str(ans.get('code') or '')) == code
        return ans

    for attempt in range(1, VISION_RETRIES + 1):
        code = f'{random.randint(0, 99999):05d}'
        image = render(code)
        if run_dir:
            image.save(run_dir / f'{tag}.png')
        data_url = siberi.image_to_data_url(image)

        t0 = time.time()
        try:
            raw = siberi.ask(system, user, images=[data_url] + extra_urls)
        except Exception as e:
            log(f'  ⚠️ {tag}: Siberi ошибка ({str(e)[:40]}), повтор {attempt}/{VISION_RETRIES}')
            continue
        ans = _check(raw, code, attempt, 'ans')
        if ans is None:
            log(f'  ⚠️ {tag}: ответ без JSON, повтор {attempt}/{VISION_RETRIES}')
            continue
        if ans['_seen'] or blind_ok(ans):
            log(f'  🧠 {tag}: {time.time() - t0:.0f} с ✓')
            return ans
        log(f'  ⚠️ {tag}: картинку НЕ видела, повтор {attempt}/{VISION_RETRIES}')
    raise NotSeen(f'мозг не увидел картинку за {VISION_RETRIES} попытки')


def _target(sub: dict, img: Image.Image, elements: List[dict], hint: str, refine: bool,
            run_dir: Optional[Path], log: Callable[[str], None], tag: str) -> Optional[dict]:
    """mark или x/y → {'x', 'y', 'box', 'source', 'mark'}; цель по сетке уточняется zoom'ом."""
    pt = resolve_point(sub, elements, img.size)
    if pt is None:
        return None
    x, y, source, box = pt
    if source == 'grid' and refine:
        cx, cy = int(x), int(y)
        try:
            ans2 = ask_seen(REFINE_SYSTEM, refine_prompt(hint),
                            lambda code: zoom_view(img, cx, cy, mark=(cx, cy), code=code),
                            tag, run_dir, log)
            x2, y2 = _num(ans2.get('x')), _num(ans2.get('y'))
            # Уточнение не должно уводить за пределы выреза — иначе это галлюцинация
            if ans2.get('found', True) and x2 is not None and y2 is not None \
                    and abs(x2 - cx) <= 120 and abs(y2 - cy) <= 120:
                x, y, source = x2, y2, 'zoom'
            else:
                log(f'  ⚠️ {tag}: уточнение не нашло цель — остаёмся на грубой оценке')
        except NotSeen as e:
            log(f'  ⚠️ {tag}: {e} — остаёмся на грубой оценке')
    mark = next((e for e in elements if e['box'] == box), None) if box else None
    return {'x': int(round(x)), 'y': int(round(y)), 'box': box, 'source': source, 'mark': mark}


def decide(img: Image.Image, system: str, user: str, elements: List[dict], hint: str,
           step: int = 100, refine: bool = True, run_dir: Optional[Path] = None,
           log: Callable[[str], None] = print, cursor: Optional[tuple] = None,
           region: Optional[list] = None) -> dict:
    """
    Один запрос решения → {'action', 'x', 'y', 'box', 'source', 'mark', 'to', 'text', 'keys',
    'scroll', 'seconds', 'answer', 'check', 'why', 'seen', 'raw'} в координатах скриншота.
    to — вторая цель для drag (куда отпустить). hint — что ищем (для промта уточнения).
    region — модели показываем только этот вырез (открытое всплывающее окно), координаты те же.
    """
    def blind_ok(a):
        return a.get('mark') is not None and (str(a.get('action')).lower() != 'drag'
                                              or a.get('to_mark') is not None)

    ans = ask_seen(system, user, lambda code: compose(img, elements, step=step, code=code, cursor=cursor,
                                                      region=region),
                   'marked', run_dir, log, blind_ok=blind_ok)
    return build_decision(ans, img, elements, hint, refine, run_dir, log)


def build_decision(ans: dict, img: Image.Image, elements: List[dict], hint: str, refine: bool,
                   run_dir: Optional[Path], log: Callable[[str], None]) -> dict:
    """Ответ модели (JSON) → действие в координатах скриншота: метки → рамки, сетка → zoom."""
    action = str(ans.get('action') or 'click').lower()
    if action not in ACTIONS:
        action = 'click'
    result = {
        'action': action, 'why': ans.get('why') or '', 'check': ans.get('check') or '',
        'answer': ans.get('answer') or '', 'raw': ans, 'seen': ans.get('_seen', False),
        'text': ans.get('text') or '', 'keys': ans.get('keys') or '', 'clear': bool(ans.get('clear')),
        'scroll': int(_num(ans.get('scroll')) or 0), 'seconds': _num(ans.get('seconds')) or 0,
        'x': None, 'y': None, 'box': None, 'source': None, 'mark': None, 'to': None, 'points': None,
        'also': _sub_actions(ans.get('also'), elements, img.size),
    }
    if action == 'stroke':
        result['points'] = _clean_points(ans.get('points'), img.size)
        if not result['points'] or len(result['points']) < 2:
            raise ValueError('stroke без линии: нужен список points из 2+ точек')
        return result
    if action not in POINT_ACTIONS and action != 'scroll':
        return result

    # У drag уточняем только то, ЧТО берём: в пояснении «почему» обычно названа точка
    # назначения, и zoom по нему уводил источник туда же («откуда» = «куда»).
    src_hint = (f'элемент, который нужно ЗАЖАТЬ и перетащить (бегунок, карточка, ручка) — '
                f'не место назначения. Цель задачи: {hint}' if action == 'drag'
                else f"{hint} ({result['why']})")
    src = _target(ans, img, elements, src_hint, refine, run_dir, log, 'zoom')
    if src is None:
        if action == 'scroll':
            return result                      # прокрутка там, где курсор
        raise ValueError(f'Siberi не дала ни метки, ни координат: {ans}')
    result.update(src)

    if action == 'drag':
        # Точку сброса zoom'ом не уточняем: это часто пустое место на шкале или в колонке,
        # а zoom ищет «объект» и цепляется за сам бегунок. Оценка по сетке и есть цель.
        to = _target({'mark': ans.get('to_mark'), 'x': ans.get('to_x'), 'y': ans.get('to_y')},
                     img, elements, '', False, run_dir, log, 'zoom_to')
        if to is None:
            raise ValueError('drag без точки, куда отпустить: укажи to_mark или to_x/to_y')
        if abs(to['x'] - src['x']) + abs(to['y'] - src['y']) < 10:
            raise ValueError(f"drag: точка «куда» ({to['x']}, {to['y']}) совпадает с «откуда» — "
                             f"откуда = сам перетаскиваемый элемент, куда = место назначения")
        result['to'] = to
    return result

def decide_text(system: str, user: str, img: Image.Image, elements: List[dict], hint: str,
                run_dir: Optional[Path] = None, log: Callable[[str], None] = print) -> Optional[dict]:
    """
    Решение БЕЗ скриншота: модель видит экран текстом (список элементов, изменения).
    → то же, что decide(), или None: модель попросила картинку (look), не указала метку
    для действия с точкой или ответила не по формату — тогда шаг решается со скриншотом.
    Координаты в текстовом режиме не принимаем: модель экран не видит, точку берём из рамки.
    """
    t0 = time.time()
    try:
        raw = siberi.ask(system, user)
    except Exception as e:
        log(f'  ⚠️ текстовый шаг: {str(e)[:60]} — беру скриншот')
        return None
    if run_dir:
        (run_dir / 'text_ans.txt').write_text(raw, encoding='utf-8')
    try:
        ans = parse_json(raw)
    except ValueError:
        log('  ⚠️ текстовый шаг: ответ без JSON — беру скриншот')
        return None
    action = str(ans.get('action') or '').lower()
    if action == 'look':
        log(f"  🖼 модели нужен скриншот: {str(ans.get('why') or '')[:90]}")
        return None
    ids = {str(e['id']) for e in elements}

    def has(key):
        return str(ans.get(key)).strip('[]') in ids

    if action == 'stroke' or (action in POINT_ACTIONS and not has('mark')) \
            or (action == 'drag' and not has('to_mark')):
        log(f'  🖼 текстовый шаг: «{action}» без элемента из списка — беру скриншот')
        return None
    for k in ('x', 'y', 'to_x', 'to_y', 'points'):
        ans.pop(k, None)
    def grounded(a):                     # доп. действие — по элементу из списка или клавишами
        return isinstance(a, dict) and (str(a.get('action')).lower() == 'key'
                                        or str(a.get('mark')).strip('[]') in ids)
    if isinstance(ans.get('also'), list):
        ans['also'] = [a for a in ans['also'] if grounded(a)]
    ans['_seen'] = False
    log(f'  📝 текстовый шаг: {time.time() - t0:.0f} с, без скриншота')
    return build_decision(ans, img, elements, hint, False, run_dir, log)


def locate(img: Image.Image, task: str, step: int = 100, refine: bool = True,
           run_dir: Optional[Path] = None, log: Callable[[str], None] = print,
           elements: Optional[List[dict]] = None, offset: Optional[tuple] = None) -> dict:
    """
    Одно действие под задачу. offset — сдвиг монитора для живого экрана
    (включает UI Automation), None — картинка из файла (только OCR).
    """
    if elements is None:
        t0 = time.time()
        elements = find_elements(img, offset)
        from . import retina
        src = retina.last_source if offset is not None else 'файл'
        log(f'  👁  {len(elements)} элементов за {time.time() - t0:.1f} с ({src}, скриншот {img.width}×{img.height})')
    user = locate_prompt(task, img.size, step, elements)
    if run_dir:
        (run_dir / 'prompt.txt').write_text(LOCATE_SYSTEM + '\n\n---\n\n' + user, encoding='utf-8')
    return decide(img, LOCATE_SYSTEM, user, elements, task, step=step, refine=refine,
                  run_dir=run_dir, log=log)


