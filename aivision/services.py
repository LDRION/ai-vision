"""
Фоновые службы AI Vision — поднимаются сами, когда нужны. Отдельно ничего запускать не нужно.

  • Screen Retina — ВСТРОЕННАЯ копия (aivision/vendor/retina) на своём порту 8766:
    живой индекс экрана для курсора. Включается ПО ТРЕБОВАНИЮ — когда агенту нужен экран
    (задача команды, сценарий, запись) — и гаснет сама через IDLE_STOP секунд без дела:
    не смотрит в экран и не пишет журнал 24/7. Процесс-ребёнок сидит в Job Object «убить при
    закрытии» — закрылся или упал лаунчер, и Retina гасится вместе с ним, без зомби.
    Внешний проект C:\\Projects\\screen-retina для этого не нужен. AV_RETINA_URL=... —
    работать с другой, своей Retina (например, dev-версией на 8765) и ничего не запускать.
  • Siberi Local — «мозг» (Google «Режим ИИ» через свой Edge без окон) на этом компьютере:
    aivision/vendor/siberi_local, API как у siberi.online. Нужен Node.js; зависимость
    (playwright-core) ставится сама при первом запуске. Поднимается вместе с лаунчером и
    гаснет вместе с ним (Job Object). SIBERI_BASE_URL в .env на другой адрес — работаем с ним.
  • Ollama — локальная модель выбора элемента по смыслу. Не запущена — запускаем
    `ollama serve` (служба общая для системы: живёт дальше, не гасим) и прогреваем модель.

Всё идемпотентно и не валит приложение: службы нет — AiVision работает по-старому.
    AV_RETINA=0 / AV_LOCAL_BRAIN=0 — не запускать соответствующую службу.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Optional

from .paths import BASE, STATE

RETINA_PORT = 8766
RETINA_DIR = STATE / 'retina'           # журнал событий (содержимое экрана) и лог — вне git
LOG_DAYS = 3                            # журналы событий старше — удаляем
RETRY_AFTER = 60.0                      # Retina не поднялась — столько не пробуем снова
IDLE_STOP = float(os.environ.get('AV_RETINA_IDLE', '120'))   # зрение без дела столько секунд — гасим

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

_lock = threading.Lock()
_child: Optional[subprocess.Popen] = None
_job = None
_failed_at = 0.0
_last_use = 0.0                         # когда зрение последний раз было нужно
_idle_thread: Optional[threading.Thread] = None
# retina: idle — спит до первой задачи; off — выключена совсем (AV_RETINA=0)
_status = {'retina': 'idle' if os.environ.get('AV_RETINA', '1') != '0' else 'off', 'ollama': 'off', 'model': '',
           'siberi': 'off', 'signed_in': None}


def status() -> dict:
    """Для интерфейса: retina ok|starting|down|off|external, ollama ok|starting|missing|down|off,
    model ready|missing|''."""
    return dict(_status)


def retina_url() -> str:
    return (os.environ.get('AV_RETINA_URL') or f'http://127.0.0.1:{RETINA_PORT}').rstrip('/')


def external() -> bool:
    return bool(os.environ.get('AV_RETINA_URL'))


def _get(url: str, timeout: float = 0.6) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _alive(url: str) -> bool:
    return _get(url + '/stats') is not None


# ── Screen Retina ───────────────────────────────────────────────────────────
def _kill_on_close_job():
    """Job Object, который убивает свои процессы, когда закрывается его последний дескриптор,
    то есть когда завершается наш процесс — даже аварийно."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', ctypes.c_uint64 * 6),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return job


def _bind_to_us(proc: subprocess.Popen) -> bool:
    global _job
    try:
        if _job is None:
            _job = _kill_on_close_job()
        if not _job:
            return False
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(k32.AssignProcessToJobObject(_job, int(proc._handle)))
    except (OSError, AttributeError):
        return False


def retina_cmd() -> list:
    args = ['watch', '--quiet', '--port', str(RETINA_PORT), '--stats', '3600']
    if getattr(sys, 'frozen', False):                       # собранный exe: он же умеет быть Retina
        return [sys.executable, '--retina', *args]
    py = Path(sys.executable)
    if py.name.lower() == 'pythonw.exe' and py.with_name('python.exe').exists():
        py = py.with_name('python.exe')                     # pythonw без stdout — Retina пишет в лог
    return [str(py), '-m', 'aivision.vendor.retina', *args]


