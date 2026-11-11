"""
Наборы значков: эмодзи-реакции, теги-картинки, «чипсы» — плотные ряды цветных значков
на ровном фоне. Ни дерево доступности, ни OCR их не видят (в Telegram поле «Доступные
реакции» — одна картинка), и модель кликает по заголовку над полем вместо самого поля.

Ищем по пикселям, без модели: пёстрое пятно (цветные пиксели разных оттенков вперемешку
с ровным фоном поля). Одноцветный текст-ссылка, ползунок, переключатель — один оттенок;
фото и текстуры — без ровного фона; аватарки и значки на панели задач — поодиночке.

    elements = add_sets(img, elements)   # + {'role': 'значки', 'text': 'набор из ~87 значков', ...}
"""

from typing import List

import numpy as np
from PIL import Image

SAT, VAL = 70, 60            # цветной пиксель: насыщенность и яркость (0–255)
CELL = 6                     # ячейка анализа, px: в ней хватает цветного — ячейка «пёстрая»
CELL_FILL = 0.1
MIN_W, MIN_H = 60, 14        # набор не меньше трёх значков в ряд
FILL_LO, FILL_HI = 0.08, 0.75   # доля цветного в рамке набора: значки с промежутками
FLAT = 4                     # фон между значками ровный: отклонение яркости не больше
FLAT_SHARE = 0.8             # …у такой доли пикселей фона
HUES = 2                     # оттенков (из 12 секторов круга) хотя бы столько…
HUE_SHARE = 0.05             # …и у каждого не меньше этой доли цветных пикселей
MIN_BLOB, MAX_BLOB = 6, 40   # пятно-значок после сужения, px
BLOB_FILL = 0.35             # и сплошное, а не росчерк
MIN_SET = 5                  # значков в наборе не меньше
MIN_GLYPH, MAX_GLYPH = 9, 32   # значки (после сужения) крупнее букв и мельче аватарок
GLYPH_FILL = 0.5             # и сплошные: у типичного значка закрашено больше половины рамки
OCR_INSIDE = 0.7             # OCR-«текст» внутри набора — мусор от распознавания значков


def find_sets(img: Image.Image) -> List[dict]:
    """→ [{'box': [x1, y1, x2, y2], 'count': ≈N}] — найденные наборы значков."""
    import cv2
    rgb = np.asarray(img.convert('RGB'))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    color = ((hsv[..., 1] > SAT) & (hsv[..., 2] > VAL)).astype(np.uint8)
    solid = cv2.erode(color, np.ones((3, 3), np.uint8))          # цветная кайма ClearType у букв — прочь
    H, W = solid.shape
    h, w = H // CELL, W // CELL
    cells = solid[:h * CELL, :w * CELL].reshape(h, CELL, w, CELL).mean(axis=(1, 3)) >= CELL_FILL
    cells = cv2.morphologyEx(cells.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(cells, connectivity=8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.int16)
    near = cv2.dilate(color, np.ones((5, 5), np.uint8)).astype(bool)    # контуры, белки глаз
    out = []
    for cx, cy, cw, ch, _ in stats[1:n]:
        found = _set_in([int(cx * CELL), int(cy * CELL), int((cx + cw) * CELL), int((cy + ch) * CELL)],
                        solid, near, gray, hsv[..., 0])
        if found:
            out.append(found)
    return out


def _set_in(box, solid, near, gray, hue):
    """Пёстрое пятно box — набор значков? → {'box', 'count'} по самим значкам или None."""
    import cv2
    x1, y1, x2, y2 = box
    if x2 - x1 < MIN_W or y2 - y1 < MIN_H:
        return None
    n, _, stats, _ = cv2.connectedComponentsWithStats(solid[y1:y2, x1:x2], connectivity=8)
    blobs = [(x, y, w, h, a / (w * h)) for x, y, w, h, a in stats[1:n]   # штрихи букв тоньше значков
             if MIN_BLOB <= max(w, h) <= MAX_BLOB and min(w, h) >= MIN_BLOB // 2 + 1 and a >= BLOB_FILL * w * h]
    if len(blobs) < MIN_SET:
        return None
    size = np.median([max(w, h) for _, _, w, h, _ in blobs])
    if not MIN_GLYPH <= size <= MAX_GLYPH or np.median([f for *_, f in blobs]) < GLYPH_FILL:
        return None                                    # буквы и статусы мельче и дырявее, аватарки крупнее
    bx1 = x1 + min(x for x, *_ in blobs)
    by1 = y1 + min(y for _, y, *_ in blobs)
    bx2 = x1 + max(x + w for x, _, w, _, _ in blobs)
    by2 = y1 + max(y + h for _, y, _, h, _ in blobs)
    if bx2 - bx1 < MIN_W or by2 - by1 < MIN_H:
        return None
    c = solid[by1:by2, bx1:bx2].astype(bool)
    if not FILL_LO <= c.mean() <= FILL_HI:
        return None
    bg = gray[by1:by2, bx1:bx2][~near[by1:by2, bx1:bx2]]
    if bg.size < 50 or (np.abs(bg - np.median(bg)) <= FLAT).mean() < FLAT_SHARE:
        return None                                    # фон не ровный — фото, текстура
    hist = np.bincount((hue[by1:by2, bx1:bx2][c].astype(int) * 12 // 180).clip(0, 11), minlength=12)
    if int((hist >= HUE_SHARE * hist.sum()).sum()) < HUES:
        return None                                    # один оттенок — ссылка, ползунок, логотип
    pad = 4
    return {'box': [int(max(0, bx1 - pad)), int(max(0, by1 - pad)), int(bx2 + pad), int(by2 + pad)], 'count': len(blobs)}


def _inside(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def add_sets(img: Image.Image, elements: List[dict]) -> List[dict]:
    """Добавить найденные наборы значков в список элементов (OCR-мусор внутри — убрать)."""
    try:
        sets = find_sets(img)
    except Exception:
        return elements
    if not sets:
        return elements
    from .detect import _number
    kept = [e for e in elements
            if not (e.get('role') in (None, 'текст') and any(_inside(e['box'], s['box']) >= OCR_INSIDE for s in sets))]
    for s in sets:
        kept.append({'text': f"набор из ~{s['count']} значков", 'role': 'значки', 'box': s['box'], 'glyphs': s['count']})
    return _number(kept)
