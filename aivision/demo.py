"""
Запись демонстрации человека → рецепт (сценарий без ИИ).

Пользователь показывает, КАК делать; мы ловим его клики/клавиши/прокрутку (pynput,
как в MacroRec) и на каждом клике СНИМАЕМ ЭКРАН, чтобы привязать действие к ЭЛЕМЕНТУ
под курсором (UIA/OCR). Получается тот же Recipe, что из AI-прогона, только без ИИ —
его правят в «Схеме» и проигрывают replay'ем, устойчиво к сдвигам интерфейса (цель
ищется по тексту/роли, а не по координатам; голый MacroRec этого не умеет).

Снимок на клике — быстрый (mss). Тяжёлый разбор элементов идёт ПОСЛЕ записи
(build_recipe), чтобы запись не лагала.

    rec = Recorder().start()
    # ... человек показывает ...
    rec.stop()
    rec.build(scenario_id, goal)     # → Recipe (сохранён в recipes/<id>.json)
"""
import math
import threading
import time
from typing import List, Optional

from . import capture
from .recipe import Recipe, Step

DBL = 0.4          # два клика ближе этого по времени (и рядом) = двойной клик
MOVE_PX = 8        # сдвиг с зажатой ЛКМ больше этого = перетаскивание, а не клик


class Recorder:
    def __init__(self):
        self.events: list = []        # {kind, t, x, y, ...} в порядке действий
        self.shots: dict = {}         # индекс события → (img, offset) для кликов/драгов
        self._lock = threading.Lock()
        self._mouse = None
        self._kb = None
        self._press = None            # данные последнего нажатия: (x, y, img, offset, right)

    def _grab(self, x, y):
        try:
            img, off = capture.grab(capture.monitor_at(int(x), int(y)))
            return img, off
        except Exception:
            return None, (0, 0)

    def _on_click(self, x, y, button, pressed):
        from pynput.mouse import Button
        right = button == Button.right
        if pressed:
            img, off = self._grab(x, y)              # снимок ДО изменения экрана
            self._press = (x, y, img, off, right)
            return
        p, self._press = self._press, None
        px, py, pimg, poff, pright = p if p else (x, y, None, (0, 0), right)
        with self._lock:
            idx = len(self.events)
            if not pright and math.dist((px, py), (x, y)) > MOVE_PX:
                self.events.append({'kind': 'drag', 't': time.perf_counter(), 'x': px, 'y': py, 'to': (x, y)})
                self.shots[idx] = (pimg, poff)
                return
            kind = 'right' if pright else 'click'
            prev = self.events[-1] if self.events else None
            now = time.perf_counter()
            if kind == 'click' and prev and prev['kind'] == 'click' \
                    and now - prev['t'] < DBL and math.dist((prev['x'], prev['y']), (x, y)) < 6:
                prev['kind'] = 'double'              # второй клик рядом и скоро → двойной
                return
            self.events.append({'kind': kind, 't': now, 'x': px, 'y': py})
            self.shots[idx] = (pimg, poff)

    def _on_scroll(self, x, y, dx, dy):
        with self._lock:
            self.events.append({'kind': 'scroll', 't': time.perf_counter(), 'x': x, 'y': y, 'dy': int(dy)})

    def _on_key(self, key):
        name = _key_name(key)
        if name:
            with self._lock:
                self.events.append({'kind': 'key', 't': time.perf_counter(), 'key': name})

    def start(self):
        from pynput import keyboard, mouse
        capture.dpi_aware()
        self.events.clear()
        self.shots.clear()
        self._press = None
        self._mouse = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._kb = keyboard.Listener(on_press=self._on_key)
        self._mouse.start()
        self._kb.start()
        return self

    def stop(self):
        if self._mouse:
            self._mouse.stop()
            self._mouse = None
        if self._kb:
            self._kb.stop()
            self._kb = None
        return self

    def build(self, scenario_id: str, goal: str, detect=None) -> Optional[Recipe]:
        return build_recipe(self.events, self.shots, scenario_id, goal, detect=detect)


def _key_name(key) -> str:
    from pynput.keyboard import Key, KeyCode
    if isinstance(key, KeyCode) and key.char is not None:
        return key.char
    return {Key.enter: 'enter', Key.tab: 'tab', Key.backspace: 'backspace',
            Key.esc: 'esc', Key.space: ' ', Key.delete: 'delete'}.get(key, '')


def _under(x, y, els):
    """Самый маленький элемент, содержащий точку (самая конкретная цель)."""
    inside = [e for e in els if e['box'][0] <= x <= e['box'][2] and e['box'][1] <= y <= e['box'][3]]
    if not inside:
        return None
    return min(inside, key=lambda e: (e['box'][2] - e['box'][0]) * (e['box'][3] - e['box'][1]))


def build_recipe(events: list, shots: dict, scenario_id: str, goal: str, detect=None) -> Optional[Recipe]:
    """События человека + снимки на кликах → Recipe (действия привязаны к элементам)."""
    if detect is None:
        from .detect import find_elements
        detect = find_elements
    steps: List[Step] = []
    buf: list = []                    # набранные символы
    keybuf: list = []                 # спец-клавиши (enter/tab/…)
    last_target = ('', '')

    def flush_type():
        nonlocal buf, keybuf
        if buf or keybuf:
            steps.append(Step(action='type', target_text=last_target[0], target_role=last_target[1],
                              text=''.join(buf), keys=' '.join(keybuf)))
            buf, keybuf = [], []

    for i, e in enumerate(events):
        if e['kind'] == 'key':
            ch = e['key']
            if ch == 'backspace':
                buf.pop() if buf else keybuf.append('backspace')
            elif ch in ('enter', 'tab', 'esc', 'delete'):
                keybuf.append(ch)
                if ch in ('enter', 'tab'):
                    flush_type()
            else:
                buf.append(ch)
            continue
        flush_type()
        if e['kind'] == 'scroll':
            steps.append(Step(action='scroll', scroll=e['dy']))
            continue
        img, off = shots.get(i, (None, (0, 0)))
        els = detect(img, off) if img is not None else []
        cx, cy = e['x'] - off[0], e['y'] - off[1]
        w, h = (img.size if img is not None else (1, 1))
        tgt = _under(cx, cy, els)
        action = {'click': 'click', 'double': 'double_click', 'right': 'right_click', 'drag': 'drag'}[e['kind']]
        st = Step(action=action, target_text=(tgt['text'] if tgt else ''),
                  target_role=(tgt.get('role', '') if tgt else ''),
                  rel=(None if tgt else [round(cx / w, 4), round(cy / h, 4)]))
        if e['kind'] == 'drag' and e.get('to'):
            tx, ty = e['to'][0] - off[0], e['to'][1] - off[1]
            tt = _under(tx, ty, els)
            if tt:
                st.to_text = tt['text']
            else:
                st.to_rel = [round(tx / w, 4), round(ty / h, 4)]
        last_target = (st.target_text, st.target_role)
        steps.append(st)
    flush_type()
    if not steps:
        return None
    r = Recipe(scenario_id=scenario_id, goal=goal, steps=steps, created=time.time())
    r.save()
    return r
