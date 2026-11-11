"""
Dev-лаунчер: запускает AI Vision из ЖИВОГО кода и сам перезапускает его, как
только сохраняешь любой .py в aivision/. Удобно, пока правишь код — не нужно
каждый раз пересобирать exe: сохранил → окно поднялось заново с новыми правками.

  python dev_launcher.py        — запуск с автоперезапуском
  AiVision.vbs                  — то же двойным кликом, без чёрного окна консоли

Отличие от dist/AiVision/AiVision.exe: тот — замороженный снимок кода (быстрый,
переносимый, но обновляется только пересборкой). Этот всегда берёт текущий код.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

POLL = 1.0            # как часто проверять изменения файлов, с
DEBOUNCE = 0.8        # подождать после последнего сохранения, с (не дёргаться на каждую букву)
APP_CMD = ['-m', 'aivision']   # что запускаем (в тестах подменяется заглушкой)
# Без этих библиотек окно не поднимется. Python бывает несколько (3.13 с библиотеками и
# свежий 3.14 без них) — первый в PATH не обязательно годится, поэтому проверяем импортом.
REQUIRED = ('mss', 'webview', 'customtkinter', 'pynput', 'winocr')
QUICK_CRASH = 8.0     # окно упало раньше — показываем причину, а не молчим
NO_WINDOW = 0x08000000


def project_root() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) \
        else Path(__file__).resolve().parent
    # на случай, если лаунчер положили глубже — поднимаемся до папки с aivision/
    for p in [base, *base.parents]:
        if (p / 'aivision' / 'launcher.py').exists():
            return p
    return base


def candidates(root: Path) -> list:
    """Все Python на машине, по порядку предпочтения: venv проекта, тот, что запустил нас,
    всё из PATH, стандартные папки установки (свежие версии первыми)."""
    import shutil
    found = [root / '.venv' / 'Scripts' / 'python.exe']
    if not getattr(sys, 'frozen', False):
        found.append(Path(sys.executable))
    try:
        out = subprocess.run(['where', 'python'], capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout
        found += [Path(x.strip()) for x in out.splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError):
        w = shutil.which('python')
        if w:
            found.append(Path(w))
    base = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Python'
    found += sorted(base.glob('Python3*/python.exe'), reverse=True)
    out, seen = [], set()
    for p in found:
        if p.name.lower() == 'pythonw.exe':
            p = p.with_name('python.exe')
        key = str(p).lower()
        if key not in seen and p.exists():
            seen.add(key)
            out.append(p)
    return out


def has_deps(py: Path) -> bool:
    try:
        r = subprocess.run([str(py), '-c', 'import ' + ', '.join(REQUIRED)], capture_output=True,
                           timeout=60, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_python(root: Path = None, check=has_deps) -> list:
    """Интерпретатор для приложения: первый Python, где библиотеки AI Vision импортируются.
    pythonw той же установки — без окна консоли. [] — подходящего нет."""
    for py in candidates(root or project_root()):
        if check(py):
            w = py.with_name('pythonw.exe')
            return [str(w if w.exists() else py)]
    return []


def note(root: Path, text: str):
    """Журнал самого запускателя (company/state/dev_launcher.log): под pythonw/vbs консоли нет,
    и без него не узнать, на каком шаге встал запуск."""
    try:
        p = root / 'company' / 'state' / 'dev_launcher.log'
        p.parent.mkdir(parents=True, exist_ok=True)
        old = p.read_text(encoding='utf-8', errors='replace').splitlines()[-300:] if p.exists() else []
        p.write_text('\n'.join(old + [f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"]) + '\n', encoding='utf-8')
    except OSError:
        pass


def alert(text: str):
    """Окно с сообщением: под pythonw/vbs консоли нет, иначе ошибку никто не увидит."""
    print(text)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, 'AI Vision', 0x10)
    except (OSError, AttributeError):
        pass


WATCH = ('*.py', 'webui/*.html', 'webui/*.js', 'webui/*.css')   # код и интерфейс окна


def snapshot(root: Path) -> dict:
    files = {}
    for pattern in WATCH:
        for p in (root / 'aivision').rglob(pattern) if pattern == '*.py' else (root / 'aivision').glob(pattern):
            try:
                files[p] = p.stat().st_mtime
            except OSError:
                pass
    return files


def main():
    root = project_root()
    note(root, f'старт: pid {os.getpid()}, {sys.executable}')
    t0 = time.time()
    py = find_python(root)
    note(root, f'интерпретатор: {py[0] if py else "не найден"} ({time.time() - t0:.1f} с)')
    if not py:
        alert('Не нашёл Python с библиотеками AI Vision.\n\n'
              'Установите их в нужный Python (обычно 3.13):\n'
              '  py -3.13 -m pip install -r requirements.txt\n\n'
              'Проверено: ' + ', '.join(str(p) for p in candidates(root)))
        return
    log_path = root / 'company' / 'state' / 'launcher.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f'AI Vision dev · {root}\n  интерпретатор: {py}\n  слежу за aivision/*.py — сохрани файл, и окно перезапустится.\n')

    while True:
        state = snapshot(root)
        log = open(log_path, 'w', encoding='utf-8')
        t_start = time.time()
        child = subprocess.Popen(py + APP_CMD, cwd=str(root), stdout=log, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        note(root, f'окно запущено: pid {child.pid}')
        changed = False
        while True:
            time.sleep(POLL)
            if child.poll() is not None:        # окно закрыли — выходим, если код не менялся
                note(root, f'окно закрылось: код {child.returncode}, проработало {time.time() - t_start:.0f} с')
                if child.returncode and time.time() - t_start < QUICK_CRASH:
                    log.close()
                    tail = log_path.read_text(encoding='utf-8', errors='replace').strip().splitlines()[-12:]
                    alert('AI Vision не запустился.\n\n' + '\n'.join(tail) + f'\n\nПолный лог: {log_path}')
                    return
                break
            now = snapshot(root)
            if now != state:
                # дождёмся, пока сохранения утихнут (несколько файлов подряд)
                last = now
                while True:
                    time.sleep(DEBOUNCE)
                    cur = snapshot(root)
                    if cur == last:
                        break
                    last = cur
                # Агент сейчас работает на экране — не обрываем задачу, перезапустимся после неё.
                busy = root / 'company' / 'state' / 'launcher.busy'
                if busy.exists() and child.poll() is None:
                    print('  ⏸ код изменился, но идёт задача — перезапущу, когда закончится')
                    while busy.exists() and child.poll() is None:
                        time.sleep(POLL)
                    if child.poll() is not None:
                        break
                print('  ↻ код изменился — перезапускаю…')
                note(root, 'код изменился — перезапуск')
                changed = True
                break
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        log.close()
        if not changed:                         # обычное закрытие окна
            print('  окно закрыто — выходим.')
            return


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        note(project_root(), 'ошибка запуска:\n' + traceback.format_exc())
        alert('AI Vision не запустился — ошибка запускателя:\n\n' + traceback.format_exc()[-1500:])
