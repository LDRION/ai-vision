"""
Поиск кликабельных кандидатов с точными рамками. ИИ не оценивает координаты —
он выбирает номер элемента, а точку клика берём из рамки.

Два источника:
  • UI Automation (только живой экран) — дерево доступности Windows: кнопки,
    ссылки, поля, пункты меню с именами, в т.ч. иконки без текста
    («Настройки», «Голосовой поиск»). Работает в Chrome/Яндекс Браузере/Edge,
    Electron и нативных приложениях.
  • OCR Windows (Windows.Media.Ocr, без моделей и GPU) — любой текст на
    картинке; единственный источник для скриншота из файла.

Иконки, которых нет ни в UIA, ни в OCR, — через сетку и шаг уточнения (zoom).
"""

import time
from typing import List, Optional, Tuple

import winocr
from PIL import Image, ImageOps

# OCR-строка может склеить соседние пункты меню («Почта Карты Маркет»):
# режем её по горизонтальным разрывам шире GAP_K высоты строки.
GAP_K = 1.6
MIN_SIDE = 6

UIA_TIME_LIMIT = 5.0
UIA_MAX_NODES = 5000
# Chrome/Electron строят дерево доступности страницы лениво — после первого
# обращения UIA-клиента. Первый обход видит только панель браузера, через
# секунду — всю страницу (проверено: 82 кнопки → 245 кнопок + 108 ссылок).
UIA_WARMUP = 1.2
UIA_ROLES = {
    'ButtonControl': 'кнопка', 'SplitButtonControl': 'кнопка', 'HyperlinkControl': 'ссылка',
    'EditControl': 'поле ввода', 'ComboBoxControl': 'список', 'CheckBoxControl': 'флажок',
    'RadioButtonControl': 'переключатель', 'MenuItemControl': 'пункт меню',
    'TabItemControl': 'вкладка', 'ListItemControl': 'элемент списка', 'TreeItemControl': 'элемент дерева',
}


def _words(img: Image.Image, lang: str):
    res = winocr.recognize_pil_sync(img, lang)
    for line in res.get('lines', []):
        ws = []
        for w in line.get('words', []):
            r = w['bounding_rect']
            ws.append({
                'text': w['text'],
                'box': [r['x'], r['y'], r['x'] + r['width'], r['y'] + r['height']],
            })
        if ws:
            yield ws


def _split_line(words: list) -> List[list]:
    words = sorted(words, key=lambda w: w['box'][0])
    h = max(w['box'][3] - w['box'][1] for w in words)
    groups, cur = [], [words[0]]
    for w in words[1:]:
        if w['box'][0] - cur[-1]['box'][2] > h * GAP_K:
            groups.append(cur)
            cur = [w]
        else:
            cur.append(w)
    groups.append(cur)
    return groups


