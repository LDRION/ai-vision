"""
Сквозная живая проверка — тот же путь, что у кнопки «Запустить» в лаунчере:
браузер (инкогнито, на весь экран) → запись экрана → агент по шагам → артефакты
(шаги, action.json, record.mp4 + метки) → закрыть только своё окно браузера.

Двигает настоящую мышь и отправляет снимки экрана в Siberi — запускать, когда
компьютер свободен. Пошевелить мышью = остановка.

    python tests/live_e2e.py ["цель"] [--url URL] [--browser "Microsoft Edge"] [--steps N] [--monitor N]
"""

import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aivision.capture import (dpi_aware, foreground_monitor, monitors,  # noqa: E402
                              window_monitor)

dpi_aware()

from aivision import browsers                  # noqa: E402
from aivision.detect import foreground_title  # noqa: E402
from aivision.loop import run_task             # noqa: E402
from aivision.memory import Memory             # noqa: E402
from aivision.recorder import ScreenRecorder   # noqa: E402
from aivision.screen import RealEnv            # noqa: E402

DEFAULT_GOAL = 'Найди через поиск Яндекса погоду в Москве и скажи, сколько сейчас градусов'


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def main():
    goal = next((a for a in sys.argv[1:] if not a.startswith('--')
                 and sys.argv[sys.argv.index(a) - 1] not in ('--url', '--browser', '--steps', '--monitor')), DEFAULT_GOAL)
    url = _arg('--url', 'https://ya.ru')
    br = browsers.find(_arg('--browser', '')) or browsers.default_browser()
    steps = int(_arg('--steps', 8))

    # Агенту — монитор, где НЕТ окна, из которого запустили (терминал/IDE): там смотрит человек
    mons = monitors()
    here = foreground_monitor()
    mon = int(_arg('--monitor', 0)) or next((i for i in range(1, len(mons)) if i != here), here)
    print(f'🎯 {goal}\n🌐 {br.name} → {url} (инкогнито) · монитор агента {mon} из {len(mons) - 1}')
    hwnd = browsers.open_on_monitor(br, url, mons[mon], incognito=True)
    time.sleep(3.0)                                    # страница грузится
    print(f'🪟 окно: «{foreground_title()}» · на мониторе {window_monitor(hwnd)}')

    run_dir = ROOT / 'runs' / f'e2e-{datetime.now():%Y%m%d-%H%M%S}'
    run_dir.mkdir(parents=True)
    memory = Memory(Path(tempfile.mkdtemp()) / 'lessons.json')    # опыт теста — отдельно от боевого
    rec = ScreenRecorder(run_dir, mon).start()
    t0 = time.time()
    try:
        r = run_task(goal, RealEnv(mon), max_steps=steps, run_dir=run_dir, memory=memory,
                     on_step=rec.mark_step)
    finally:
        rec.stop()
        browsers.close_window(hwnd)                    # закрываем только своё окно

    meta = json.loads((run_dir / 'record.json').read_text(encoding='utf-8'))
    step_dirs = sorted(run_dir.glob('step_*'))
    actions = [d for d in step_dirs if (d / 'action.json').exists()]
    print('\n══ ИТОГ')
    print(f"  {r['status']} за {r['steps']} шаг(а), {time.time() - t0:.0f} с: {r['answer']}")
    print(f'  шагов на диске: {len(step_dirs)}, с action.json: {len(actions)}')
    print(f"  запись: {meta['duration']} с, {meta['frames']} кадров, меток шагов: {len(meta['steps'])}, "
          f"{(run_dir / 'record.mp4').stat().st_size // 1024} КБ")
    for les in r.get('lessons', []):
        print(f"  📚 [{les['app'] or 'общий'}] {les['text']}")
    print(f'  📁 {run_dir}')
    sys.exit(0 if r['status'] == 'done' else 1)


if __name__ == '__main__':
    main()
