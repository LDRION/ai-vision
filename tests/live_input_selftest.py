"""
Живая самопроверка ввода на настоящей ОС: «рука» агента (motion.build_events +
motion.play) проходит по виджетам тестового окна, а окно фиксирует, что реально
пришло от системы. Двигает настоящую мышь и печатает — запускать, когда
компьютер свободен. Пошевелить мышью = остановка.

    python tests/live_input_selftest.py
"""

import sys
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aivision.capture import dpi_aware  # noqa: E402

dpi_aware()                                          # до создания окна: координаты в физ. пикселях

from aivision.detect import foreground_title       # noqa: E402
from aivision.motion import Aborted, build_events, play  # noqa: E402
from pynput import keyboard, mouse                  # noqa: E402

# Что реально пришло в ОС. Tk при русской раскладке ищет Ctrl+A по символу («ф»)
# и сочетание не ловит, а браузеры и обычные приложения смотрят на код клавиши —
# поэтому сочетания проверяем на уровне ОС, а не по реакции виджета Tk.
os_keys = []
_ctrl = {'down': False}


def _kd(k):
    if k in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.ctrl):
        _ctrl['down'] = True
    os_keys.append((getattr(k, 'vk', None), _ctrl['down']))


def _ku(k):
    if k in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.ctrl):
        _ctrl['down'] = False


keyboard.Listener(on_press=_kd, on_release=_ku, daemon=True).start()

TITLE = 'aivision live input self-test'
got = {'submitted': [], 'double': 0, 'right': 0, 'press_ms': [], 'wheel': 0}
results = []

root = tk.Tk()
root.title(TITLE)
root.geometry('640x520+640+260')
root.attributes('-topmost', True)

entry = tk.Entry(root, font=('Segoe UI', 16))
entry.place(x=30, y=30, width=580, height=40)
entry.bind('<Return>', lambda e: got['submitted'].append(entry.get()))

scale = tk.Scale(root, from_=0, to=100, orient='horizontal', length=580, sliderlength=30,
                 width=24, showvalue=True)
scale.set(80)
scale.place(x=30, y=100)

dbl = tk.Label(root, text='двойной клик', bg='#e8e8e8', font=('Segoe UI', 13))
dbl.place(x=30, y=200, width=180, height=60)
dbl.bind('<Double-Button-1>', lambda e: got.__setitem__('double', got['double'] + 1))

rgt = tk.Label(root, text='правый клик', bg='#e8e8e8', font=('Segoe UI', 13))
rgt.place(x=230, y=200, width=180, height=60)
rgt.bind('<Button-3>', lambda e: got.__setitem__('right', got['right'] + 1))

hold = tk.Label(root, text='удержание', bg='#e8e8e8', font=('Segoe UI', 13))
hold.place(x=430, y=200, width=180, height=60)
_t = {}
hold.bind('<ButtonPress-1>', lambda e: _t.__setitem__('down', time.perf_counter()))
hold.bind('<ButtonRelease-1>', lambda e: got['press_ms'].append(
    round((time.perf_counter() - _t.get('down', time.perf_counter())) * 1000)))

wheel = tk.Label(root, text='колесо', bg='#e8e8e8', font=('Segoe UI', 13))
wheel.place(x=30, y=290, width=580, height=60)
wheel.bind('<MouseWheel>', lambda e: got.__setitem__('wheel', got['wheel'] + (1 if e.delta < 0 else -1)))


def center(w):
    x, y = w.winfo_rootx(), w.winfo_rooty()
    return x + w.winfo_width() // 2, y + w.winfo_height() // 2, \
        [x, y, x + w.winfo_width(), y + w.winfo_height()]


def knob_x(value):
    """Экранный X бегунка для значения: tk.Scale считает от краёв жёлоба."""
    sx = scale.winfo_rootx()
    x, _y = scale.coords(value)
    return sx + int(x)


def act(name, action, check):
    if foreground_title() != TITLE:
        results.append((name, False, f'фокус ушёл: «{foreground_title()}»'))
        return False
    m = mouse.Controller()
    play(build_events(m.position, action))
    time.sleep(0.4)
    ok, detail = check()
    results.append((name, ok, detail))
    return True


def worker():
    time.sleep(1.2)
    try:
        cx, cy, box = center(entry)
        act('клик + кириллица', dict(action='type', x=cx, y=cy, box=box, text='черновик'),
            lambda: (entry.get() == 'черновик', f'поле={entry.get()!r}'))
        os_keys.clear()
        act('ctrl+a (в ОС)', dict(action='key', x=None, y=None, keys='ctrl+a'),
            lambda: ((0x41, True) in os_keys, f'vk/ctrl в ОС: {os_keys}'))
        act('backspace', dict(action='key', x=None, y=None, keys='backspace'),
            lambda: (entry.get() == 'чернови', f'поле={entry.get()!r}'))
        root.after(0, lambda: entry.delete(0, 'end'))     # подготовка стенда, не действие агента
        time.sleep(0.2)
        act('ввод + Enter', dict(action='type', x=cx, y=cy, box=box, text='Привет, мир 123', keys='enter'),
            lambda: (got['submitted'] == ['Привет, мир 123'], f"Enter отправил {got['submitted']}"))

        sy = scale.winfo_rooty() + int(scale.coords(80)[1])
        act('drag ползунка 80 → 30', dict(action='drag', x=knob_x(80), y=sy, box=None,
                                         to={'x': knob_x(30), 'y': sy, 'box': None}),
            lambda: (abs(scale.get() - 30) <= 2, f'значение={scale.get()}'))

        dx, dy, dbox = center(dbl)
        act('двойной клик', dict(action='double_click', x=dx, y=dy, box=dbox),
            lambda: (got['double'] == 1, f"событий: {got['double']}"))
        rx, ry, rbox = center(rgt)
        act('правый клик', dict(action='right_click', x=rx, y=ry, box=rbox),
            lambda: (got['right'] == 1, f"событий: {got['right']}"))
        hx, hy, hbox = center(hold)
        act('удержание 1.2 с', dict(action='long_press', x=hx, y=hy, box=hbox, seconds=1.2),
            lambda: (bool(got['press_ms']) and 1100 <= got['press_ms'][-1] <= 1500, f"держали {got['press_ms']} мс"))
        wx, wy, wbox = center(wheel)
        act('колесо вниз ×3', dict(action='scroll', x=wx, y=wy, box=wbox, scroll=-3),
            lambda: (got['wheel'] == 3, f"щелчков: {got['wheel']}"))
    except Aborted as e:
        results.append(('остановка', False, str(e)))
    root.after(300, root.destroy)


root.after(300, lambda: (root.focus_force(), threading.Thread(target=worker, daemon=True).start()))
root.mainloop()

ok = sum(r[1] for r in results)
for name, passed, detail in results:
    print(f"{'✅' if passed else '❌'} {name:<26} {detail}")
print(f'{ok}/{len(results)}')
sys.exit(0 if results and ok == len(results) else 1)
