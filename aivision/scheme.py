"""
«Схема» сценария — пошаговый просмотр прогона с ручной правкой, как эмулятор.

Слева список шагов, по центру — экран на шаге (что видел агент и что сделал),
справа — правка: тип действия, точка клика (задаётся мышью прямо по картинке),
текст/клавиши, шум именно этого шага. «Проиграть действие» выполняет шаг
вживую — удобно подогнать рукой то, что агент сделал неточно.

Два режима:
  • обзор — открывается кнопкой «Схема» для последнего прогона;
  • живой — лаунчер открывает перед запуском, окно само подхватывает шаги
    по мере их появления (опрашивает папку прогона).

Данные: runs/<время>/step_NN/{screen.png, marked.png, action.json} + result.json.
"""

import json
import threading
import time
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw

from .ui_theme import PAD, PALETTE, RADIUS

VIEW_W, VIEW_H = 640, 420
POINT_ACTIONS = {'click', 'double_click', 'right_click', 'type', 'drag', 'hover', 'long_press'}
ACTION_CHOICES = ['click', 'double_click', 'right_click', 'hover', 'long_press',
                  'drag', 'stroke', 'type', 'key', 'scroll', 'wait']
POLL_MS = 700


class SchemeView(ctk.CTkFrame):
    """Панель «Схема» — встраивается во вкладку лаунчера (раньше было отдельным окном)."""

    def __init__(self, master, scenario, f, live: bool = False):
        super().__init__(master, fg_color=PALETTE['bg'], corner_radius=0)
        self.f = f
        self.live = live
        self.follow = live

        self.run_dir = Path(scenario.last_run) if scenario.last_run else None
        self.steps: List[dict] = []
        self.idx = 0
        self.show_marked = True
        self._disp = None            # (scale, ox, oy, img_w, img_h) текущей картинки
        self._edit = None            # действие выбранного шага (правится)
        self._playing = False

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_list()
        self._build_viewer()
        self._build_editor()
        self.reload(select_last=live)
        if live:
            self.after(POLL_MS, self._poll)

    # ── Загрузка ─────────────────────────────────────────────────────────────
    def reload(self, select_last: bool = False):
        """
        Перечитать папку прогона и перерисовать ТОЛЬКО изменившееся. Живой режим
        зовёт это каждые 0,7 с: если каждый раз пересоздавать список и панель
        правки, прокручиваемый список схлопывается и интерфейс мерцает.
        """
        prev = len(self.steps)
        steps = self._load_steps()
        sig = _signature(steps)
        self._update_head(len(steps))
        if sig == getattr(self, '_sig', None) and not select_last:
            return                                   # ничего нового — ничего не трогаем
        self._sig = sig
        self.steps = steps
        self._fill_rows()
        if not self.steps:
            self._show_empty_viewer()
            return
        if select_last or self.idx >= len(self.steps) or (self.follow and len(self.steps) > prev):
            self.idx = len(self.steps) - 1
        if _signature([self.steps[self.idx]]) == getattr(self, '_shown', None):
            self._highlight()                        # выбранный шаг тот же — правки в полях не трогаем
            return
        self._show(self.idx)

    def _load_steps(self) -> List[dict]:
        self.result = {}
        if not self.run_dir or not self.run_dir.exists():
            return []
        rj = self.run_dir / 'result.json'
        if rj.exists():
            try:
                self.result = json.loads(rj.read_text(encoding='utf-8'))
            except ValueError:
                pass
        history = self.result.get('history', [])
        dirs = sorted(self.run_dir.glob('step_*'))
        steps = []
        for d in dirs:
            try:
                n = int(d.name.split('_')[1])
            except (IndexError, ValueError):
                continue
            line = next((h for h in history if h.strip().startswith(f'{n}.')), f'{n}.')
            if line == f'{n}.' and d == dirs[-1] and self.result.get('status'):
                # финальный шаг — решение «готово/не получается», действия нет: показываем итог
                line = f"{n}. {self.result['status']}: {self.result.get('answer', '')}"
            steps.append({'n': n, 'dir': d, 'line': line.split('.', 1)[-1].strip(),
                          'marked': d / 'marked.png', 'screen': d / 'screen.png',
                          'action': self._load_action(d), 'why': self._why(d)})
        return steps

    def _load_action(self, d: Path) -> Optional[dict]:
        p = d / 'action.json'
        if p.exists():
            try:
                return json.loads(p.read_text(encoding='utf-8'))
            except ValueError:
                return None
        return None

    def _why(self, d: Path) -> str:
        act = self._load_action(d)
        if act and act.get('why'):
            return act['why']
        for name in ('marked_answer1.txt', 'marked_answer2.txt', 'marked_answer3.txt'):
            p = d / name
            if p.exists():
                try:
                    txt = p.read_text(encoding='utf-8').replace('```json', '').replace('```', '')
                    return json.loads(txt).get('why', '')
                except ValueError:
                    continue
        return ''

    def _poll(self):
        if not self.winfo_exists():
            return
        self.reload()
        done = self.run_dir and (self.run_dir / 'result.json').exists()
        if not done:
            self.after(POLL_MS, self._poll)
        else:
            self.reload()          # финальный проход: истории и итог уже записаны
            self.follow = False

    # ── Список шагов (левая колонка) ─────────────────────────────────────────
    def _build_list(self):
        panel = ctk.CTkFrame(self, fg_color=PALETTE['surface'], corner_radius=0, width=280)
        panel.grid(row=0, column=0, sticky='nsew')
        panel.grid_propagate(False)
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.head = ctk.CTkFrame(panel, fg_color='transparent')
        self.head.grid(row=0, column=0, sticky='ew', padx=PAD, pady=(PAD, 6))
        self.head_title = ctk.CTkLabel(self.head, text='Шаги', font=self.f['h2'],
                                       text_color=PALETTE['text'])
        self.head_title.pack(anchor='w')
        self.head_sub = ctk.CTkLabel(self.head, text='', font=self.f['small'],
                                     text_color=PALETTE['text_muted'])
        self.head_sub.pack(anchor='w')
        self.rows_frame = ctk.CTkScrollableFrame(panel, fg_color='transparent')
        self.rows_frame.grid(row=1, column=0, sticky='nsew', padx=8, pady=8)
        self.step_rows = []

    def _update_head(self, count: Optional[int] = None):
        status = self.result.get('status', '')
        live = ' · идёт…' if (self.live and not status) else ''
        text = f'{len(self.steps) if count is None else count} шагов · {status or "ожидание"}{live}'
        if self.head_sub.cget('text') != text:
            self.head_sub.configure(text=text)

    def _fill_rows(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.step_rows = []
        for i, st in enumerate(self.steps):
            row = ctk.CTkFrame(self.rows_frame, corner_radius=RADIUS, fg_color='transparent')
            row.pack(fill='x', pady=3)
            inner = ctk.CTkFrame(row, fg_color='transparent')
            inner.pack(fill='x', padx=12, pady=8)
            ctk.CTkLabel(inner, text=f'Шаг {st["n"]}', font=self.f['body_b'],
                         text_color=PALETTE['text'], anchor='w').pack(fill='x')
            edited = '  ✎' if (st['action'] or {}).get('edited') else ''
            ctk.CTkLabel(inner, text=(st['line'][:44] or '—') + edited, font=self.f['small'],
                         text_color=PALETTE['text_muted'], anchor='w',
                         wraplength=236, justify='left').pack(fill='x')
            for w in (row, inner, *inner.winfo_children()):
                w.bind('<Button-1>', lambda _e, k=i: self._select_step(k))
            self.step_rows.append(row)

    # ── Просмотрщик (центр) ──────────────────────────────────────────────────
    def _build_viewer(self):
        main = ctk.CTkFrame(self, fg_color='transparent')
        main.grid(row=0, column=1, sticky='nsew', padx=PAD, pady=PAD)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(main, fg_color='transparent')
        bar.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        self.toggle = ctk.CTkSegmentedButton(
            bar, values=['Разметка', 'Чистый экран'], font=self.f['body'],
            command=self._on_toggle, fg_color=PALETTE['surface_muted'],
            selected_color=PALETTE['surface'], selected_hover_color=PALETTE['border'],
            unselected_color=PALETTE['surface_muted'], unselected_hover_color=PALETTE['border'],
            text_color=PALETTE['text'])
        self.toggle.set('Разметка')
        self.toggle.pack(side='left')
        for txt, d in (('›', 1), ('‹', -1)):
            ctk.CTkButton(bar, text=txt, width=42, height=32, font=self.f['body_b'],
                          corner_radius=10, fg_color=PALETTE['surface'], text_color=PALETTE['text'],
                          hover_color=PALETTE['surface_muted'], border_width=1,
                          border_color=PALETTE['border'],
                          command=lambda dd=d: self._step(dd)).pack(side='right', padx=(6, 0))

        self.image_card = ctk.CTkFrame(main, fg_color=PALETTE['surface'], corner_radius=RADIUS,
                                       border_width=1, border_color=PALETTE['border'])
        self.image_card.grid(row=1, column=0, sticky='nsew')
        self.image_label = ctk.CTkLabel(self.image_card, text='')
        self.image_label.pack(expand=True, padx=10, pady=10)
        self.image_label.bind('<Button-1>', self._on_image_click)

        cap = ctk.CTkFrame(main, fg_color='transparent')
        cap.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        self.action_label = ctk.CTkLabel(cap, text='', font=self.f['body_b'],
                                         text_color=PALETTE['text'], anchor='w', justify='left')
        self.action_label.pack(fill='x')
        self.why_label = ctk.CTkLabel(cap, text='', font=self.f['small'],
                                      text_color=PALETTE['text_muted'], anchor='w',
                                      justify='left', wraplength=640)
        self.why_label.pack(fill='x', pady=(4, 0))

    def _show_empty_viewer(self):
        self.action_label.configure(text='Пока нет шагов')
        self.why_label.configure(text='Запустите сценарий — шаги появятся здесь по мере выполнения.')
        self.image_label.configure(image=None, text='')
        self._clear_editor()

    # ── Редактор шага (правая колонка) ───────────────────────────────────────
    def _build_editor(self):
        self.ed = ctk.CTkScrollableFrame(self, fg_color=PALETTE['surface'], width=320,
                                         corner_radius=0)
        self.ed.grid(row=0, column=2, sticky='nsew')
        self.grid_columnconfigure(2, weight=0, minsize=320)

    def _clear_editor(self):
        for w in self.ed.winfo_children():
            w.destroy()
        self._edit = None

    def _render_editor(self):
        self._clear_editor()
        st = self.steps[self.idx]
        act = st['action']
        ctk.CTkLabel(self.ed, text='Правка шага', font=self.f['h2'],
                     text_color=PALETTE['text']).pack(anchor='w', padx=PAD, pady=(PAD, 2))
        if not act:
            ctk.CTkLabel(self.ed, text='У этого шага нет сохранённого действия\n(ничего не исполнялось).',
                         font=self.f['small'], text_color=PALETTE['text_muted'],
                         justify='left').pack(anchor='w', padx=PAD, pady=6)
            return
        self._edit = dict(act)

        ctk.CTkLabel(self.ed, text='Тип действия', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=PAD, pady=(10, 2))
        self.act_menu = ctk.CTkOptionMenu(
            self.ed, values=ACTION_CHOICES, font=self.f['body'], command=self._on_act_change,
            fg_color=PALETTE['field'], button_color=PALETTE['border'],
            button_hover_color=PALETTE['border'], text_color=PALETTE['text'],
            dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
        self.act_menu.set(self._edit.get('action', 'click'))
        self.act_menu.pack(fill='x', padx=PAD)

        self.point_hint = ctk.CTkLabel(
            self.ed, text='Кликните по картинке слева,\nчтобы задать точку действия.',
            font=self.f['small'], text_color=PALETTE['text_muted'], justify='left')
        self.point_hint.pack(anchor='w', padx=PAD, pady=(10, 4))
        self.xy_entry = self._xy_row('Точка X, Y', self._edit.get('x'), self._edit.get('y'))
        self.to_entry = self._xy_row('Куда (drag)', (self._edit.get('to') or {}).get('x'),
                                     (self._edit.get('to') or {}).get('y'))

        self.text_entry = self._field('Текст (для type)', self._edit.get('text', ''))
        self.keys_entry = self._field('Клавиши (для key/type)', self._edit.get('keys', ''))

        ctk.CTkLabel(self.ed, text='Шум траектории этого шага', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=PAD, pady=(10, 2))
        nrow = ctk.CTkFrame(self.ed, fg_color='transparent')
        nrow.pack(fill='x', padx=PAD)
        self.noise_var = ctk.DoubleVar(value=float(self._edit.get('noise', 1.0)))
        self.noise_lbl = ctk.CTkLabel(nrow, text='', font=self.f['body_b'],
                                      text_color=PALETTE['text'], width=52)
        self.noise_lbl.pack(side='right')
        self._fmt_noise()
        ctk.CTkSlider(nrow, from_=0.0, to=3.0, variable=self.noise_var,
                      progress_color=PALETTE['accent'], button_color=PALETTE['accent'],
                      button_hover_color=PALETTE['accent_hover'], fg_color=PALETTE['track'],
                      command=lambda _v: self._fmt_noise()).pack(side='left', fill='x', expand=True)

        btns = ctk.CTkFrame(self.ed, fg_color='transparent')
        btns.pack(fill='x', padx=PAD, pady=(16, 8))
        ctk.CTkButton(btns, text='Сохранить', font=self.f['body_b'], height=38, corner_radius=10,
                      fg_color=PALETTE['surface_muted'], hover_color=PALETTE['border'],
                      text_color=PALETTE['text'], command=self._save_edit).pack(fill='x')
        ctk.CTkButton(btns, text='▶  Проиграть действие', font=self.f['body_b'], height=38,
                      corner_radius=10, fg_color=PALETTE['accent'],
                      hover_color=PALETTE['accent_hover'], text_color=PALETTE['accent_text'],
                      command=self._play_action).pack(fill='x', pady=(8, 0))
        self.ed_status = ctk.CTkLabel(self.ed, text='', font=self.f['small'],
                                      text_color=PALETTE['text_muted'], wraplength=280, justify='left')
        self.ed_status.pack(anchor='w', padx=PAD, pady=(6, 16))

    def _xy_row(self, label, x, y):
        ctk.CTkLabel(self.ed, text=label, font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=PAD, pady=(8, 2))
        row = ctk.CTkFrame(self.ed, fg_color='transparent')
        row.pack(fill='x', padx=PAD)
        ex = ctk.CTkEntry(row, font=self.f['body'], height=34, width=120, corner_radius=10,
                          fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                          text_color=PALETTE['text'])
        ex.insert(0, '' if x is None else str(x))
        ex.pack(side='left', expand=True, fill='x', padx=(0, 6))
        ey = ctk.CTkEntry(row, font=self.f['body'], height=34, width=120, corner_radius=10,
                          fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                          text_color=PALETTE['text'])
        ey.insert(0, '' if y is None else str(y))
        ey.pack(side='left', expand=True, fill='x')
        return (ex, ey)

    def _field(self, label, value):
        ctk.CTkLabel(self.ed, text=label, font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=PAD, pady=(8, 2))
        e = ctk.CTkEntry(self.ed, font=self.f['body'], height=34, corner_radius=10,
                         fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                         text_color=PALETTE['text'])
        e.insert(0, value or '')
        e.pack(fill='x', padx=PAD)
        return e

    def _fmt_noise(self):
        v = self.noise_var.get()
        self.noise_lbl.configure(text='ровно' if v < 0.05 else f'{v:.1f}×')

    def _on_act_change(self, _v):
        pass

    # ── Клик по картинке → точка действия ────────────────────────────────────
    def _on_image_click(self, event):
        if self._edit is None or not self._disp:
            return
        scale, ox, oy, iw, ih = self._disp
        px = (event.x - ox) / scale
        py = (event.y - oy) / scale
        if not (0 <= px <= iw and 0 <= py <= ih):
            return
        off = self._edit.get('offset', [0, 0])
        sx, sy = int(round(px + off[0])), int(round(py + off[1]))
        act = self.act_menu.get()
        if act == 'drag' and (self.xy_entry[0].get().strip() and not self.to_entry[0].get().strip()):
            self.to_entry[0].delete(0, 'end'); self.to_entry[0].insert(0, str(sx))
            self.to_entry[1].delete(0, 'end'); self.to_entry[1].insert(0, str(sy))
        else:
            self.xy_entry[0].delete(0, 'end'); self.xy_entry[0].insert(0, str(sx))
            self.xy_entry[1].delete(0, 'end'); self.xy_entry[1].insert(0, str(sy))
        self._preview_marker(sx, sy)

    def _collect_edit(self) -> dict:
        e = dict(self._edit)
        e['action'] = self.act_menu.get()
        e['x'] = _num(self.xy_entry[0].get())
        e['y'] = _num(self.xy_entry[1].get())
        # точку задали рукой — рамку метки убираем, чтобы клик шёл точно в точку
        if (e['x'], e['y']) != (self._edit.get('x'), self._edit.get('y')):
            e['box'] = None
        tx, ty = _num(self.to_entry[0].get()), _num(self.to_entry[1].get())
        e['to'] = {'x': tx, 'y': ty, 'box': None} if tx is not None and ty is not None else None
        e['text'] = self.text_entry.get()
        e['keys'] = self.keys_entry.get()
        e['noise'] = round(self.noise_var.get(), 2)
        return e

    def _save_edit(self):
        from .motion import build_events, save_rec
        e = self._collect_edit()
        e['edited'] = True
        st = self.steps[self.idx]
        (st['dir'] / 'action.json').write_text(json.dumps(e, ensure_ascii=False, indent=1),
                                               encoding='utf-8')
        start = tuple(e.get('start') or (e.get('x') or 0, e.get('y') or 0))
        try:
            events = build_events(start, e, noise=e['noise'])
            save_rec(st['dir'] / 'action.rec', events, start)
        except Exception as ex:
            self.ed_status.configure(text=f'Сохранено, но .rec не обновлён: {ex}', text_color=PALETTE['danger'])
        else:
            self.ed_status.configure(text='Сохранено. action.json и action.rec обновлены.',
                                     text_color=PALETTE['ok'])
        st['action'] = e
        self._fill_rows()
        self._highlight()
        self._sig = _signature(self.steps)          # иначе следующий опрос сочтёт это «новым»
        self._shown = _signature([st])

    def _play_action(self):
        if self._playing:
            return
        e = self._collect_edit()
        self._playing = True
        self.ed_status.configure(text='Старт через 3 с… переключитесь в нужное окно.',
                                 text_color=PALETTE['text_muted'])
        threading.Thread(target=self._play_worker, args=(e,), daemon=True).start()

    def _play_worker(self, e: dict):
        from .capture import dpi_aware
        from .motion import Aborted, build_events, play
        try:
            from pynput import mouse
            dpi_aware()
            for c in range(3, 0, -1):
                self._set_ed_status(f'Старт через {c}…', PALETTE['text_muted'])
                time.sleep(1)
            start = mouse.Controller().position
            events = build_events(start, e, noise=e.get('noise', 1.0))
            play(events)
            self._set_ed_status('Готово.', PALETTE['ok'])
        except Aborted as ex:
            self._set_ed_status(f'Остановлено: {ex}', PALETTE['danger'])
        except Exception as ex:
            self._set_ed_status(f'Ошибка: {ex}', PALETTE['danger'])
        finally:
            self._playing = False

    def _set_ed_status(self, text, color):
        self.after(0, lambda: self.ed_status.configure(text=text, text_color=color)
                   if self.ed_status.winfo_exists() else None)

    # ── Навигация и отрисовка ────────────────────────────────────────────────
    def _on_toggle(self, _v):
        self.show_marked = self.toggle.get() == 'Разметка'
        self._render_image()

    def _step(self, delta):
        self.follow = False
        self._select_step(max(0, min(len(self.steps) - 1, self.idx + delta)))

    def _select_step(self, i):
        self.follow = False
        self._show(i)

    def _highlight(self):
        for k, row in enumerate(self.step_rows):
            row.configure(fg_color=PALETTE['surface_muted'] if k == self.idx else 'transparent')

    def _show(self, i):
        if not self.steps:
            return
        self.idx = max(0, min(len(self.steps) - 1, i))
        self._highlight()
        st = self.steps[self.idx]
        self.action_label.configure(text=f'Шаг {st["n"]}:  {st["line"] or "—"}')
        self.why_label.configure(text=st['why'])
        self._render_image()
        self._render_editor()
        self._shown = _signature([st])

    def _render_image(self, marker: Optional[tuple] = None):
        if not self.steps:
            return
        st = self.steps[self.idx]
        path = st['marked'] if self.show_marked and st['marked'].exists() else st['screen']
        if not path.exists():
            self.image_label.configure(image=None, text='нет изображения',
                                       font=self.f['body'], text_color=PALETTE['text_muted'])
            self._disp = None
            return
        img = Image.open(path).convert('RGB')
        scale = min(VIEW_W / img.width, VIEW_H / img.height)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        disp = img.resize(size)
        # Картинка marked уже с полями разметки (pad=48). action.json — в координатах
        # снимка без полей; метку рисуем только на «чистом экране».
        if marker and not self.show_marked:
            d = ImageDraw.Draw(disp)
            mx, my = marker[0] * scale, marker[1] * scale
            d.line([(mx - 12, my), (mx + 12, my)], fill='#d23b2e', width=2)
            d.line([(mx, my - 12), (mx, my + 12)], fill='#d23b2e', width=2)
        self._img = ctk.CTkImage(light_image=disp, size=size)
        self.image_label.configure(image=self._img, text='')
        # смещение картинки внутри CTkLabel (padx/pady=10 + центровка)
        self._disp = (scale, 10, 10, img.width, img.height)

    def _preview_marker(self, sx, sy):
        if not self.steps:
            return
        off = (self._edit or {}).get('offset', [0, 0])
        if self.show_marked:
            self.toggle.set('Чистый экран')
            self.show_marked = False
        self._render_image(marker=(sx - off[0], sy - off[1]))


def _signature(steps: List[dict]) -> tuple:
    """Отпечаток состояния шагов: меняется, только когда на диске появилось что-то новое."""
    return tuple((s['n'], s['line'], s['marked'].exists(), s['screen'].exists(),
                  json.dumps(s['action'], sort_keys=True, ensure_ascii=False) if s['action'] else '')
                 for s in steps)


def _num(text):
    try:
        return int(float(text))
    except (ValueError, TypeError):
        return None
