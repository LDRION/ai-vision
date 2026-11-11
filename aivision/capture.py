"""Снимок экрана в ФИЗИЧЕСКИХ пикселях — тех же, в которых двигается курсор."""

import ctypes
import sys
from typing import List, Tuple

import mss
from PIL import Image


def dpi_aware():
    """
    Без этого при масштабе 125–200 % Windows отдаёт процессу «логические»
    координаты: скриншот и курсор разъедутся. Вызывать до первого обращения
    к экрану и мыши.
    """
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _mss():
    return mss.MSS() if hasattr(mss, 'MSS') else mss.mss()


def monitors() -> List[dict]:
    """Мониторы в нумерации mss: [0] — все вместе, [1] — основной, дальше остальные."""
    with _mss() as s:
        return [dict(m) for m in s.monitors]


def monitor_at(x: int, y: int) -> int:
    """Номер монитора (mss), на котором точка; вне всех — основной (1)."""
    for i, m in enumerate(monitors()[1:], 1):
        if m['left'] <= x < m['left'] + m['width'] and m['top'] <= y < m['top'] + m['height']:
            return i
    return 1


def window_monitor(hwnd: int) -> int:
    """Монитор, где центр окна. Окно на стыке экранов — по большей части."""
    if sys.platform != 'win32' or not hwnd:
        return 1
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return monitor_at((r.left + r.right) // 2, (r.top + r.bottom) // 2)


def foreground_monitor() -> int:
    """Монитор активного окна — «авто»: агент смотрит туда, где работает пользователь."""
    if sys.platform != 'win32':
        return 1
    return window_monitor(ctypes.windll.user32.GetForegroundWindow())


def grab(monitor: int = 1) -> Tuple[Image.Image, Tuple[int, int]]:
    """→ (снимок, (left, top)) — сдвиг монитора в общих координатах рабочего стола."""
    with _mss() as s:
        m = s.monitors[monitor]
        shot = s.grab(m)
        img = Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
        return img, (m['left'], m['top'])
