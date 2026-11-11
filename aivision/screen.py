"""
Живой экран для многошагового агента: снимки, ожидание, пока интерфейс
успокоится после действия, сравнение «до/после» и исполнение событий.

Интерфейс среды (его же реализует виртуальный экран в tests/fake_env.py):
    grab() → (img, offset)          title() → str        window() → id окна
    cursor() → (x, y)               elements(img, offset) → [...]
    perform(events)                 settle(before) → (img, offset)
"""

import math
import time
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from .capture import foreground_monitor, grab
from .detect import find_elements, foreground_title
from .motion import TAKEOVER_PX, Aborted, foreground_window, play

PIXEL_DELTA = 28            # разница канала, после которой пиксель считается изменённым


def _arr(img: Image.Image, scale: int = 2) -> np.ndarray:
    small = img.convert('RGB').reduce(scale) if scale > 1 else img.convert('RGB')
    return np.asarray(small, dtype=np.int16)


def changed(a: Image.Image, b: Image.Image, box: Optional[list] = None,
            scale: int = 2) -> Tuple[float, Optional[Tuple[int, int, int, int]]]:
    """Доля изменившихся пикселей и рамка изменений (в координатах скриншота)."""
    if a.size != b.size:
        return 1.0, (0, 0, b.width, b.height)
    if box:
        a, b = a.crop(box), b.crop(box)
    x0, y0 = (box[0], box[1]) if box else (0, 0)
    d = np.abs(_arr(a, scale) - _arr(b, scale)).max(axis=2) > PIXEL_DELTA
    frac = float(d.mean()) if d.size else 0.0
    if not d.any():
        return frac, None
    ys, xs = np.nonzero(d)
    return frac, (x0 + int(xs.min()) * scale, y0 + int(ys.min()) * scale,
                  x0 + int(xs.max() + 1) * scale, y0 + int(ys.max() + 1) * scale)


def describe_change(before: Image.Image, after: Image.Image) -> str:
    frac, bb = changed(before, after)
    if frac < 0.0002:
        return 'экран НЕ изменился'
    if frac < 0.02:
        cx, cy = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
        return f'изменилась небольшая область около ({cx}, {cy})'
    return f'экран заметно изменился ({frac:.0%} площади)'


def target_moved(before: Image.Image, now: Image.Image, x: int, y: int,
                 box: Optional[list] = None, margin: int = 40) -> bool:
    """Пока Siberi думала, область цели изменилась (прокрутка, догрузка) — клик может уйти мимо."""
    if box:
        x1, y1, x2, y2 = box
    else:
        x1, y1, x2, y2 = x, y, x, y
    region = [max(0, x1 - margin), max(0, y1 - margin),
              min(now.width, x2 + margin), min(now.height, y2 + margin)]
    frac, _ = changed(before, now, region, scale=1)
    return frac > 0.03


