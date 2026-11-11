"""
Человеческое движение курсора → события в формате MacroRec (.rec, JSON Lines).

Траектория:
  • длительность — по закону Фиттса (дальше и мельче цель → дольше);
  • путь — дуга (кубическая Безье с боковым смещением контрольных точек);
  • скорость — профиль минимального рывка (колоколом, как у руки);
  • шум — коррелированный боковой дрейф с огибающей sin, как в MacroRec _glide_to;
  • основное движение промахивается на 2–7 % (чаще недолёт, иногда перелёт),
    затем короткое корректирующее движение точно в цель.
Клик — пауза наведения, нажатие 70–130 мс. Точка внутри рамки — не центр,
а гауссово рассеяние вокруг него.

Воспроизведение — точные тайминги MacroRec (таймер 1 мс, спин-ожидание,
приоритет потока). Если пользователь сам сдвинул мышь — немедленная остановка.
"""

import json
import math
import random
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from pynput import keyboard, mouse
from pynput.mouse import Button

try:
    from macrorec.replayer import (_boost_thread, _parse_key, _timer_begin, _timer_end,
                                   _unboost_thread)
except ImportError:
    # Локальная копия MacroRec лежит рядом (C:\Projects\antianti\macrorec)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'macrorec'))
    from macrorec.replayer import (_boost_thread, _parse_key, _timer_begin, _timer_end,
                                   _unboost_thread)

SAMPLE_DT = 1 / 120          # частота точек траектории
TAKEOVER_PX = 40             # курсор ушёл от заданного дальше — это рука пользователя
_SPIN = 0.002                # последние 2 мс ожидания досчитываем спином


def _precise_sleep(seconds: float):
    """
    Ждать ровно seconds: грубый сон + короткий спин до зафиксированной цели.
    Своя версия вместо macrorec.replayer._precise_sleep: там цель считается
    заново ПОСЛЕ сна (target = now + seconds), и каждое ожидание почти
    удваивается — удержание 1,2 с длилось 2,4 с (замер на уровне ОС).
    """
    if seconds <= 0:
        return
    target = time.perf_counter() + seconds
    if seconds > _SPIN:
        time.sleep(seconds - _SPIN)
    while time.perf_counter() < target:
        pass


class Aborted(Exception):
    pass


def _min_jerk(t: float) -> float:
    return t * t * t * (10 - 15 * t + 6 * t * t)


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def _segment(start, end, target_w: float, t0: float, arc: float = 1.0,
             noise: float = 1.0) -> List[Tuple[float, float, float]]:
    """Одно субдвижение: [(t, x, y), ...] без начальной точки."""
    (sx, sy), (ex, ey) = start, end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist < 1:
        return []

    # Фиттс: T = a + b·log2(D/W + 1), ±15 % индивидуального разброса
    dur = (0.07 + 0.11 * math.log2(dist / max(target_w, 4) + 1)) * random.uniform(0.85, 1.15)
    dur = max(0.08, min(1.1, dur))

    nx, ny = -dy / dist, dx / dist
    bend = random.gauss(0, 0.08) * dist * arc
    bend = max(-0.15 * dist, min(0.15 * dist, bend))
    c1 = (sx + dx * 0.3 + nx * bend, sy + dy * 0.3 + ny * bend)
    c2 = (sx + dx * 0.7 + nx * bend * 0.6, sy + dy * 0.7 + ny * bend * 0.6)

    noise_amp = min(6.0, dist * 0.012) * noise
    steps = max(6, int(dur / SAMPLE_DT))
    pts, drift = [], 0.0
    for i in range(1, steps + 1):
        u = i / steps
        bx, by = _bezier(start, c1, c2, end, _min_jerk(u))
        env = math.sin(math.pi * u) * noise_amp
        drift = drift * 0.72 + random.gauss(0, max(0.1, env * 0.35)) * 0.28
        n = max(-env, min(env, drift))
        jitter_t = random.uniform(-0.0008, 0.0008) if i < steps else 0.0
        pts.append((t0 + dur * u + jitter_t, bx + nx * n, by + ny * n))
    return pts


