"""
Своя строка заголовка лаунчера: окно без системной рамки, свои кнопки
«Свернуть» и «Крестик» в стиле темы, перетаскивание за шапку, двойной клик по
шапке — развернуть/вернуть. Кнопка в панели задач и сворачивание держатся на
Win32 (WS_EX_APPWINDOW + ShowWindow). Если убрать системную рамку не удалось —
тихо остаёмся с обычным заголовком, кнопки всё равно работают.

    from .titlebar import TitleBar
    self._titlebar = TitleBar(self)     # создаёт шапку в row=0, columnspan=2
"""
import sys

import customtkinter as ctk

from .tooltip import tip
from .ui_theme import PALETTE


class TitleBar:
    def __init__(self, app):
        self.app = app
        self._hwnd = None
        self._maxed = False
        self._restore_geom = None
        self.borderless = self._make_borderless()
        self._build()
        if self.borderless:
            self.app.bind('<Map>', self._on_map, add='+')

    # ── Оконная обвязка ──────────────────────────────────────────────────
    def _make_borderless(self) -> bool:
        try:
            self.app.overrideredirect(True)
            self.app.update_idletasks()
            if sys.platform == 'win32':
                self._win32_taskbar_button()
            return True
        except Exception:
            try:
                self.app.overrideredirect(False)
            except Exception:
                pass
            return False

    def _win32_taskbar_button(self):
        """Вернуть окну кнопку в панели задач (у borderless её по умолчанию нет)."""
        from ctypes import windll
        GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x00040000, 0x00000080
        hwnd = windll.user32.GetParent(self.app.winfo_id())
        self._hwnd = hwnd
        ex = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex = (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        self.app.withdraw()                                   # перерисовать таскбар
        self.app.after(10, self.app.deiconify)

    def _on_map(self, e=None):
        """Вернулись из панели задач — снова прячем системную рамку."""
        if e is not None and getattr(e, 'widget', None) is not self.app:
            return
        try:
            self.app.overrideredirect(True)
        except Exception:
            pass

    # ── Действия кнопок ──────────────────────────────────────────────────
    def minimize(self):
        try:
            if self._hwnd is not None:
                from ctypes import windll
                windll.user32.ShowWindow(self._hwnd, 6)        # SW_MINIMIZE
            else:
                self.app.iconify()
        except Exception:
            try:
                self.app.iconify()
            except Exception:
                pass

    def toggle_max(self, _e=None):
        if not self.borderless:
            return
        if self._maxed:
            if self._restore_geom:
                self.app.geometry(self._restore_geom)
            self._maxed = False
        else:
            self._restore_geom = self.app.geometry()
            x, y, w, h = self._work_area()
            self.app.geometry(f'{w}x{h}+{x}+{y}')
            self._maxed = True

    def _work_area(self):
        """Рабочая область экрана без панели задач; иначе — весь экран."""
        try:
            if sys.platform == 'win32':
                from ctypes import byref, windll, wintypes
                rect = wintypes.RECT()
                windll.user32.SystemParametersInfoW(0x0030, 0, byref(rect), 0)   # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
        return 0, 0, self.app.winfo_screenwidth(), self.app.winfo_screenheight()

    # ── Разметка шапки ───────────────────────────────────────────────────
    def _build(self):
        f = self.app.f
        bar = ctk.CTkFrame(self.app, height=46, corner_radius=0, fg_color=PALETTE['surface'])
        bar.grid(row=0, column=0, columnspan=2, sticky='ew')
        bar.grid_propagate(False)
        bar.grid_rowconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkLabel(bar, text='  ✦  AI Vision', font=f['body_b'], text_color=PALETTE['text'])
        logo.grid(row=0, column=0, sticky='nsw', padx=(14, 0))
        grip = ctk.CTkLabel(bar, text='', fg_color='transparent')
        grip.grid(row=0, column=1, sticky='nsew')

        btns = ctk.CTkFrame(bar, fg_color='transparent')
        btns.grid(row=0, column=2, sticky='e', padx=(0, 6))
        b_min = ctk.CTkButton(btns, text='—', width=42, height=30, corner_radius=8,
                              font=(f['body'][0], 15), fg_color='transparent',
                              hover_color=PALETTE['surface_muted'], text_color=PALETTE['text_muted'],
                              command=self.minimize)
        b_min.grid(row=0, column=0, padx=2)
        b_close = ctk.CTkButton(btns, text='✕', width=42, height=30, corner_radius=8,
                                font=(f['body'][0], 14), fg_color='transparent',
                                hover_color=PALETTE['danger'], text_color=PALETTE['text_muted'],
                                command=self.app._on_close)
        b_close.grid(row=0, column=1, padx=2)
        tip(b_min, 'Свернуть в панель задач')
        tip(b_close, 'Закрыть — лаунчер уедет в трей')

        line = ctk.CTkFrame(bar, height=1, corner_radius=0, fg_color=PALETTE['border'])
        line.grid(row=1, column=0, columnspan=3, sticky='ew')

        for w in (logo, grip):
            w.bind('<Button-1>', self._drag_start, add='+')
            w.bind('<B1-Motion>', self._drag, add='+')
            w.bind('<Double-Button-1>', self.toggle_max, add='+')
        self.bar = bar

    def _drag_start(self, e):
        self._ox = e.x_root - self.app.winfo_x()
        self._oy = e.y_root - self.app.winfo_y()

    def _drag(self, e):
        if self._maxed:
            return
        self.app.geometry(f'+{e.x_root - self._ox}+{e.y_root - self._oy}')