def _clean_logs():
    cutoff = time.time() - LOG_DAYS * 86400
    for p in (RETINA_DIR / 'data').glob('events-*.jsonl'):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def _spawn() -> subprocess.Popen:
    RETINA_DIR.mkdir(parents=True, exist_ok=True)
    _clean_logs()
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if not getattr(sys, 'frozen', False):
        env['PYTHONPATH'] = os.pathsep.join([str(BASE)] + [x for x in [env.get('PYTHONPATH')] if x])
    log = open(RETINA_DIR / 'retina.log', 'w', encoding='utf-8')
    try:
        proc = subprocess.Popen(retina_cmd(), cwd=str(RETINA_DIR), env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    finally:
        log.close()
    _bind_to_us(proc)
    return proc


def _retina_ready():
    from . import retina
    retina._down_until = 0.0
    retina.register_own()                                   # лог лаунчера — не события экрана
    _status['retina'] = 'external' if external() else 'ok'


def touch():
    """Зрение нужно прямо сейчас (шаг агента, сценарий, запись) — не гасить его по простою."""
    global _last_use
    _last_use = time.time()


def wake(wait: float = 8.0) -> str:
    """Агенту нужен экран: зрение спит — разбудить (подождать до wait с), не спит — отметить работу."""
    touch()
    if _status['retina'] in ('ok', 'external') and (external() or (_child is not None and _child.poll() is None)):
        return _status['retina']
    return start_retina(wait=wait)


def _idle_check():
    """Своя Retina без дела дольше IDLE_STOP — гасим; понадобится — wake() поднимет снова."""
    if _child is not None and _child.poll() is None and time.time() - _last_use > IDLE_STOP:
        stop_retina(status='idle')


def _idle_watch(period: float = 5.0):
    while True:
        time.sleep(period)
        try:
            _idle_check()
        except Exception:
            pass


def start_retina(wait: float = 0.0) -> str:
    """Поднять встроенную Retina (если её ещё нет) и подождать до wait секунд, пока ответит."""
    global _child, _failed_at, _idle_thread
    touch()
    if os.environ.get('AV_RETINA', '1') == '0':
        _status['retina'] = 'off'
        return 'off'
    url = retina_url()
    if _alive(url):
        _retina_ready()
        return _status['retina']
    if external():                                          # чужую Retina не запускаем
        _status['retina'] = 'down'
        return 'down'
    with _lock:
        if _child is None or _child.poll() is not None:
            if _failed_at and time.time() - _failed_at < RETRY_AFTER:
                return 'down'
            try:
                _child = _spawn()
                if _idle_thread is None:
                    _idle_thread = threading.Thread(target=_idle_watch, daemon=True, name='aivision-retina-idle')
                    _idle_thread.start()
            except OSError:
                _failed_at = time.time()
                _status['retina'] = 'down'
                return 'down'
            _status['retina'] = 'starting'
    t_end = time.time() + wait
    while time.time() < t_end:
        if _alive(url):
            _retina_ready()
            return 'ok'
        if _child.poll() is not None:                       # упала на старте — см. retina.log
            _failed_at = time.time()
            _status['retina'] = 'down'
            return 'down'
        time.sleep(0.25)
    return _status['retina']


def stop_retina(status: str = 'off'):
    """Погасить свою Retina: по простою (status='idle' — проснётся по требованию) или насовсем.
    При выходе это не нужно: Job Object погасит её сам."""
    global _child
    with _lock:
        if _child is not None and _child.poll() is None:
            _child.terminate()
            try:
                _child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _child.kill()
        _child = None
    _status['retina'] = status


# ── Ollama ──────────────────────────────────────────────────────────────────
def _ollama_exe() -> Optional[str]:
    found = shutil.which('ollama')
    if found:
        return found
    p = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe'
    return str(p) if p.exists() else None


def _models(base: str) -> Optional[list]:
    d = _get(base + '/api/tags', timeout=1.5)
    return None if d is None else [m.get('name', '') for m in d.get('models', [])]


def start_ollama(wait: float = 45.0) -> str:   # холодный старт Ollama — до ~20 с
    from . import local_brain
    if os.environ.get('AV_LOCAL_BRAIN', '1') == '0':
        _status['ollama'] = 'off'
        return 'off'
    base = local_brain._url()
    names = _models(base)
    if names is None:
        exe = _ollama_exe()
        if not exe:
            _status['ollama'] = 'missing'
            return 'missing'
        try:
            # Скрытая консоль, а не DETACHED: без консоли вовсе каждый runner, который Ollama
            # запускает при загрузке модели, открывал своё чёрное окно (по три подряд).
            subprocess.Popen([exe, 'serve'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        except OSError:
            _status['ollama'] = 'down'
            return 'down'
        _status['ollama'] = 'starting'
        t_end = time.time() + wait
        while names is None and time.time() < t_end:
            time.sleep(0.5)
            names = _models(base)
        if names is None:
            _status['ollama'] = 'down'
            return 'down'
    _status['ollama'] = 'ok'
    want = local_brain.model()
    if want in names or f'{want}:latest' in names:
        local_brain._down_until = 0.0
        _status['model'] = 'ready'
        local_brain.warmup()                                # модель — в видеопамять заранее
    else:
        _status['model'] = 'missing'                        # нужна: ollama pull qwen2.5:7b
    return 'ok'


# ── Всё вместе ──────────────────────────────────────────────────────────────
# ── Siberi Local ────────────────────────────────────────────────────────────
SIBERI_PORT = int(os.environ.get('SIBERI_LOCAL_PORT', '8770'))
SIBERI_CDP_PORT = 9224
SIBERI_DIR = BASE / 'aivision' / 'vendor' / 'siberi_local'
SIBERI_PROFILE = STATE / 'siberi-edge'        # профиль Edge: вход в Google живёт здесь — вне git
SIBERI_LOG = STATE / 'siberi-local.log'
_siberi_child: Optional[subprocess.Popen] = None
_siberi_lock = threading.Lock()


def siberi_local() -> bool:
    """Мозг — Siberi Local на этом компьютере (а не удалённый сервер)."""
    from .siberi import BASE_URL
    return BASE_URL.startswith((f'http://127.0.0.1:{SIBERI_PORT}', f'http://localhost:{SIBERI_PORT}'))


def _siberi_ping(timeout: float = 1.5) -> Optional[dict]:
    key = os.environ.get('SIBERI_API_KEY', '')
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{SIBERI_PORT}/api/v1/ai/ping',
                                     headers={'Authorization': f'Bearer {key}'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _node() -> Optional[str]:
    return shutil.which('node')


def _siberi_deps() -> bool:
    """playwright-core в node_modules — ставим npm'ом при первом запуске (без браузеров Playwright)."""
    if (SIBERI_DIR / 'node_modules' / 'playwright-core').exists():
        return True
    npm = shutil.which('npm') or shutil.which('npm.cmd')
    if not npm:
        return False
    env = dict(os.environ, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1')
    r = subprocess.run([npm, 'install', '--omit=dev', '--no-audit', '--no-fund'], cwd=str(SIBERI_DIR), env=env,
                       capture_output=True, timeout=300, creationflags=CREATE_NO_WINDOW, shell=npm.endswith('.cmd'))
    return r.returncode == 0


def _siberi_env() -> dict:
    return dict(os.environ, SIBERI_LOCAL_KEY=os.environ.get('SIBERI_API_KEY', ''), SIBERI_LOCAL_PORT=str(SIBERI_PORT),
                SIBERI_CDP_PORT=str(SIBERI_CDP_PORT), SIBERI_PROFILE=str(SIBERI_PROFILE))


def start_siberi(wait: float = 60.0) -> str:
    """Поднять Siberi Local (если мозг — он и ещё не отвечает) и подождать до wait секунд."""
    global _siberi_child
    if not siberi_local():
        _status['siberi'] = 'remote'
        return 'remote'
    ping = _siberi_ping()
    if ping:
        _status.update(siberi='ok', signed_in=ping.get('signedIn'))
        return 'ok'
    with _siberi_lock:
        if _siberi_child is None or _siberi_child.poll() is not None:
            node = _node()
            if not node:
                _status['siberi'] = 'missing'
                return 'missing'
            _status['siberi'] = 'starting'
            if not _siberi_deps():
                _status['siberi'] = 'down'
                return 'down'
            STATE.mkdir(parents=True, exist_ok=True)
            log = open(SIBERI_LOG, 'w', encoding='utf-8')
            try:
                _siberi_child = subprocess.Popen([node, 'server.js'], cwd=str(SIBERI_DIR), env=_siberi_env(),
                                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                                 creationflags=CREATE_NO_WINDOW)
            finally:
                log.close()
            _bind_to_us(_siberi_child)                  # гаснет вместе с лаунчером — и его Edge тоже
    t_end = time.time() + wait
    while time.time() < t_end:
        ping = _siberi_ping()
        if ping:
            _status.update(siberi='ok', signed_in=ping.get('signedIn'))
            return 'ok'
        if _siberi_child is not None and _siberi_child.poll() is not None:
            _status['siberi'] = 'down'                  # упал на старте — см. company/state/siberi-local.log
            return 'down'
        time.sleep(0.4)
    return _status['siberi']


def stop_siberi():
    global _siberi_child
    with _siberi_lock:
        if _siberi_child is not None and _siberi_child.poll() is None:
            _siberi_child.terminate()
            try:
                _siberi_child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _siberi_child.kill()
        _siberi_child = None
    _status['siberi'] = 'off'


def siberi_login() -> str:
    """Окно Edge с профилем Siberi Local — войти в Google (картинки «Режим ИИ» понимает только
    после входа). Профиль занят фоновым Edge — гасим Siberi Local на время входа."""
    node = _node()
    if not node:
        return 'missing'
    stop_siberi()
    subprocess.run([node, 'server.js', '--login'], cwd=str(SIBERI_DIR), env=_siberi_env(),
                   creationflags=CREATE_NO_WINDOW)
    return start_siberi(wait=60)


_bg_started = False


def start_background(on_ready: Optional[Callable[[dict], None]] = None):
    """Поднять службы фоном, не задерживая окно: локальную модель. Зрение (Retina) — не здесь:
    оно спит до первой задачи (wake). on_ready(status) — когда всё выяснилось."""
    global _bg_started
    if _bg_started:
        if on_ready:
            on_ready(status())
        return
    _bg_started = True

    def report():
        if on_ready:
            try:
                on_ready(status())
            except Exception:
                pass

    def work():
        report()                                    # зрение спит до задачи — сообщаем сразу
        t = threading.Thread(target=start_ollama, daemon=True)
        t.start()
        start_siberi()                              # мозг — сразу: без него ни одного решения
        report()
        t.join(timeout=60.0)
        report()                                    # и ещё раз, когда выяснилось с моделью
    threading.Thread(target=work, daemon=True, name='aivision-services').start()


def ensure(wait: float = 8.0) -> dict:
    """Перед работой на экране: зрение просыпается (ждём до wait с), Ollama — фоном."""
    start_background()
    wake(wait=wait)
    return status()


def describe(st: Optional[dict] = None) -> str:
    """Одна строка для статуса в интерфейсе."""
    st = st or status()
    eye = {'ok': '👁 Retina работает', 'external': '👁 Retina (внешняя)', 'starting': '👁 Retina запускается…',
           'down': '👁 Retina не запустилась (см. company/state/retina/retina.log)', 'off': '👁 Retina выключена',
           'idle': '👁 зрение спит — включится, когда понадобится экран'}
    brain = {'ok': '🧠 Ollama готова', 'starting': '🧠 Ollama запускается…', 'missing': '🧠 Ollama не установлена',
             'down': '🧠 Ollama не отвечает', 'off': '🧠 локальная модель выключена'}
    s = f"{eye.get(st['retina'], st['retina'])} · {brain.get(st['ollama'], st['ollama'])}"
    head = {'ok': '🧩 Siberi Local работает' + ('' if st.get('signed_in') is not False
                                                 else ' (войдите в Google — иначе не видит картинки)'),
            'starting': '🧩 Siberi Local запускается…', 'missing': '🧩 Siberi Local: нужен Node.js',
            'down': '🧩 Siberi Local не запустился (см. company/state/siberi-local.log)',
            'off': '🧩 Siberi Local выключен', 'remote': '🧩 Siberi — удалённый сервер'}
    if st.get('siberi'):
        s += f" · {head.get(st['siberi'], st['siberi'])}"
    if st.get('model') == 'missing':
        from . import local_brain
        s += f' (нет модели: ollama pull {local_brain.model()})'
    return s