def pick_point(x: float, y: float, box: Optional[list]) -> Tuple[int, int]:
    """Точка клика: внутри рамки метки — гауссово рассеяние у центра, иначе ±1 px."""
    if box:
        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        px = random.gauss((x1 + x2) / 2, w / 8)
        py = random.gauss((y1 + y2) / 2, h / 8)
        px = min(max(px, x1 + w * 0.2), x2 - w * 0.2)
        py = min(max(py, y1 + h * 0.25), y2 - h * 0.25)
        return int(round(px)), int(round(py))
    return int(round(x + random.uniform(-1, 1))), int(round(y + random.uniform(-1, 1)))


def path_to(start: Tuple[int, int], end: Tuple[int, int], target_w: float = 30,
            noise: float = 1.0) -> List[dict]:
    """События 'move' от start до end: основное движение с промахом + коррекция."""
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 2:
        return [{'t': 0.0, 'e': 'move', 'x': end[0], 'y': end[1]}]

    pts = []
    if dist > 60:
        ang = math.atan2(end[1] - start[1], end[0] - start[0])
        k = random.choice([-1, -1, 1]) * random.uniform(0.02, 0.07) * noise   # недолёт чаще перелёта
        err = max(-25, min(25, dist * k))
        side = random.gauss(0, min(6, dist * 0.01)) * noise
        aim = (end[0] + math.cos(ang) * err - math.sin(ang) * side,
               end[1] + math.sin(ang) * err + math.cos(ang) * side)
        pts += _segment(start, aim, target_w, 0.0, noise=noise)
        pause = pts[-1][0] + random.uniform(0.03, 0.09)
        pts += _segment(aim, end, target_w, pause, arc=0.3, noise=noise)
    else:
        pts += _segment(start, end, target_w, 0.0, noise=noise)

    events, last = [], None
    for t, x, y in pts:
        xy = (int(round(x)), int(round(y)))
        if xy != last:
            events.append({'t': round(t, 6), 'e': 'move', 'x': xy[0], 'y': xy[1]})
            last = xy
    events[-1].update(x=end[0], y=end[1])
    return events


def _click(t: float, x: int, y: int, btn: str = 'left') -> Tuple[List[dict], float]:
    t += random.uniform(0.06, 0.18)                      # наведение перед нажатием
    hold = random.uniform(0.07, 0.13)
    evs = [{'t': round(t, 6), 'e': 'click', 'x': x, 'y': y, 'btn': btn, 'down': True},
           {'t': round(t + hold, 6), 'e': 'click', 'x': x, 'y': y, 'btn': btn, 'down': False}]
    return evs, t + hold


KEY_NAMES = {
    'enter': 'enter', 'return': 'enter', 'esc': 'esc', 'escape': 'esc', 'tab': 'tab',
    'backspace': 'backspace', 'delete': 'delete', 'del': 'delete', 'space': 'space',
    'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'home': 'home', 'end': 'end',
    'pageup': 'page_up', 'page_up': 'page_up', 'pgup': 'page_up',
    'pagedown': 'page_down', 'page_down': 'page_down', 'pgdn': 'page_down',
    'ctrl': 'ctrl', 'control': 'ctrl', 'alt': 'alt', 'shift': 'shift', 'win': 'cmd', 'cmd': 'cmd',
    **{f'f{i}': f'f{i}' for i in range(1, 13)},
}


def _key_event(t: float, name: str, down: bool) -> dict:
    """
    Спец-клавиша → 'Key.enter'; буква/цифра в сочетании → виртуальный код:
    юникод-инъекция с зажатым Ctrl горячую клавишу не вызывает. Формат и
    приоритет vk — как у MacroRec (_parse_key).
    """
    n = name.strip().lower()
    if n in KEY_NAMES:
        return {'t': round(t, 6), 'e': 'key', 'key': f'Key.{KEY_NAMES[n]}', 'vk': None, 'down': down}
    if len(n) == 1 and n.isascii() and n.isalnum():
        return {'t': round(t, 6), 'e': 'key', 'key': n, 'vk': ord(n.upper()), 'down': down}
    raise ValueError(f'неизвестная клавиша: {name!r}')


