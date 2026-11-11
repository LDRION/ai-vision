"""
Видео команды — СБОРКА ролика из материала (проект dumaem «edit»): не генерация
с нуля, а умный монтаж. dumaem анализирует биты музыки, лица, движение и моменты,
режет исходник в такт и рендерит 1080×1920 mp4 с вшитыми титрами (ffmpeg).

Инструмент агента-монтажёра. Чем больше материала в dumaem/media — тем лучше
монтаж. Сюжет/озвучку планирует отдельный шаг оркестрации (копирайтер → видеограф),
а человек может подправить результат в output dumaem.

Путь к dumaem — из env DUMAEM_DIR или соседний C:\\Projects\\dumaem. Материал —
из env VIDEO_MEDIA или dumaem/media.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from .paths import BASE

DUMAEM_DIR = Path(os.environ.get('DUMAEM_DIR', BASE.parent / 'dumaem'))


def enabled() -> bool:
    return (DUMAEM_DIR / 'package.json').exists() and shutil.which('npm') is not None \
        and (DUMAEM_DIR / 'node_modules').exists() and shutil.which('ffmpeg') is not None


def media_dir() -> Path:
    return Path(os.environ.get('VIDEO_MEDIA', DUMAEM_DIR / 'media'))


def have_material() -> bool:
    d = media_dir()
    return d.exists() and any(p.suffix.lower() in ('.mp4', '.mov', '.mkv', '.mp3', '.wav', '.m4a')
                              for p in d.iterdir())


def _newest_output(out_dir: Path, after: float) -> Optional[Path]:
    if not out_dir.exists():
        return None
    fresh = [p for p in out_dir.iterdir() if p.stat().st_mtime >= after - 1]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


def edit(material: Optional[List[str]] = None, duration: Optional[int] = None,
         timeout: float = 1200.0) -> Path:
    """
    Смонтировать ролик из материала → путь к папке результата (output/edit-…).
    material — явные файлы [музыка, исходник]; без них dumaem берёт свежие из media/.
    duration — длина ролика в секундах (напр. 30 для шортса).
    """
    if not enabled():
        raise RuntimeError(f'dumaem/ffmpeg не готовы (dir {DUMAEM_DIR})')
    if not material and not have_material():
        raise RuntimeError(f'нет материала в {media_dir()} — положи видео/музыку туда')
    out_dir = DUMAEM_DIR / 'output'
    started = time.time()
    extra = list(material or [])
    if duration:
        extra.append(str(int(duration)))
    cmd = ['npm', 'run', 'edit', '--'] + extra
    proc = subprocess.run(cmd, cwd=str(DUMAEM_DIR), capture_output=True, text=True,
                          timeout=timeout, shell=(os.name == 'nt'),
                          creationflags=0x08000000 if os.name == 'nt' else 0)   # без окна консоли
    if proc.returncode != 0:
        raise RuntimeError(f'dumaem edit: {(proc.stderr or proc.stdout)[-300:]}')
    result = _newest_output(out_dir, started)
    if result is None:
        raise RuntimeError('dumaem смонтировал, но результата в output/ не видно')
    return result
