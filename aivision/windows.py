"""
Окна рабочего стола: какие открыты, на каком мониторе, и как переключиться на нужное.

Агент не должен «искать» приложение кликами по панели задач: клик по кнопке уже
активного окна его СВОРАЧИВАЕТ (так агент открывал-закрывал Telegram по кругу), а
окно на другом мониторе он не видит вовсе. Здесь — то же, что знает Windows:
список окон с мониторами, «рабочее» окно (активное, но не наш лаунчер) и
переключение фокуса на окно по названию программы или заголовку.
"""

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional

OWN_TITLE = 'AI Vision'          # окна лаунчера и служб AI Vision — не рабочие
_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_DWMWA_CLOAKED = 14
_SKIP_CLASSES = {'Shell_TrayWnd', 'Shell_SecondaryTrayWnd', 'Progman', 'WorkerW', 'Windows.UI.Core.CoreWindow'}
_SKIP_PROCESSES = {'textinputhost.exe', 'shellexperiencehost.exe', 'searchhost.exe', 'startmenuexperiencehost.exe',
                   'lockapp.exe', 'nvidia overlay.exe', 'gamebar.exe'}

if sys.platform == 'win32':
    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetWindow.restype = wintypes.HWND
    _u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]


def _fold(s: str) -> str:
    return ' '.join(re.sub(r'[^\w]+', ' ', (s or '').lower().replace('ё', 'е')).split())


def title(hwnd: int) -> str:
    """Заголовок без невидимых символов направления текста (Telegram ставит их вокруг имён)."""
    import unicodedata
    buf = ctypes.create_unicode_buffer(512)
    _u32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
    return ''.join(ch for ch in buf.value if unicodedata.category(ch) != 'Cf')


def process(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    h = _k32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ''
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return ''
    finally:
        _k32.CloseHandle(h)


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value


def own(hwnd: int, ttl: str = None) -> bool:
    """Окно AI Vision: наш процесс, его дети (Retina, WebView2) или заголовок «AI Vision…»."""
    t = title(hwnd) if ttl is None else ttl
    return t.startswith(OWN_TITLE) or _pid(hwnd) == os.getpid()


def _cloaked(hwnd: int) -> bool:
    val = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), _DWMWA_CLOAKED,
                                                   ctypes.byref(val), ctypes.sizeof(val))
    except OSError:
        return False
    return bool(val.value)


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def _app_window(hwnd: int) -> bool:
    """Окно, которое человек считает «программой»: видимое, с заголовком, не служебное."""
    if not _u32.IsWindowVisible(hwnd) or _u32.GetWindow(hwnd, _GW_OWNER):
        return False
    ex = _u32.GetWindowLongW(wintypes.HWND(hwnd), _GWL_EXSTYLE)
    if ex & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE):
        return False
    if not title(hwnd) or _class(hwnd) in _SKIP_CLASSES or _cloaked(hwnd):
        return False
    return True


def list_windows(include_own: bool = False) -> List[dict]:
    """Окна программ сверху вниз (как лежат на экране): hwnd, title, process, monitor, minimized, active."""
    if sys.platform != 'win32':
        return []
    from .capture import window_monitor
    fg = _u32.GetForegroundWindow() or 0
    hwnds: List[int] = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(lambda h, _l: hwnds.append(h) or True)
    _u32.EnumWindows(cb, 0)
    out = []
    for h in hwnds:
        try:
            if not _app_window(h):
                continue
            t = title(h)
            if not include_own and own(h, t):
                continue
            proc = process(h)
            if proc in _SKIP_PROCESSES:
                continue
            out.append({'hwnd': h, 'title': t, 'process': proc, 'monitor': window_monitor(h),
                        'minimized': bool(_u32.IsIconic(h)), 'active': h == fg})
        except OSError:
            continue
    return out


def foreground_title() -> str:
    return title(_u32.GetForegroundWindow() or 0)


def work_window() -> int:
    """Окно, с которым работает агент: активное, а если активен наш лаунчер — верхнее под ним."""
    if sys.platform != 'win32':
        return 0
    fg = _u32.GetForegroundWindow() or 0
    if fg and not own(fg) and _app_window(fg):
        return fg
    for w in list_windows():
        if not w['minimized']:
            return w['hwnd']
    return fg


def app_name(w: dict) -> str:
    """«Telegram» для telegram.exe — так модель и человек называют программу."""
    stem = Path(w['process']).stem
    known = {'chrome': 'Google Chrome', 'msedge': 'Microsoft Edge', 'code': 'VS Code', 'explorer': 'Проводник',
             'telegram': 'Telegram', 'discord': 'Discord', 'browser': 'Яндекс Браузер', 'firefox': 'Firefox'}
    return known.get(stem.lower(), stem[:1].upper() + stem[1:] if stem else '?')


def find(name: str) -> Optional[dict]:
    """Окно по названию программы («Telegram», «Telegram Desktop») или по части заголовка."""
    q = _fold(re.sub(r'\s*[—-]\s*\d+\s+запущенн\w*\s+окн\w*$', '', name or ''))   # подпись кнопки панели задач
    if not q:
        return None
    words = [w for w in q.split() if len(w) >= 3]
    best, best_score = None, 0
    for i, w in enumerate(list_windows()):
        stem = _fold(Path(w['process']).stem)
        t = _fold(w['title'])
        app = _fold(app_name(w))
        if q == stem or q == app:
            score = 100
        elif stem and stem in words:
            score = 90
        elif q in t:
            score = 70
        elif app and (app in q or q in app):
            score = 60
        elif words and all(x in t for x in words):
            score = 50
        else:
            continue
        score -= i * 0.1 + (5 if w['minimized'] else 0)        # выше по z-порядку и развёрнутые — лучше
        if score > best_score:
            best, best_score = w, score
    return best


def focus(hwnd: int, wait: float = 1.5) -> bool:
    """Вывести окно вперёд (развернуть, если свёрнуто). Без кликов по панели задач."""
    if sys.platform != 'win32' or not hwnd:
        return False
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)                               # SW_RESTORE
    # Windows не отдаёт фокус чужому процессу просто так: нажатие Alt снимает блокировку.
    _u32.keybd_event(0x12, 0, 0, 0)
    _u32.keybd_event(0x12, 0, 2, 0)
    _u32.BringWindowToTop(hwnd)
    _u32.SetForegroundWindow(hwnd)
    t_end = time.time() + wait
    while time.time() < t_end:
        if (_u32.GetForegroundWindow() or 0) == hwnd:
            return True
        time.sleep(0.05)
    return False


def describe(windows: List[dict], here: int = 0, limit: int = 12) -> str:
    """Список окон для промпта: программа «заголовок» · монитор · активно/свёрнуто."""
    rows = []
    for w in windows[:limit]:
        tags = [f'монитор {w["monitor"]}' + (' (этот)' if here and w['monitor'] == here else '')]
        if w['active']:
            tags.append('активно')
        if w['minimized']:
            tags.append('свёрнуто')
        rows.append(f'- {app_name(w)} «{w["title"][:70]}» · ' + ' · '.join(tags))
    if len(windows) > limit:
        rows.append(f'… ещё {len(windows) - limit}')
    return '\n'.join(rows) if rows else '(других окон нет)'