def parse_keys(spec: str) -> List[List[str]]:
    """'ctrl+a, backspace' / 'ctrl+l enter' → [['ctrl', 'a'], ['backspace']]."""
    combos = [c for c in spec.replace(',', ' ').split() if c]
    return [[k for k in c.split('+') if k] for c in combos]


def _press_keys(t: float, spec: str) -> Tuple[List[dict], float]:
    evs = []
    for combo in parse_keys(spec):
        t += random.uniform(0.08, 0.2)
        for k in combo:                                  # модификаторы — по очереди, как рукой
            evs.append(_key_event(t, k, True))
            t += random.uniform(0.03, 0.07)
        t += random.uniform(0.04, 0.09)
        for k in reversed(combo):
            evs.append(_key_event(t, k, False))
            t += random.uniform(0.02, 0.05)
    return evs, t


def _drag(t: float, src: Tuple[int, int], to: dict, noise: float = 1.0) -> Tuple[List[dict], float]:
    """
    Зажать ЛКМ в src, «подхватить», провести к to и отпустить. Перенос медленнее
    обычного движения, по пути много точек — веб-перетаскивание (HTML5 DnD,
    ползунки) срабатывает только на движение с зажатой кнопкой.
    """
    box = to.get('box')
    dst = pick_point(to['x'], to['y'], box) if box else (int(round(to['x'])), int(round(to['y'])))
    t += random.uniform(0.08, 0.18)
    evs = [{'t': round(t, 6), 'e': 'click', 'x': src[0], 'y': src[1], 'btn': 'left', 'down': True}]
    t += random.uniform(0.12, 0.25)                      # «подхватили»
    path = path_to(src, dst, min(box[2] - box[0], box[3] - box[1]) if box else 6, noise=noise)
    slow = random.uniform(1.25, 1.5)
    for ev in path:
        evs.append(dict(ev, t=round(t + ev['t'] * slow, 6)))
    t = evs[-1]['t'] + random.uniform(0.1, 0.22)        # задержались над целью — подсветка зоны сброса
    evs.append({'t': round(t, 6), 'e': 'move', 'x': dst[0], 'y': dst[1]})
    t += random.uniform(0.03, 0.08)
    evs.append({'t': round(t, 6), 'e': 'click', 'x': dst[0], 'y': dst[1], 'btn': 'left', 'down': False})
    return evs, t


def _stroke(t: float, start: Tuple[int, int], points: list, noise: float = 1.0) -> Tuple[List[dict], float]:
    """
    Свободный штрих: зажать ЛКМ в первой точке, провести через все точки
    с зажатой кнопкой и отпустить в последней. Обобщает drag на ломаную/кривую —
    рисование, подпись, жест, лассо-выделение. Длинные отрезки дробим, чтобы линия
    была сплошной; шум небольшой, иначе фигура перестаёт читаться.
    """
    pts = [(int(round(p[0])), int(round(p[1]))) for p in points if p is not None]
    if len(pts) < 2:
        return [], t

    evs = []
    for ev in path_to(start, pts[0], 8, noise=noise):          # подвод к началу штриха
        evs.append(dict(ev, t=round(t + ev['t'], 6)))
    t = evs[-1]['t'] + random.uniform(0.05, 0.12)
    evs.append({'t': round(t, 6), 'e': 'click', 'x': pts[0][0], 'y': pts[0][1], 'btn': 'left', 'down': True})
    t += random.uniform(0.04, 0.08)

    prev = pts[0]
    amp = min(1.5, 0.6 * noise)                                # лёгкая дрожь руки, фигуру не ломает
    for p in pts[1:]:
        dx, dy = p[0] - prev[0], p[1] - prev[1]
        dist = math.hypot(dx, dy)
        steps = max(1, int(dist / 6))
        nx, ny = (-dy / dist, dx / dist) if dist else (0.0, 0.0)
        for i in range(1, steps + 1):
            u = i / steps
            n = random.uniform(-amp, amp) * math.sin(math.pi * u) if i < steps else 0.0
            t += max(0.004, dist / steps * 0.0018) + random.uniform(-0.0006, 0.0006)
            evs.append({'t': round(t, 6), 'e': 'move',
                        'x': int(round(prev[0] + dx * u + nx * n)),
                        'y': int(round(prev[1] + dy * u + ny * n))})
        prev = p
    t += random.uniform(0.03, 0.08)
    evs.append({'t': round(t, 6), 'e': 'click', 'x': pts[-1][0], 'y': pts[-1][1], 'btn': 'left', 'down': False})
    return evs, t


