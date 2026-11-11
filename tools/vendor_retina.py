"""
Встроить (обновить) копию Screen Retina в AiVision.

AiVision работает со СВОЕЙ копией Retina из aivision/vendor/retina: внешний проект
можно править, уводить в другую сторону или удалить — здесь ничего не сломается.
Обновлять копию нужно осознанно, этим скриптом, и после него прогнать тесты:

    python tools/vendor_retina.py                      # из C:\\Projects\\screen-retina
    python tools/vendor_retina.py D:\\где-то\\screen-retina
    python -m pytest tests/retina -q && python -m tests.test_offline

Копируется пакет retina/ целиком (без __pycache__) и его тесты (импорты
переписываются на aivision.vendor.retina). В VENDORED.md пишется, откуда и с
какого коммита взято.
"""

import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / 'aivision' / 'vendor' / 'retina'
TESTS = ROOT / 'tests' / 'retina'
DEFAULT_SRC = Path(r'C:\Projects\screen-retina')
SKIP = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')


def git(src: Path, *args) -> str:
    try:
        return subprocess.run(['git', '-C', str(src), *args], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def rewrite_imports(text: str) -> str:
    return re.sub(r'\b(from|import) retina\b', r'\1 aivision.vendor.retina', text)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    pkg = src / 'retina'
    if not (pkg / 'perception.py').exists():
        sys.exit(f'не нашёл пакет Retina в {pkg}')
    commit = git(src, 'rev-parse', '--short', 'HEAD') or 'неизвестен'
    dirty = bool(git(src, 'status', '--porcelain', '--', 'retina', 'tests'))

    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(pkg, DEST, ignore=SKIP)
    (DEST.parent / '__init__.py').touch()

    if TESTS.exists():
        shutil.rmtree(TESTS)
    TESTS.mkdir(parents=True)
    (TESTS / '__init__.py').touch()
    for t in sorted((src / 'tests').glob('test_*.py')):
        (TESTS / t.name).write_text(rewrite_imports(t.read_text(encoding='utf-8')), encoding='utf-8')

    (DEST / 'VENDORED.md').write_text(f"""# Встроенная копия Screen Retina

Источник: `{src}` (репозиторий github.com/LDRION/screen-retina), коммит `{commit}`{' + незакоммиченные правки' if dirty else ''}.
Скопировано: {date.today().isoformat()} скриптом `tools/vendor_retina.py`.

Это **замороженная копия**: AiVision запускает именно её (`python -m aivision.vendor.retina watch`,
порт 8766, см. `aivision/services.py`) и не зависит от внешнего проекта. Внешнюю Retina можно
менять или удалить — здесь ничего не сломается.

Код здесь не правим руками: правки — в самой Retina, потом обновление копии:

    python tools/vendor_retina.py
    python -m pytest tests/retina -q && python -m tests.test_offline

Что AiVision использует из её API (при обновлении это должно остаться совместимым):
`GET /stats`, `GET /elements?area&exclude&refresh`, `GET /find?q&area&limit&exclude`,
`GET /settled?area&quiet&timeout&expect`, `GET /events?since`, `POST /exclude {{"prefixes"}}`.
""", encoding='utf-8')
    n = sum(1 for _ in DEST.rglob('*.py'))
    print(f'Retina {commit}{" (+ правки)" if dirty else ""} → {DEST.relative_to(ROOT)}: {n} модулей, '
          f'тесты → {TESTS.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
