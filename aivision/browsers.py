"""
Браузеры, установленные на этой машине: список для выбора в лаунчере,
браузер по умолчанию (системный) и запуск выбранного на нужном URL.

Источник — реестр Windows (ключи StartMenuInternet в HKLM и HKCU), плюс
проверка известных путей для браузеров, которые там не прописались
(portable-сборки, свежие установки). Системный браузер по умолчанию берём
из UserChoice текущего пользователя.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

# ProgId из UserChoice → понятное имя браузера. ProgId у Chrome/Edge содержит
# суффикс-хэш профиля, поэтому сверяемся по началу строки.
PROGID_HINTS = [
    ('ChromeHTML', 'Google Chrome'),
    ('MSEdgeHTM', 'Microsoft Edge'),
    ('FirefoxURL', 'Mozilla Firefox'),
    ('YandexHTML', 'Yandex Browser'),
    ('BraveHTML', 'Brave'),
    ('OperaStable', 'Opera'),
    ('IE.HTTP', 'Internet Explorer'),
]

# Запасной список: имя → возможные пути exe (если браузера нет в реестре).
KNOWN = {
    'Google Chrome': [r'%ProgramFiles%\Google\Chrome\Application\chrome.exe',
                      r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
                      r'%LocalAppData%\Google\Chrome\Application\chrome.exe'],
    'Microsoft Edge': [r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe',
                       r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'],
    'Mozilla Firefox': [r'%ProgramFiles%\Mozilla Firefox\firefox.exe',
                        r'%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe'],
    'Yandex Browser': [r'%LocalAppData%\Yandex\YandexBrowser\Application\browser.exe'],
    'Brave': [r'%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe',
              r'%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe'],
    'Opera': [r'%LocalAppData%\Programs\Opera\opera.exe'],
}

# В браузерах на движке Chromium новое окно открывается флагом --new-window.
CHROMIUM = {'Google Chrome', 'Microsoft Edge', 'Yandex Browser', 'Brave', 'Opera'}


@dataclass
class Browser:
    name: str            # «Google Chrome»
    path: str            # полный путь к exe
    is_default: bool = False

    @property
    def key(self) -> str:
        return os.path.basename(self.path).lower()


def _expand(path: str) -> Optional[str]:
    full = os.path.expandvars(path)
    return full if '%' not in full and os.path.isfile(full) else None


def _default_progid() -> str:
    if sys.platform != 'win32':
        return ''
    try:
        import winreg
        key = (r'Software\Microsoft\Windows\Shell\Associations'
               r'\UrlAssociations\https\UserChoice')
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, 'ProgId')[0]
    except OSError:
        return ''


def _from_registry() -> List[Browser]:
    if sys.platform != 'win32':
        return []
    import winreg
    found = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, 0)]
    for hive, flag in roots:
        try:
            root = winreg.OpenKey(hive, r'SOFTWARE\Clients\StartMenuInternet', 0,
                                  winreg.KEY_READ | flag)
        except OSError:
            continue                        # в этом кусте ключа нет — пробуем следующий
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, sub + r'\shell\open\command') as c:
                        cmd = winreg.QueryValueEx(c, '')[0].strip('"')
                    with winreg.OpenKey(root, sub) as c:
                        name = winreg.QueryValueEx(c, '')[0]
                except OSError:
                    continue
                if os.path.isfile(cmd):
                    found.append(Browser(name, cmd))
    return found


def list_browsers() -> List[Browser]:
    """Установленные браузеры без дублей; системный по умолчанию — первым."""
    found, seen = [], set()
    for b in _from_registry():                      # один exe может быть в 32- и 64-битном кусте
        if b.path.lower() not in seen:
            seen.add(b.path.lower())
            found.append(b)
    have = {b.key for b in found}
    for name, paths in KNOWN.items():
        for p in paths:
            full = _expand(p)
            if full and os.path.basename(full).lower() not in have:
                found.append(Browser(name, full))
                have.add(os.path.basename(full).lower())
                break

    # Internet Explorer как агент бесполезен — убираем из выбора.
    found = [b for b in found if 'iexplore' not in b.key]

    progid = _default_progid()
    default_name = next((n for pref, n in PROGID_HINTS if progid.startswith(pref)), '')
    for b in found:
        b.is_default = b.name == default_name
    found.sort(key=lambda b: (not b.is_default, b.name.lower()))
    return found


def default_browser(browsers: Optional[List[Browser]] = None) -> Optional[Browser]:
    browsers = browsers or list_browsers()
    return next((b for b in browsers if b.is_default), browsers[0] if browsers else None)


def find(name_or_key: str) -> Optional[Browser]:
    """Браузер по имени или по имени exe (chrome.exe). None → ничего не нашли."""
    if not name_or_key:
        return None
    needle = name_or_key.lower()
    for b in list_browsers():
        if needle in (b.name.lower(), b.key):
            return b
    return None


def launch(browser: Browser, url: str = '', incognito: bool = False) -> subprocess.Popen:
    """Открывает НОВОЕ окно браузера (новая сессия — опрятный старт для сценария)."""
    args = [browser.path]
    if browser.name in CHROMIUM:
        # на весь экран: иначе по краям видны лаунчер и другие окна, и OCR читает чужой текст
        args += ['--new-window', '--start-maximized']
        if incognito:
            args.append('--incognito' if browser.name != 'Microsoft Edge' else '--inprivate')
    elif browser.name == 'Mozilla Firefox':
        args.append('-new-window')
        if incognito:
            args.append('-private-window')
    if url:
        args.append(url)
    return subprocess.Popen(args, close_fds=True)


BROWSER_CLASSES = ('Chrome_WidgetWin_1', 'MozillaWindowClass')


def browser_windows() -> dict:
    """Видимые окна на движках Chromium/Firefox: hwnd → заголовок."""
    if sys.platform != 'win32':
        return {}
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in BROWSER_CLASSES:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out[hwnd] = buf.value
        return True

    user32.EnumWindows(cb, 0)
    return out


def place_on_monitor(hwnd: int, mon: dict):
    """
    Окно → развёрнутым на нужном мониторе. Флаг --start-maximized Chrome игнорирует,
    если уже запущен (берёт прошлое положение окна) — поэтому ставим через WinAPI:
    восстановить, перенести внутрь монитора, развернуть (разворот идёт на том
    мониторе, где окно).
    """
    import ctypes
    user32 = ctypes.windll.user32
    SW_RESTORE, SW_MAXIMIZE = 9, 3
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.MoveWindow(hwnd, mon['left'] + 40, mon['top'] + 40,
                      max(400, mon['width'] - 80), max(300, mon['height'] - 80), True)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.SetForegroundWindow(hwnd)


def open_on_monitor(browser: Browser, url: str, mon: dict, incognito: bool = False,
                    timeout: float = 8.0) -> Optional[int]:
    """Открыть новое окно браузера и поставить его развёрнутым на монитор. → hwnd или None."""
    import time
    before = set(browser_windows())
    launch(browser, url, incognito)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        new = [h for h in browser_windows() if h not in before]
        if new:
            time.sleep(0.4)                     # дать окну дорисоваться перед переносом
            place_on_monitor(new[0], mon)
            return new[0]
    return None


def close_window(hwnd: int):
    if sys.platform == 'win32' and hwnd:
        import ctypes
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE


if __name__ == '__main__':
    for b in list_browsers():
        print(f"{'★' if b.is_default else ' '} {b.name:<20} {b.path}")
