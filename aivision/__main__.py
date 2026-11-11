"""
AI Vision — Siberi смотрит на экран и ведёт курсор как человек.

  python -m aivision                                              открыть лаунчер (GUI)
  python -m aivision --classic                                    старый лаунчер (Tk)
  python -m aivision "зайди в почту и открой последнее письмо"   агент по шагам до цели
  python -m aivision "нажми Войти" --once                         одно действие
  python -m aivision "нажми Войти" --dry-run                      план одного действия, курсор не трогаем
  python -m aivision "нажми Найти" --image shot.png               план по готовому скриншоту
  python -m aivision "перенеси карточку в Готово" --hint "зажми карточку и тащи правее"

Опции:
  --hint TEXT    подсказка человеческим языком, как это делается (можно несколько --hint)
  --no-ask       не спрашивать подсказку, если агент застрял (по умолчанию спрашивает)
  --no-learn     не читать и не пополнять опыт (experience/lessons.json)
  --steps N      лимит шагов агента (15)
  --delay N      секунд до первого снимка, чтобы переключиться в нужное окно (3)
  --monitor N    номер монитора mss (1 — основной)
  --image PATH   скриншот из файла; действие — только с --act
  --act          выполнить действие и для --image (координаты = экран)
  --no-refine    без шага уточнения zoom
  --speed K      скорость движения (1.0)

Остановить: сдвинуть мышь рукой. Всё, что видела и ответила Siberi, —
в runs/<время>/ (по папке на шаг): screen.png, marked.png, zoom.png, ответы,
action.rec (открывается в MacroRec), result.json.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .capture import dpi_aware, grab

ROOT = Path(__file__).resolve().parent.parent

STATUS = {
    'done': '✅ Готово', 'fail': '🛑 Не получается', 'stuck': '🧱 Застряла',
    'limit': '⏱ Кончились шаги', 'aborted': '⏹ Остановлено', 'blind': '🙈 Siberi не видит экран',
}


def _arg(args, name, default=None, cast=str):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return cast(args[i + 1])
    return default


def _plan_image(img, offset, events, target):
    out = img.convert('RGB').copy()
    d = ImageDraw.Draw(out)
    pts = [(e['x'] - offset[0], e['y'] - offset[1]) for e in events if e['e'] == 'move']
    if len(pts) > 1:
        d.line(pts, fill='#00c853', width=3)
    for e in events:
        if e['e'] == 'click' and e['down']:
            x, y = e['x'] - offset[0], e['y'] - offset[1]
            d.ellipse([x - 9, y - 9, x + 9, y + 9], outline='red', width=3)
    if target.get('box'):
        x1, y1, x2, y2 = target['box']
        d.rectangle([x1 - 3, y1 - 3, x2 + 3, y2 + 3], outline='red', width=2)
    return out


def _countdown(delay: float):
    for c in range(int(delay), 0, -1):
        print(f'\r  📸 Старт через {c}… переключись в нужное окно', end='', flush=True)
        time.sleep(1)
    print('\r' + ' ' * 60 + '\r', end='')


def _all(args, name):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]


def _ask_hint(delay):
    """Агент застрял — спрашиваем в терминале, как это делается, и даём вернуться в окно."""
    def ask(status, reason):
        print(f'\n  🤔 {STATUS[status]}: {reason}')
        try:
            hint = input('  Подскажи, как это сделать (Enter — закончить): ').strip()
        except EOFError:
            return None
        if hint:
            _countdown(delay)
        return hint or None
    return ask


def run_loop(goal, args, run_dir):
    from .loop import run_task
    from .memory import Memory
    from .screen import RealEnv

    delay = _arg(args, '--delay', 3.0, float)
    hints = _all(args, '--hint')
    for h in hints:
        print(f'  🙋 Подсказка: {h}')
    memory = None if '--no-learn' in args else Memory()
    if memory and memory.lessons:
        print(f'  📚 Опыт: {len(memory.lessons)} уроков')
    _countdown(delay)
    env = RealEnv(_arg(args, '--monitor', 1, int), _arg(args, '--speed', 1.0, float))
    r = run_task(goal, env, max_steps=_arg(args, '--steps', 15, int),
                 refine='--no-refine' not in args, run_dir=run_dir, memory=memory, hints=hints,
                 ask_hint=None if '--no-ask' in args else _ask_hint(delay))
    print(f"\n  {STATUS[r['status']]} · шагов {r['steps']} · {r['seconds']} с")
    print(f"  {r['answer']}")
    for les in r['lessons']:
        print(f"  📚 Запомнила [{les['app'] or 'общее'}]: {les['text']}")
    print(f'  📁 {run_dir}\n')
    return 0 if r['status'] == 'done' else 1


def run_once(task, args, run_dir):
    from pynput import mouse

    from .agent import NotSeen, locate
    from .loop import _to_screen
    from .motion import Aborted, build_events, play, save_rec

    image_path = _arg(args, '--image')
    dry = '--dry-run' in args or (image_path and '--act' not in args)
    if image_path:
        img, offset = Image.open(image_path).convert('RGB'), (0, 0)
        print(f'  🖼  Скриншот из файла: {image_path}')
    else:
        _countdown(_arg(args, '--delay', 3.0, float))
        img, offset = grab(_arg(args, '--monitor', 1, int))
        print(f'  📸 Снимок {img.width}×{img.height}')
    img.save(run_dir / 'screen.png')

    t0 = time.time()
    try:
        res = locate(img, task, refine='--no-refine' not in args, run_dir=run_dir,
                     offset=None if image_path else offset)
    except NotSeen as e:
        print(f'\n  ❌ {e}. Действие отменено — вслепую не кликаем.\n')
        return 2
    print(f"  💬 {res['why']}" + ('' if res['seen'] else '  (выбор по списку элементов, без зрения)'))
    if res['action'] == 'none' or res['x'] is None:
        print('  🤷 Цель не найдена — ничего не делаю.\n')
        return 1

    target = _to_screen(res, offset)
    start = mouse.Controller().position
    events = build_events(start, target)
    save_rec(run_dir / 'action.rec', events, start)
    _plan_image(img, offset, events, res).save(run_dir / 'plan.png')
    print(f"  📍 {res['action']} → ({target['x']}, {target['y']})  [{res['source']}]"
          f"  · {time.time() - t0:.0f} с на зрение · {events[-1]['t']:.2f} с движения")
    print(f'  📁 {run_dir}')

    if dry:
        print('  🧪 Dry-run: курсор не трогаю. План — plan.png\n')
        return 0
    try:
        play(events, speed=_arg(args, '--speed', 1.0, float))
        print('  ✅ Готово\n')
        return 0
    except Aborted as e:
        print(f'  ⏹  {e}\n')
        return 1


def launch_ui(classic: bool = False):
    """Лаунчер: веб-вид (одно окно, дизайн Откликса); без pywebview — старый Tk.
    Лаунчер уже открыт — показываем его и выходим, второе окно не создаём."""
    from . import single
    if not single.acquire():
        if single.ask_to_show():
            print('лаунчер уже открыт — показал его')
            return
        pid = single.holder_pid()
        print(f'лаунчер уже запущен, но не отвечает (pid {pid})')
        if not pid:
            single.tell('AI Vision уже запущен, но не отвечает.\n\n'
                        'Закройте pythonw.exe в диспетчере задач и запустите снова.')
            return
        if not single.ask_user('AI Vision уже запущен, но завис и не отвечает.\n\nЗакрыть его и открыть заново?'):
            return
        if not single.take_over(pid):
            single.tell(f'Не получилось закрыть зависший AI Vision (pid {pid}).\n\n'
                        'Закройте pythonw.exe в диспетчере задач и запустите снова.')
            return
        print(f'зависший лаунчер (pid {pid}) закрыт — открываю новый')
    if not classic:
        try:
            from .weblauncher import main as web
            web()
            return
        except Exception as e:                       # нет pywebview / WebView2 — старое окно
            print(f'веб-лаунчер не запустился ({e}) — открываю классический')
    from .launcher import main as tk
    tk()


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('--ui', 'ui', '--launcher', '--classic'):
        launch_ui(classic=bool(args) and args[0] == '--classic')   # без аргументов — лаунчер
        return
    if args[0].startswith('--'):
        print(__doc__)
        return
    goal = args[0]
    dpi_aware()
    run_dir = ROOT / 'runs' / datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f'\n  🎯 Цель: {goal}')
    single = '--once' in args or '--dry-run' in args or _arg(args, '--image')
    sys.exit(run_once(goal, args, run_dir) if single else run_loop(goal, args, run_dir))


if __name__ == '__main__':
    main()
