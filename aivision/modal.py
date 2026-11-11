"""
Всплывающие окна (модальные диалоги): по действию в центре открылось окно, а всё
остальное затемнилось. Пока оно открыто, работать можно только внутри: клик по
затемнённому фону закрывает окно и возвращает на шаг назад.

Определяем по пикселям, без модели. Сравниваем экран с тем, что был до действия:
  • затемнение — почти все пиксели вокруг стали темнее В ОДНО И ТО ЖЕ число раз
    (так рисуется полупрозрачная подложка);
  • окно — самая большая область нового содержимого (изменилась, но не потемнела)
    внутри затемнения.
Пока окно открыто, проверяем его по тому же снимку «до»: фон вокруг всё ещё темнее
— окно на месте; посветлел обратно — окно закрыли. Окно поверх окна — стопка.

    tracker = ModalTracker()
    box = tracker.update(prev_img, img)     # [x1, y1, x2, y2] активного окна или None
"""

from typing import List, Optional

import numpy as np
from PIL import Image

SCALE = 8                 # анализ на уменьшенной копии: экран 1920×1080 → 240×135
DARK = 35                 # темнее этого (0–255) не понять, потемнело ли
DIM_LO, DIM_HI = 0.2, 0.88   # затемнение: яркость упала до 20–88 % от прежней
DIM_SPREAD = 0.12         # и упала одинаково: отклонение от медианы не больше этого
MIN_DIM_SHARE = 0.45      # затемнено не меньше этой доли экрана вокруг окна
MIN_SIDE = 140            # окно не меньше 140 px по каждой стороне
MIN_FILL = 0.55           # новое содержимое занимает больше половины рамки окна
MAX_AREA = 0.7            # окно — не весь экран
RING_DIM = 0.6            # вокруг окна затемнено хотя бы столько


