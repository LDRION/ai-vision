"""
Один лаунчер на компьютер. Второй запуск (двойной клик по AiVision.vbs, exe) не
открывает новое окно, а просит уже открытое показаться — даже из трея — и выходит.

Занятость — именованный мьютекс Windows: его держит процесс лаунчера, и Windows сама
освобождает его, когда процесс завершается (в том числе аварийно). Просьба «покажись» —
одно слово по локальному сокету 127.0.0.1:PORT, живой лаунчер сразу отвечает «ok».
Зависший не отвечает (соединение за него принимает ядро) — тогда предлагаем закрыть его
и открыть заново, а не выходим молча: иначе AI Vision «не открывается в принципе».

    if not single.acquire():
        if single.ask_to_show():
            return
        pid = single.holder_pid()                  # завис — закрыть и занять его место
        if pid and single.ask_user('…закрыть и открыть заново?') and single.take_over(pid):
            ...                                    # дальше обычный запуск
    single.serve(on_show)          # в первом экземпляре, когда окно создано
"""

import ctypes
import socket
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable

NAME = 'Local\\AIVisionLauncher'
PORT = 8767
ERROR_ALREADY_EXISTS = 183

_mutex = None


def acquire(name: str = NAME) -> bool:
    """True — мы первые (и держим мьютекс до выхода); False — лаунчер уже запущен."""
    global _mutex
    if sys.platform != 'win32':
        return True
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    h = k32.CreateMutexW(None, False, name)
    if not h:
        return True                                   # не смогли проверить — не мешаем запуску
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(h)
        return False
    _mutex = h
    return True


def ask_to_show(port: int = PORT, wait: float = 6.0, reply: float = 4.0) -> bool:
    """Попросить открытый лаунчер показаться. Он может ещё стартовать — подключаемся до wait
    секунд. True — ответил «ok»; False — некому ответить или он завис (подключились, а ответа нет)."""
    t_end = time.time() + wait
    while True:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1.0) as s:
                s.sendall(b'show')
                s.settimeout(reply)
                return s.recv(16).strip() == b'ok'
        except TimeoutError:
            return False
        except OSError:
            if time.time() >= t_end:
                return False
            time.sleep(0.3)


def holder_pid(port: int = PORT):
    """Процесс, который слушает порт лаунчера (он и держит мьютекс). None — не нашли."""
    try:
        import psutil
        for c in psutil.net_connections(kind='tcp'):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                return c.pid
    except Exception:
        pass
    return None


def take_over(pid: int, name: str = NAME, wait: float = 6.0) -> bool:
    """Закрыть зависший лаунчер и занять его место (мьютекс освободится с его смертью)."""
    try:
        import psutil
        psutil.Process(pid).kill()
    except Exception:
        return False
    t_end = time.time() + wait
    while time.time() < t_end:
        if acquire(name):
            return True
        time.sleep(0.2)
    return False


def ask_user(text: str) -> bool:
    """Да/нет окном Windows: под pythonw консоли нет."""
    try:
        return ctypes.windll.user32.MessageBoxW(None, text, 'AI Vision', 0x04 | 0x30 | 0x40000) == 6
    except (OSError, AttributeError):
        return False


def tell(text: str):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, 'AI Vision', 0x30 | 0x40000)
    except (OSError, AttributeError):
        pass


def serve(on_show: Callable[[], None], port: int = PORT) -> bool:
    """Слушать просьбы «покажись» от повторных запусков. False — порт занят чужой программой."""
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(('127.0.0.1', port))
        srv.listen(4)
    except OSError:
        return False

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(1.0)
                    if conn.recv(16).strip() == b'show':
                        conn.sendall(b'ok')                # живы — отвечаем сразу, окно покажем следом
                        on_show()
                except OSError:
                    pass
    threading.Thread(target=loop, daemon=True, name='aivision-single').start()
    return True