def build_events(start: Tuple[int, int], action: dict, noise: float = 1.0) -> List[dict]:
    """
    action — решение агента в экранных координатах: action, x, y, box, text, keys, scroll,
    seconds (long_press), to = {x, y, box} (drag), points = [[x,y],…] (stroke). x/y нет —
    действие без мыши (key, scroll на месте). noise — множитель бокового шума (0 = ровно).
    Возвращает события MacroRec с t от 0.
    """
    kind = action['action']
    evs, t = [], 0.0
    if kind == 'stroke':
        return _stroke(0.0, start, action.get('points') or [], noise=noise)[0]
    if action.get('x') is not None:
        box = action.get('box')
        x, y = pick_point(action['x'], action['y'], box)
        target_w = min(box[2] - box[0], box[3] - box[1]) if box else 12
        evs = path_to(start, (x, y), target_w, noise=noise)
        t = evs[-1]['t']
    else:
        x, y = start

    aimed = action.get('x') is not None             # без цели — не кликаем «куда попало»
    if kind == 'click' or (kind == 'type' and aimed):
        c, t = _click(t, x, y)
        evs += c
    elif kind == 'right_click':
        c, t = _click(t, x, y, 'right')
        evs += c
    elif kind == 'double_click':
        c, t = _click(t, x, y)
        evs += c
        t += random.uniform(0.07, 0.14)                  # пауза между щелчками двойного
        hold = random.uniform(0.06, 0.10)
        evs += [{'t': round(t, 6), 'e': 'click', 'x': x, 'y': y, 'btn': 'left', 'down': True},
                {'t': round(t + hold, 6), 'e': 'click', 'x': x, 'y': y, 'btn': 'left', 'down': False}]
        t += hold
    elif kind == 'scroll':
        notches = action.get('scroll') or -3
        t += random.uniform(0.1, 0.25)
        for _ in range(abs(notches)):
            evs.append({'t': round(t, 6), 'e': 'scroll', 'x': x, 'y': y,
                        'dx': 0, 'dy': 1 if notches > 0 else -1})
            t += random.uniform(0.04, 0.12)
    elif kind == 'hover':
        t += random.uniform(0.3, 0.6)                    # задержались — меню/подсказка успевают открыться
        evs.append({'t': round(t, 6), 'e': 'move', 'x': x, 'y': y})
    elif kind == 'long_press':
        hold = min(5.0, max(0.4, float(action.get('seconds') or 1.0)))
        c, t = _click(t, x, y)
        c[1]['t'] = round(c[0]['t'] + hold * random.uniform(0.95, 1.08), 6)
        evs += c
        t = c[1]['t']
    elif kind == 'drag':
        evs_drag, t = _drag(t, (x, y), action['to'], noise=noise)
        evs += evs_drag

    if kind == 'type' and action.get('clear'):
        t += random.uniform(0.2, 0.4)                    # курсор встал в поле
        k, t = _press_keys(t, 'ctrl+a, delete')          # заменить целиком: выделить всё и стереть
        evs += k

    if kind == 'type' and action.get('text'):
        t += random.uniform(0.25, 0.5)                   # курсор встал в поле
        for ch in action['text']:
            hold = random.uniform(0.04, 0.11)
            evs.append({'t': round(t, 6), 'e': 'key', 'key': ch, 'vk': None, 'down': True})
            evs.append({'t': round(t + hold, 6), 'e': 'key', 'key': ch, 'vk': None, 'down': False})
            gap = random.lognormvariate(math.log(0.12), 0.35)
            t += hold + min(0.45, gap) + (random.uniform(0.1, 0.3) if ch == ' ' else 0)

    if kind in ('key', 'type') and action.get('keys'):
        k, t = _press_keys(t, action['keys'])
        evs += k
    if kind == 'cast' and action.get('keys'):           # навык: курсор на цели → клавиша → ЛКМ по цели
        k, t = _press_keys(t, action['keys'])
        evs += k
        if aimed and not action.get('quick'):          # быстрый каст срабатывает от самой клавиши
            t += random.uniform(0.05, 0.12)
            c, t = _click(t, x, y)
            evs += c
    return evs