def _gray(img: Image.Image) -> np.ndarray:
    w, h = img.size
    return np.asarray(img.convert('L').resize((max(1, w // SCALE), max(1, h // SCALE)), Image.BILINEAR),
                      dtype=np.float32)


def _dimmed(a: np.ndarray, b: np.ndarray):
    """→ (маска затемнённых пикселей, маска «видно, потемнело ли») или None, если экран
    не затемнялся равномерно."""
    known = a > DARK
    if known.mean() < 0.2:
        return None
    r = (b + 1.0) / (a + 1.0)
    cand = known & (r >= DIM_LO) & (r <= DIM_HI)
    if cand.sum() < 0.25 * known.sum():
        return None
    med = float(np.median(r[cand]))
    return known & (np.abs(r - med) <= DIM_SPREAD), known


def detect(before: Image.Image, after: Image.Image) -> Optional[List[int]]:
    """Открылось ли всплывающее окно между двумя снимками. → [x1, y1, x2, y2] (пиксели
    снимка) или None."""
    if before is None or after is None or before.size != after.size:
        return None
    import cv2
    a, b = _gray(before), _gray(after)
    dm = _dimmed(a, b)
    if dm is None:
        return None
    dim, known = dm
    # Верхний слой — то, что НЕ потемнело (новое или прежнее светлое: диалог поверх белой
    # панели не меняет пикселей), и его со всех сторон обступает затемнение.
    top = ~dim & (known | (b > DARK))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(top.astype(np.uint8), connectivity=8)
    H, W = a.shape
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w * SCALE < MIN_SIDE or h * SCALE < MIN_SIDE or w * h > MAX_AREA * W * H:
            continue
        if area < MIN_FILL * w * h:
            continue
        if x == 0 or y == 0 or x + w >= W or y + h >= H:
            continue                                     # прижато к краю — соседнее окно, панель
        if _ring_share(dim, known, (x, y, x + w, y + h)) < RING_DIM:
            continue                                     # вокруг не затемнено — это не всплывающее окно
        if best is None or area > best[4]:
            best = (x, y, w, h, area)
    if best is None:
        return None
    x, y, w, h, _ = best
    if _outside_share(dim, known, (x, y, x + w, y + h)) < MIN_DIM_SHARE:
        return None
    return [int(x * SCALE), int(y * SCALE), int((x + w) * SCALE), int((y + h) * SCALE)]


def _ring_share(dim: np.ndarray, known: np.ndarray, box, width: int = 3) -> float:
    """Доля затемнённого в рамке шириной width вокруг окна."""
    x1, y1, x2, y2 = box
    H, W = dim.shape
    ring = np.zeros_like(dim)
    ring[max(0, y1 - width):min(H, y2 + width), max(0, x1 - width):min(W, x2 + width)] = True
    ring[y1:y2, x1:x2] = False
    ring &= known
    total = ring.sum()
    return float((dim & ring).sum() / total) if total else 0.0


def _outside_share(dim: np.ndarray, known: np.ndarray, box) -> float:
    """Какая доля «видимого» фона вокруг рамки затемнена."""
    x1, y1, x2, y2 = box
    out = known.copy()
    out[y1:y2, x1:x2] = False
    total = out.sum()
    return float((dim & out).sum() / total) if total else 0.0


def still_open(ref: Image.Image, now: Image.Image, box: List[int]) -> bool:
    """Окно всё ещё открыто: фон вокруг рамки по-прежнему темнее, чем на снимке «до»."""
    if ref is None or now is None or ref.size != now.size:
        return False
    dm = _dimmed(_gray(ref), _gray(now))
    if dm is None:
        return False
    dim, known = dm
    small = [v // SCALE for v in box]
    return _outside_share(dim, known, small) >= MIN_DIM_SHARE


def grow(ref: Image.Image, now: Image.Image, box: List[int]) -> List[int]:
    """Поверх окна открылась панель (выбор эмодзи, меню) и вылезла за его край — она тоже часть
    окна: к рамке добавляем незатемнённое, что касается окна. Сжать рамку не может."""
    import cv2
    a, b = _gray(ref), _gray(now)
    dm = _dimmed(a, b)
    if dm is None:
        return box
    dim, known = dm
    top = ~dim & (known | (b > DARK))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(top.astype(np.uint8), connectivity=8)
    H, W = a.shape
    x1, y1, x2, y2 = [v // SCALE for v in box]
    for i in set(np.unique(labels[y1:y2, x1:x2]).tolist()) - {0}:
        x, y, w, h, _ = stats[i]
        if x == 0 or y == 0 or x + w >= W or y + h >= H or w * h > MAX_AREA * W * H:
            continue                                     # прижато к краю или во весь экран — не панель
        x1, y1, x2, y2 = min(x1, x), min(y1, y), max(x2, x + w), max(y2, y + h)
    if (x2 - x1) * (y2 - y1) > MAX_AREA * W * H:
        return box
    return [int(min(box[0], x1 * SCALE)), int(min(box[1], y1 * SCALE)),
            int(max(box[2], x2 * SCALE)), int(max(box[3], y2 * SCALE))]


class ModalTracker:
    """Стопка открытых всплывающих окон в агенте: верхнее — то, где сейчас работать."""

    def __init__(self):
        self.stack: List[dict] = []

    @property
    def box(self) -> Optional[List[int]]:
        if not self.stack:
            return None
        return self.stack[-1].get('view') or self.stack[-1]['box']

    def update(self, before: Optional[Image.Image], now: Image.Image) -> Optional[List[int]]:
        """Новый снимок экрана (before — снимок прошлого шага). → рамка активного окна или None
        (с открытыми поверх него панелями)."""
        while self.stack and not still_open(self.stack[-1]['ref'], now, self.stack[-1]['box']):
            self.stack.pop()                                   # закрыли — вернулись к прежнему
        box = detect(before, now) if before is not None else None
        if box and not (self.stack and _same_box(box, self.stack[-1]['box'])):
            self.stack.append({'box': box, 'ref': before})
        if self.stack:
            top = self.stack[-1]
            top['view'] = grow(top['ref'], now, top['box'])
        return self.box

    def reset(self):
        self.stack.clear()


def _same_box(a, b, tol: int = 24) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def inside(box: List[int], e_box, margin: int = 6) -> bool:
    """Центр элемента внутри рамки окна."""
    cx, cy = (e_box[0] + e_box[2]) / 2, (e_box[1] + e_box[3]) / 2
    return box[0] - margin <= cx <= box[2] + margin and box[1] - margin <= cy <= box[3] + margin