def _area(r) -> float:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def _covered(a, b) -> float:
    """Доля площади a, лежащая внутри b."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = _area(a)
    return ix * iy / area if area else 1.0


def _is_number(text: str) -> bool:
    """«53», «2 м 49 с», «0,00 %», «А +51» (А — так OCR читает ▲): цифры, букв не больше."""
    digits = sum(c.isdigit() for c in text)
    return digits >= 1 and sum(c.isalpha() for c in text) <= max(2, digits)


def _is_label(text: str) -> bool:
    return sum(c.isalpha() for c in text) >= 3 and not any(c.isdigit() for c in text)


def pair_values(elements: List[dict]) -> List[dict]:
    """Плитки панелей: подпись, под ней число («Просмотры» / «53 ▲+51,43 %»). В списке элементов
    это две разные строки, и модель сопоставляла их по порядку — со сдвигом на колонку
    (визиты = 53, хотя 53 — просмотры). Склеиваем: число — значение подписи (e['value']),
    отдельной строкой его больше нет. Колонку таблицы (числа друг под другом) не трогаем."""
    texts = [e for e in elements if e.get('role') in (None, 'текст') and (e.get('text') or '').strip()]
    nums = [e for e in texts if _is_number(e['text'])]
    labels = [e for e in texts if _is_label(e['text'])]
    used = set()
    for lab in labels:
        x1, y1, x2, y2 = lab['box']
        h = max(8, y2 - y1)
        below = [n for n in nums if id(n) not in used and 0 <= n['box'][1] - y2 <= max(30, 2.5 * h)
                 and abs(n['box'][0] - x1) <= max(12, h)]
        if not below:
            continue
        first = min(below, key=lambda n: (n['box'][1], n['box'][0]))
        fx1, fy1, fx2, fy2 = first['box']
        if any(n is not first and 0 <= n['box'][1] - fy2 < 60 and abs(n['box'][0] - fx1) <= max(12, h)
               for n in nums):
            continue                                       # ниже ещё числа в той же колонке — таблица
        right = [o['box'][0] for o in labels if o is not lab and o['box'][0] > x2
                 and min(o['box'][3], y2) - max(o['box'][1], y1) > 0]
        end = min(right) if right else fx2 + 150
        line = [n for n in nums if id(n) not in used and fx1 <= n['box'][0] < end
                and min(n['box'][3], fy2) - max(n['box'][1], fy1) > 0]
        line = [n for n in line if not any(o is not n and _area(o['box']) > _area(n['box'])
                                           and _covered(n['box'], o['box']) >= 0.6 for o in line)]
        line.sort(key=lambda n: n['box'][0])
        lab['value'] = ' '.join(n['text'].strip() for n in line)
        used.update(id(n) for n in line)
        for n in line:                                     # и OCR-обрывки внутри склеенного
            used.update(id(o) for o in nums if _covered(o['box'], n['box']) >= 0.6)
    if not used:
        return elements
    return _number([e for e in elements if id(e) not in used])


def _number(elements: List[dict]) -> List[dict]:
    """Номера в порядке чтения: сверху вниз (строками по 12 px), слева направо."""
    elements.sort(key=lambda e: ((e['box'][1] + e['box'][3]) // 2 // 12, e['box'][0]))
    for i, e in enumerate(elements, 1):
        e['id'] = i
    return elements


def _ocr_raw(img: Image.Image, langs=('ru', 'en')) -> List[dict]:
    """
    Второй проход — по инвертированной картинке: светлый текст на тёмных кнопках
    OCR Windows часто пропускает. Второй язык даёт на том же тексте транслит-мусор
    («6ypxe» поверх «бирже») — его рамка почти целиком внутри уже найденной.
    """
    rgb = img.convert('RGB')
    found = []
    for src in (rgb, ImageOps.invert(rgb)):
        for lang in langs:
            for line in _words(src, lang):
                for g in _split_line(line):
                    box = [
                        min(w['box'][0] for w in g), min(w['box'][1] for w in g),
                        max(w['box'][2] for w in g), max(w['box'][3] for w in g),
                    ]
                    if box[2] - box[0] < MIN_SIDE or box[3] - box[1] < MIN_SIDE:
                        continue
                    if any(_covered(box, f['box']) > 0.5 for f in found):
                        continue
                    found.append({'text': ' '.join(w['text'] for w in g), 'role': 'текст',
                                  'box': [int(round(v)) for v in box]})
    return found


def ocr_elements(img: Image.Image, langs=('ru', 'en')) -> List[dict]:
    """Текстовые элементы: [{'id', 'text', 'role', 'box': [x1,y1,x2,y2]}]."""
    return _number(_ocr_raw(img, langs))


def uia_elements(size: Tuple[int, int], offset: Tuple[int, int] = (0, 0)) -> List[dict]:
    """
    Интерактивные элементы окна на переднем плане + панели задач, в координатах
    скриншота (offset — сдвиг монитора). Обход ограничен по времени и числу узлов:
    дерево браузера бывает на десятки тысяч элементов.
    """
    import uiautomation as auto

    W, H = size
    ox, oy = offset
    out = []
    roots = []
    from . import windows
    work = windows.work_window()                 # активное окно, но не наш лаунчер
    fg = auto.ControlFromHandle(work) if work else auto.GetForegroundControl()
    if fg:
        roots.append(fg.GetTopLevelControl() or fg)
    taskbar = auto.PaneControl(searchDepth=1, ClassName='Shell_TrayWnd')
    if taskbar.Exists(0, 0):
        roots.append(taskbar)

    if roots and roots[0].ClassName == 'Chrome_WidgetWin_1':
        for i, _ in enumerate(auto.WalkControl(roots[0], maxDepth=12)):
            if i > 200:
                break
        time.sleep(UIA_WARMUP)

    deadline = time.time() + UIA_TIME_LIMIT
    for root in roots:
        on_taskbar = root is taskbar
        visited = 0
        for c, _depth in auto.WalkControl(root, includeTop=False, maxDepth=60):
            visited += 1
            if visited > UIA_MAX_NODES or time.time() > deadline:
                break
            role = UIA_ROLES.get(c.ControlTypeName)
            if not role:
                continue
            try:
                if c.IsOffscreen:
                    continue
                r = c.BoundingRectangle
                name = (c.Name or '').strip()
            except Exception:
                continue
            box = [r.left - ox, r.top - oy, r.right - ox, r.bottom - oy]
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                continue
            if box[0] < 0 or box[1] < 0 or box[2] > W or box[3] > H:
                continue
            if _area(box) > 0.25 * W * H or (not name and role != 'поле ввода'):
                continue
            el = {'text': ' '.join(name.split())[:80], 'role': role, 'box': box}
            if on_taskbar:
                el['taskbar'] = True
            out.append(el)
    return out


def foreground_title() -> str:
    """Заголовок окна на переднем плане — контекст для агента («Входящие — Почта»)."""
    try:
        import uiautomation as auto
        fg = auto.GetForegroundControl()
        top = fg.GetTopLevelControl() if fg else None
        return ((top or fg).Name or '').strip() if fg else ''
    except Exception:
        return ''


def find_elements(img: Image.Image, offset: Optional[Tuple[int, int]] = None) -> List[dict]:
    """
    offset задан → живой экран: UIA + OCR. Текст OCR, уже покрытый небольшим
    элементом UIA (кнопка со своей подписью), не дублируем.
    offset None → картинка из файла: только OCR.
    """
    ocr = _ocr_raw(img)
    if offset is None:
        return _number(ocr)
    from . import retina
    uia = retina.elements(img.size, offset)      # живой индекс Retina: ~20 мс вместо ~4 с обхода
    retina.last_source = 'retina' if uia else 'uia'
    if not uia:
        try:
            uia = uia_elements(img.size, offset)
        except Exception:
            uia = []
    merged = list(uia)
    for o in ocr:
        if any(_covered(o['box'], u['box']) > 0.6 and _area(u['box']) < 6 * _area(o['box']) for u in uia):
            continue
        merged.append(o)
    return _number(merged)
