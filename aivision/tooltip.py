"""
Подсказки у курсора. Навёл на виджет (иконку-кнопку) — через паузу рядом с
курсором всплывает тёмная плашка с текстом; увёл курсор или нажал — исчезает.
Нужны, когда «всё в пиктограммах»: иконка + подсказка = понятно и современно.

    from .tooltip import tip
    tip(button, 'Записать сценарий')
"""
import tkinter as tk

from .ui_theme import PALETTE


class _Tip:
    def __init__(self, widget, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.win = None
        self._job = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _e=None):
        self._cancel()
        try:
            self._job = self.widget.after(self.delay, self._show)
        except Exception:
            pass

    def _show(self):
        if self.win or not self.text:
            return
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 20
            self.win = tk.Toplevel(self.widget)
            self.win.wm_overrideredirect(True)
            self.win.wm_geometry(f'+{x}+{y}')
            self.win.attributes('-topmost', True)
            tk.Label(self.win, text=self.text, bg=PALETTE['dark'], fg=PALETTE['dark_text'],
                     font=('Segoe UI', 9), padx=10, pady=5, bd=0).pack()
        except Exception:
            self.win = None

    def _hide(self, _e=None):
        self._cancel()
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _cancel(self):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


def tip(widget, text: str, delay: int = 450) -> _Tip:
    """Повесить подсказку у курсора на виджет."""
    return _Tip(widget, text, delay)
