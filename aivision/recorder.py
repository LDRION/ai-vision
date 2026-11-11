"""
Запись экрана во время прогона сценария — для таймлайна «как в Clipchamp».

Фоновый поток снимает монитор (mss) с заданной частотой и пишет mp4 (cv2).
Аппаратный курсор в снимок не попадает, поэтому рисуем его сами по реальной
позиции мыши — на дорожке видно, что делает курсор. Рядом пишем meta.json:
частота, размер, длительность и метки шагов (когда агент начал каждое действие),
чтобы таймлайн показал отрезки шагов.

Запись и прогон идут параллельно; в кадрах — ровно то, что было на экране.
"""

import json
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
from pynput import mouse

from .capture import _mss

MAX_WIDTH = 1366          # кадр ужимаем по ширине — файл меньше, кодирование быстрее
CURSOR_R = 9


class ScreenRecorder:
    """
    recorder = ScreenRecorder(run_dir, monitor=1, fps=10).start()
    recorder.mark_step(n, 'click [1] «Почта»')   # из on_step агента
    recorder.stop()
    """

    def __init__(self, run_dir: Path, monitor: int = 1, fps: int = 10):
        self.run_dir = Path(run_dir)
        self.monitor = monitor
        self.fps = fps
        self.path = self.run_dir / 'record.mp4'
        self.meta_path = self.run_dir / 'record.json'
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0
        self._steps: List[dict] = []
        self._cursor: List[list] = []
        self.size: Tuple[int, int] = (0, 0)
        self.scale = 1.0
        self.frames = 0
        self._mouse = mouse.Controller()

    def start(self) -> 'ScreenRecorder':
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._t0 = time.perf_counter()        # отсчёт до старта потока — метки до 1-го кадра = ~0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def mark_step(self, n: int, label: str, kind: str = 'act'):
        """kind: 'think' — Siberi принимает решение, 'act' — исполняется действие."""
        self._steps.append({'n': n, 'label': label, 'kind': kind,
                            't': round(time.perf_counter() - self._t0, 3)})

    def _loop(self):
        with _mss() as sct:
            mon = sct.monitors[self.monitor]
            left, top = mon['left'], mon['top']
            w, h = mon['width'], mon['height']
            self.scale = min(1.0, MAX_WIDTH / w)
            ow, oh = int(w * self.scale), int(h * self.scale)
            self.size = (ow, oh)
            writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*'mp4v'),
                                     self.fps, (ow, oh))
            period = 1.0 / self.fps                    # _t0 задан в start()
            try:
                while not self._stop.is_set():
                    tick = time.perf_counter()
                    frame = np.asarray(sct.grab(mon))[:, :, :3]        # BGRA → BGR
                    if self.scale < 1.0:
                        frame = cv2.resize(frame, (ow, oh), interpolation=cv2.INTER_AREA)
                    frame = np.ascontiguousarray(frame)
                    t = tick - self._t0
                    cx, cy = self._mouse.position
                    px, py = int((cx - left) * self.scale), int((cy - top) * self.scale)
                    if 0 <= px < ow and 0 <= py < oh:
                        self._draw_cursor(frame, px, py)
                    self._cursor.append([round(t, 3), px, py])
                    writer.write(frame)
                    self.frames += 1
                    sleep = period - (time.perf_counter() - tick)
                    if sleep > 0:
                        time.sleep(sleep)
            finally:
                writer.release()
                self._write_meta()

    @staticmethod
    def _draw_cursor(frame, x, y):
        # белый кружок с чёрной обводкой + точка — заметно на любом фоне
        cv2.circle(frame, (x, y), CURSOR_R, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.circle(frame, (x, y), CURSOR_R, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.circle(frame, (x, y), 2, (0, 0, 255), -1, cv2.LINE_AA)

    def _write_meta(self):
        duration = round(self.frames / self.fps, 3) if self.fps else 0
        meta = {'fps': self.fps, 'width': self.size[0], 'height': self.size[1],
                'scale': round(self.scale, 4), 'frames': self.frames, 'duration': duration,
                'monitor': self.monitor, 'steps': self._steps, 'cursor': self._cursor}
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