def save_rec(path: Path, events: List[dict], start: Tuple[int, int]) -> Path:
    """Тот же формат, что пишет MacroRec Recorder — файл открывается в его редакторе."""
    header = {'version': '1.0', 'event_count': len(events),
              'duration': round(events[-1]['t'], 3) if events else 0,
              'initial_pos': list(start)}
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(header, ensure_ascii=False) + '\n')
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    return path


def foreground_window() -> int:
    """Дескриптор активного окна (0 вне Windows)."""
    if sys.platform != 'win32':
        return 0
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow() or 0


def play(events: List[dict], speed: float = 1.0):
    """
    Воспроизведение с таймингами MacroRec. Раскладку не трогаем (в отличие от
    Player.play): текст идёт юникод-символами, ему раскладка не важна.
    Ввод с клавиатуры привязан к окну, где начался: если фокус ушёл в другое
    окно (пользователь переключился, всплыло уведомление), ввод обрывается —
    иначе текст и сочетания клавиш попадут не туда.
    """
    m, kb = mouse.Controller(), keyboard.Controller()
    _timer_begin()
    _boost_thread()
    pressed = []
    key_window = None
    try:
        last_set = m.position
        wall = time.perf_counter()
        for ev in events:
            _precise_sleep(wall + ev['t'] / speed - time.perf_counter())
            cx, cy = m.position
            if math.hypot(cx - last_set[0], cy - last_set[1]) > TAKEOVER_PX:
                raise Aborted('пользователь перехватил мышь — остановлено')
            e = ev['e']
            if e == 'move':
                m.position = (ev['x'], ev['y'])
                last_set = (ev['x'], ev['y'])
            elif e == 'click':
                btn = getattr(Button, ev['btn'], Button.left)
                if ev['down']:
                    m.press(btn)
                    pressed.append(('m', btn))
                else:
                    m.release(btn)
                    if ('m', btn) in pressed:
                        pressed.remove(('m', btn))
            elif e == 'scroll':
                m.scroll(ev['dx'], ev['dy'])
            elif e == 'key':
                key = _parse_key(ev)
                if key is None:
                    continue
                if ev['down']:
                    hwnd = foreground_window()
                    if key_window is None:
                        key_window = hwnd
                    elif hwnd != key_window:
                        raise Aborted('активное окно сменилось посреди ввода — клавиатура остановлена')
                    kb.press(key)
                    pressed.append(('k', key))
                else:
                    kb.release(key)
                    if ('k', key) in pressed:
                        pressed.remove(('k', key))
    finally:
        # Ничего не оставляем зажатым, даже при остановке посреди клика
        for kind, k in pressed:
            try:
                (m if kind == 'm' else kb).release(k)
            except Exception:
                pass
        _unboost_thread()
        _timer_end()
