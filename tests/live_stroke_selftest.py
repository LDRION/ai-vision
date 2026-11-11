"""
Живая проверка штриха: «рука» агента (motion.build_events + play) рисует фигуру
на холсте тестового окна, а окно записывает реальный путь курсора с зажатой ЛКМ.
Сверяем: пришла ли связная линия через все заданные вершины. Двигает настоящую
мышь — запускать на свободном компьютере. Пошевелить мышью = остановка.

    python tests/live_stroke_selftest.py
"""

import sys
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aivision.capture import dpi_aware  # noqa: E402

dpi_aware()

from aivision import motion                        # noqa: E402
from aivision.detect import foreground_title       # noqa: E402
from aivision.motion import Aborted, build_events, play  # noqa: E402
from pynput import mouse                            # noqa: E402

# Тест сам ведёт мышь; защита «перехвата» здесь только мешает (переход границы
# двух мониторов даёт ложное срабатывание). Проверяем именно корректность линии.
motion.TAKEOVER_PX = 100000

TITLE = 'aivision stroke self-test'
drawn = []          # путь, фактически пришедший в холст при зажатой ЛКМ
result = {}

root = tk.Tk()
root.title(TITLE)
root.geometry('560x560+560+200')
root.attributes('-topmost', True)
canvas = tk.Canvas(root, bg='white', highlightthickness=0)
canvas.pack(fill='both', expand=True)

_down = {'on': False}


def press(e):
    _down['on'] = True
    drawn.clear()
    drawn.append((e.x_root, e.y_root))


def move(e):
    if _down['on']:
        drawn.append((e.x_root, e.y_root))
        if len(drawn) > 1:
            canvas.create_line(e.x - 2, e.y, e.x + 2, e.y, fill='black')


def release(e):
    _down['on'] = False


canvas.bind('<ButtonPress-1>', press)
canvas.bind('<B1-Motion>', move)
canvas.bind('<ButtonRelease-1>', release)


def shape():
    """Домик в координатах экрана: контур + крыша, несколькими штрихами."""
    x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
    box = [(x + 160, y + 380), (x + 160, y + 220), (x + 400, y + 220), (x + 400, y + 380), (x + 160, y + 380)]
    roof = [(x + 140, y + 220), (x + 280, y + 120), (x + 420, y + 220)]
    return [box, roof]


def dist_point_to_path(pt, path):
    return min((pt[0] - q[0]) ** 2 + (pt[1] - q[1]) ** 2 for q in path) ** 0.5


def worker():
    time.sleep(1.0)
    strokes = shape()
    all_ok = True
    try:
        for i, pts in enumerate(strokes):
            if foreground_title() != TITLE:
                result['err'] = f'фокус ушёл: «{foreground_title()}»'
                return
            play(build_events(mouse.Controller().position, {'action': 'stroke', 'points': pts}))
            time.sleep(0.4)
            # каждая заданная вершина должна оказаться близко к реально нарисованному пути
            worst = max(dist_point_to_path(v, drawn) for v in pts) if drawn else 999
            ok = len(drawn) > 10 and worst < 12
            all_ok &= ok
            print(f"  штрих {i + 1}: вершин {len(pts)}, точек пришло {len(drawn)}, "
                  f"макс. отклонение {worst:.1f}px — {'✅' if ok else '❌'}")
        result['ok'] = all_ok
    except Aborted as e:
        result['err'] = str(e)
    finally:
        root.after(400, root.destroy)


root.after(300, lambda: (root.focus_force(), threading.Thread(target=worker, daemon=True).start()))
root.mainloop()

if result.get('err'):
    print('⛔', result['err'])
    sys.exit(1)
print('ИТОГ:', '✅ штрих рисует связную линию через заданные точки' if result.get('ok') else '❌ линия не совпала')
sys.exit(0 if result.get('ok') else 1)
