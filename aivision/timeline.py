"""
Таймлайн прогона — плеер записи экрана в духе Clipchamp.

Сверху превью кадра, снизу дорожка: отрезки шагов (что делал агент), маркеры
начала каждого действия, линейка времени и бегунок. Play/пауза, перемотка
кликом по дорожке. Курсор на кадрах нарисован рекордером, поэтому видно и то,
что на экране, и что делает курсор.

Данные: runs/<время>/record.mp4 + record.json (из aivision.recorder).
"""

import json
import tkinter as tk
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk

try:
    import cv2
except ImportError:
    cv2 = None
from PIL import Image

from .ui_theme import PAD, PALETTE, RADIUS

PREVIEW_W, PREVIEW_H = 980, 540
TRACK_H = 104
SEG_COLORS = ['#dfe2e8', '#d5d9e0']       # отрезки действий — чередование серых
THINK_COLOR = '#f5f6f8'                    # отрезки «Siberi думает» — светлые


class TimelineView(ctk.CTkFrame):
    """Панель «Запись» — встраивается во вкладку лаунчера (раньше было отдельным окном)."""

    def __init__(self, master, scenario, f):
        super().__init__(master, fg_color=PALETTE['bg'], corner_radius=0)
        self.f = f

        run = Path(scenario.last_run) if scenario.last_run else None
        self.video = run / 'record.mp4' if run else None
        self.meta = self._load_meta(run)
        self.playing = False
        self.t = 0.0
        self._last_frame_idx = -1
        self._after_id = None

        if cv2 is None or not self.video or not self.video.exists() or not self.meta:
            self._empty()
            return
        self.cap = cv2.VideoCapture(str(self.video))
        self.fps = self.meta.get('fps', 10) or 10
        self.duration = self.meta.get('duration', 0) or (
            int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) / self.fps)
        self.steps: List[dict] = self.meta.get('steps', [])

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build()
        self._seek(0.0)

    def _load_meta(self, run: Optional[Path]) -> Optional[dict]:
        if not run:
            return None
        p = run / 'record.json'
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            return None

    def _empty(self):
        box = ctk.CTkFrame(self, fg_color='transparent')
        box.place(relx=0.5, rely=0.5, anchor='center')
        msg = 'Записи для этого прогона нет' if cv2 else 'Нужен пакет opencv-python для просмотра записи'
        ctk.CTkLabel(box, text=msg, font=self.f['h2'], text_color=PALETTE['text']).pack()
        ctk.CTkLabel(box, text='Включите «Запись экрана» в сценарии и запустите его.',
                     font=self.f['body'], text_color=PALETTE['text_muted']).pack(pady=6)

    # ── Вёрстка ──────────────────────────────────────────────────────────────
    def _build(self):
        prev = ctk.CTkFrame(self, fg_color=PALETTE['surface'], corner_radius=RADIUS,
                            border_width=1, border_color=PALETTE['border'])
        prev.grid(row=0, column=0, sticky='nsew', padx=PAD, pady=(PAD, 8))
        self.preview = ctk.CTkLabel(prev, text='')
        self.preview.pack(expand=True, padx=8, pady=8)

        ctrl = ctk.CTkFrame(self, fg_color='transparent')
        ctrl.grid(row=1, column=0, sticky='ew', padx=PAD)
        self.play_btn = ctk.CTkButton(ctrl, text='▶', width=48, height=40, font=self.f['h2'],
                                      corner_radius=RADIUS, fg_color=PALETTE['accent'],
                                      hover_color=PALETTE['accent_hover'],
                                      text_color=PALETTE['accent_text'], command=self._toggle)
        self.play_btn.pack(side='left')
        self.time_lbl = ctk.CTkLabel(ctrl, text='0:00 / 0:00', font=self.f['body_b'],
                                     text_color=PALETTE['text'])
        self.time_lbl.pack(side='left', padx=14)
        self.step_lbl = ctk.CTkLabel(ctrl, text='', font=self.f['body'],
                                     text_color=PALETTE['text_muted'])
        self.step_lbl.pack(side='left')

        track_wrap = ctk.CTkFrame(self, fg_color=PALETTE['surface'], corner_radius=RADIUS,
                                  border_width=1, border_color=PALETTE['border'])
        track_wrap.grid(row=2, column=0, sticky='ew', padx=PAD, pady=(8, PAD))
        self.canvas = tk.Canvas(track_wrap, height=TRACK_H, bg=PALETTE['surface'],
                                highlightthickness=0)
        self.canvas.pack(fill='x', padx=10, pady=10)
        self.canvas.bind('<Button-1>', self._on_track_click)
        self.canvas.bind('<B1-Motion>', self._on_track_click)
        self.canvas.bind('<Configure>', lambda _e: self._draw_track())

    # ── Дорожка ──────────────────────────────────────────────────────────────
    def _track_geom(self):
        w = max(self.canvas.winfo_width(), 200)
        return 8, w - 8, 26, TRACK_H - 20        # left, right, top(seg), bottom

    def _t_to_x(self, t):
        l, r, _, _ = self._track_geom()
        return l + (r - l) * (t / self.duration if self.duration else 0)

    def _x_to_t(self, x):
        l, r, _, _ = self._track_geom()
        return max(0.0, min(self.duration, (x - l) / (r - l) * self.duration)) if r > l else 0.0

    def _draw_track(self):
        c = self.canvas
        c.delete('all')
        l, r, top, bot = self._track_geom()
        bounds = sorted((s['t'], s.get('label', ''), s['n'], s.get('kind', 'act')) for s in self.steps)
        # отрезки: «Siberi думает» — светлые, действия — плотнее (как клипы на дорожке)
        for i, (t0, label, n, kind) in enumerate(bounds):
            t1 = bounds[i + 1][0] if i + 1 < len(bounds) else self.duration
            x0, x1 = self._t_to_x(t0), self._t_to_x(t1)
            think = kind == 'think'
            c.create_rectangle(x0, top, max(x1, x0 + 2), bot,
                               fill=THINK_COLOR if think else SEG_COLORS[n % 2],
                               outline=PALETTE['border'])
            if x1 - x0 > 46:
                text = f'{n} · думает…' if think else f'{n}. {label}'
                c.create_text(x0 + 6, (top + bot) / 2, anchor='w',
                              fill=PALETTE['text_muted'] if think else PALETTE['text'],
                              font=(self.f['small'][0], 10), text=text[:int((x1 - x0) / 7)])
        # линейка времени
        step = 1 if self.duration <= 20 else 5
        tt = 0
        while tt <= self.duration:
            x = self._t_to_x(tt)
            c.create_line(x, bot, x, bot + 6, fill=PALETTE['text_muted'])
            c.create_text(x, bot + 8, anchor='n', fill=PALETTE['text_muted'],
                          font=(self.f['small'][0], 9), text=_fmt(tt))
            tt += step
        # маркеры начала действий
        for t0, _label, _n, kind in bounds:
            if kind == 'think':
                continue
            x = self._t_to_x(t0)
            c.create_polygon(x - 5, top - 10, x + 5, top - 10, x, top - 1,
                             fill=PALETTE['accent'], outline='')
        # бегунок
        px = self._t_to_x(self.t)
        c.create_line(px, top - 12, px, bot + 4, fill=PALETTE['danger'], width=2)
        c.create_oval(px - 4, top - 16, px + 4, top - 8, fill=PALETTE['danger'], outline='')

    def _on_track_click(self, event):
        self._seek(self._x_to_t(event.x))

    # ── Воспроизведение ──────────────────────────────────────────────────────
    def _toggle(self):
        self.playing = not self.playing
        self.play_btn.configure(text='⏸' if self.playing else '▶')
        if self.playing:
            if self.t >= self.duration - 0.05:
                self.t = 0.0
            self._last_tick = _now()
            self._tick()

    def _tick(self):
        if not self.playing or not self.winfo_exists():
            return
        now = _now()
        self.t = min(self.duration, self.t + (now - self._last_tick))
        self._last_tick = now
        self._render()
        if self.t >= self.duration - 1e-3:
            self.playing = False
            self.play_btn.configure(text='▶')
            return
        self._after_id = self.after(int(1000 / self.fps), self._tick)

    def _seek(self, t):
        self.t = max(0.0, min(self.duration, t))
        self._render()

    def _render(self):
        idx = int(round(self.t * self.fps))
        if idx != self._last_frame_idx:
            if idx != self._last_frame_idx + 1:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = self.cap.read()
            self._last_frame_idx = idx
            if ok:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                scale = min(PREVIEW_W / img.width, PREVIEW_H / img.height)
                size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
                self._img = ctk.CTkImage(light_image=img, size=size)
                self.preview.configure(image=self._img, text='')
        self.time_lbl.configure(text=f'{_fmt(self.t)} / {_fmt(self.duration)}')
        cur = sorted((s for s in self.steps if s['t'] <= self.t + 1e-6), key=lambda s: s['t'])
        if not cur:
            text = 'подготовка'
        elif cur[-1].get('kind') == 'think':
            text = f'Шаг {cur[-1]["n"]} · Siberi думает…'
        else:
            text = f'Шаг {cur[-1]["n"]}: {cur[-1]["label"]}'
        self.step_lbl.configure(text=text)
        self._draw_track()

    def destroy(self):
        self.playing = False
        if getattr(self, 'cap', None) is not None:
            self.cap.release()
        super().destroy()


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    return f'{sec // 60}:{sec % 60:02d}'


def _now():
    import time
    return time.perf_counter()