class RealEnv:
    """Настоящий экран и мышь. Пользователь двинул мышь между шагами — стоп."""

    def __init__(self, monitor: int = 0, speed: float = 1.0, vision: bool = True):
        """
        monitor — номер в нумерации mss; 0 = авто: агент смотрит на монитор рабочего окна
        (активного, но не нашего лаунчера) и переходит за ним, если окно сменило монитор
        или агент переключился на программу на другом экране.
        vision=False — без Retina (игра: только скриншоты, а OCR на 10 кадрах/с отнимал бы у неё процессор).
        """
        from pynput import mouse
        self.follow = not monitor
        self.monitor = monitor or self._work_monitor()
        self.speed = speed
        self._mouse = mouse.Controller()
        self._left_at: Optional[Tuple[int, int]] = None
        self.last_find = ''          # ok | ambiguous | none | down — итог последнего find()
        self.vision = vision
        if vision:
            from . import services
            services.ensure(wait=8.0)    # встроенная Retina и Ollama: подняты лаунчером или сейчас

    @staticmethod
    def _work_monitor() -> int:
        from . import windows
        from .capture import window_monitor
        w = windows.work_window()
        return window_monitor(w) if w else foreground_monitor()

    def grab(self):
        if getattr(self, 'vision', True):
            from . import services
            services.wake()          # зрение уснуло посреди долгой задачи (ждали подсказку) — будим
        if self.follow:
            self.monitor = self._work_monitor()
        return grab(self.monitor)

    def title(self) -> str:
        from . import windows
        w = windows.work_window()
        return windows.title(w) if w else foreground_title()

    def window(self) -> int:
        from . import windows
        return windows.work_window() or foreground_window()

    def open_windows(self) -> str:
        """Открытые программы и их мониторы — модели, чтобы она не искала окно наугад."""
        from . import windows
        return windows.describe(windows.list_windows(), here=self.monitor)

    def focus_window(self, name: str) -> Optional[str]:
        """Переключиться на окно программы (развернуть, вывести вперёд) — без панели задач.
        → описание окна или None, если такого окна нет."""
        from . import windows
        self._guard()                    # человек взял мышь — окно у него не отбираем
        w = windows.find(name)
        if not w:
            return None
        ok = windows.focus(w['hwnd'])
        self.monitor = w['monitor']
        time.sleep(0.3)
        self._left_at = self._mouse.position
        return (f"{windows.app_name(w)} «{w['title'][:60]}» · монитор {w['monitor']}"
                + ('' if ok else ' (Windows не отдал фокус — окно может быть позади)'))

    def cursor(self):
        return self._mouse.position

    def elements(self, img, offset):
        from . import icons
        return icons.annotate(find_elements(img, offset), self.unnamed(img, offset), img, self.app())

    def mark(self) -> Optional[int]:
        """Точка отсчёта в журнале Retina перед действием (None — Retina без журнала)."""
        from . import retina
        return retina.mark()

    def changes(self, since: int):
        """Что Retina заметила на экране после since: «+ кнопка «Отправить»», «focus → …»."""
        from . import retina
        return retina.changes(since)

    def app(self) -> str:
        from .icons import process_name
        return process_name(self.window())

    def unnamed(self, img, offset):
        """Кнопки-иконки без подписи в окне в фокусе (из Retina), координаты монитора."""
        from . import retina
        return retina.unnamed(img.size, offset)

    def probe(self, box, wait: float = 1.6) -> str:
        """Навести курсор на иконку и прочитать всплывающую подсказку. '' — подсказки нет.

        Подсказка — новый текст рядом с курсором, которого до наведения не было, и не
        внутри самой иконки (там при наведении OCR может «увидеть» глиф).
        """
        from . import retina
        from .motion import build_events
        x0, y0, _w, _h = self._area()
        bx = [box[0] + x0, box[1] + y0, box[2] + x0, box[3] + y0]
        cx, cy = (bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2
        region = (cx - 280, cy - 160, 560, 320)
        before = retina.texts(region)
        if before is None:
            return ''
        seen = {' '.join(t.lower().split()) for t, _ in before}
        self.perform(build_events(self.cursor(), {'action': 'hover', 'x': cx, 'y': cy, 'box': bx}))
        retina.settled(region, quiet_ms=200, timeout_ms=int(wait * 800), expect=True)
        t_end = time.time() + wait
        while True:
            fresh = []
            for t, (x, y, w, h) in retina.texts(region) or []:
                if ' '.join(t.lower().split()) in seen or not 2 <= len(t) <= 80:
                    continue
                tx, ty = x + w // 2, y + h // 2
                if bx[0] <= tx <= bx[2] and bx[1] <= ty <= bx[3]:
                    continue
                fresh.append((math.hypot(tx - cx, ty - cy), t))
            if fresh:
                return min(fresh)[1]
            if time.time() >= t_end:
                return ''
            time.sleep(0.12)

    def _area(self):
        from .capture import monitors
        m = monitors()[self.monitor]
        return m['left'], m['top'], m['width'], m['height']

    def find(self, text: str, role: str = '') -> Optional[dict]:
        """Цель по подписи из живого индекса Retina — без скриншота и модели.

        → элемент в координатах монитора ({'text', 'role', 'box', 'score'}) или None:
        Retina не запущена, цель не найдена или их несколько равных (наугад не кликаем).
        """
        from . import retina
        x0, y0, w, h = area = self._area()
        m, self.last_find = retina.lookup(text, area, role)
        if not m:
            return None
        x, y, bw, bh = m['rect']
        return {'text': m['name'], 'role': retina.ROLES.get(m['role'], 'текст'), 'score': m['score'],
                'box': [x - x0, y - y0, x + bw - x0, y + bh - y0]}

    def resume(self):
        """После паузы на подсказку пользователь двигал мышь сам — это не перехват."""
        self._left_at = None

    def _guard(self):
        """Мышь сдвинулась с места, где её оставил агент, — за компьютером человек: стоп."""
        if self._left_at:
            cx, cy = self._mouse.position
            if math.hypot(cx - self._left_at[0], cy - self._left_at[1]) > TAKEOVER_PX:
                raise Aborted('пользователь взял мышь между шагами — остановлено')

    def perform(self, events):
        self._guard()
        if events:
            play(events, speed=self.speed)
        self._left_at = self._mouse.position

    def settle(self, before: Image.Image, timeout: float = 8.0, quiet: float = 0.7,
               late: float = 2.5):
        """
        Ждём, пока экран перестанет меняться (quiet секунд подряд). Если ничего
        не изменилось вообще — ещё до late секунд ловим запоздалую реакцию
        (сеть, анимация открытия). Видео/анимации — выходим по timeout.

        Retina запущена — ждём по её событиям, а не опросом скриншотов: она видит
        конец изменений на мониторе сразу (тишина 0.3 с вместо 0.35 + 0.7 с).
        """
        from . import retina
        area = self._area()
        r = retina.settled(area, quiet_ms=300, timeout_ms=int(late * 1000), expect=True)
        if r is not None:
            if r.get('changed') and not r.get('settled'):          # ещё меняется — дожидаемся тишины
                retina.settled(area, quiet_ms=300, timeout_ms=int(max(0.5, timeout - late) * 1000),
                               expect=False)
            return self.grab()
        time.sleep(0.35)
        t_end = time.time() + timeout
        img, offset = self.grab()
        last, still_since = img, time.time()
        late_end = time.time() + late
        while time.time() < t_end:
            time.sleep(0.15)
            img, offset = self.grab()
            if changed(last, img)[0] > 0.0005:
                still_since = time.time()
            elif time.time() - still_since >= quiet:
                if changed(before, img)[0] >= 0.0002 or time.time() >= late_end:
                    break
            last = img
        return img, offset
