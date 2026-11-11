"""
Лаунчер AI Vision — настройка и запуск сценариев, всё в одном окне.

Слева — список сценариев и браузер по умолчанию. Справа — вкладки выбранного
сценария: «Редактор» (цель-промт, подсказки, браузер, параметры), «Схема»
(пошаговый просмотр и ручная правка последнего прогона) и «Запись» (видео-
таймлайн). «Схема» и «Запись» раньше открывались отдельными окнами — теперь
это вкладки, чтобы не плодить окна.

Запуск:  python -m aivision.launcher
"""

import ctypes
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from . import browsers as browsers_mod
from .scenarios import Scenario, Store
from .tooltip import tip
from .ui_theme import PAD, PALETTE, RADIUS, fonts

from .paths import RUNS
DEFAULT_LABEL = '— по умолчанию —'


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode('light')
        self.title('AI Vision — Лаунчер')
        self.geometry('1540x880')
        self.minsize(1120, 680)
        self.configure(fg_color=PALETTE['bg'])

        self.f = fonts()
        self.store = Store()
        self.browsers = browsers_mod.list_browsers()
        self.current: Scenario | None = None
        self.runs: dict[str, str] = {}           # id сценария → статус активного прогона
        self.queue: list[str] = []               # id сценариев, ждущих своей очереди
        self._row_widgets: dict[str, ctk.CTkFrame] = {}

        self.grid_columnconfigure(0, weight=0, minsize=300)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)      # строка заголовка
        self.grid_rowconfigure(1, weight=1)      # контент
        from .titlebar import TitleBar
        self._titlebar = TitleBar(self)
        self._build_sidebar()
        self._build_editor()
        self._refresh_list()
        if self.store.scenarios:
            self._select(self.store.scenarios[0])
        else:
            self._show_placeholder()

        self.protocol('WM_DELETE_WINDOW', self._on_close)      # крестик — свернуть в трей
        from . import tray as tray_mod
        self._tray = tray_mod.Tray(self).start()
        from . import services                         # Retina и локальная модель — сами, фоном
        services.start_background(lambda st: self.after(0, lambda: self._services_ready(st)))

    def _services_ready(self, st: dict):
        from . import services
        status = getattr(self, 'status', None)
        if status is not None and status.winfo_exists():
            ok = st['retina'] in ('ok', 'external')
            status.configure(text=services.describe(st), text_color=PALETTE['ok'] if ok else PALETTE['text_muted'])

    def _on_close(self):
        """Крестик окна: не выходим, а прячемся в трей (иконка внизу справа)."""
        self.withdraw()
        try:
            self._set_status('Свёрнут в трей — иконка внизу справа: «Показать» / «Выход».', PALETTE['text_muted'])
        except Exception:
            pass

    def _real_quit(self):
        """Настоящий выход — из меню трея «Выход»."""
        try:
            if getattr(self, '_tray', None):
                self._tray.stop()
        except Exception:
            pass
        self.destroy()

    # ── Сайдбар ────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, fg_color=PALETTE['surface'], corner_radius=0,
                           border_width=0)
        bar.grid(row=1, column=0, sticky='nsew')
        bar.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(bar, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=PAD, pady=(PAD + 4, 8))
        ctk.CTkLabel(head, text='AI Vision', font=self.f['title'],
                     text_color=PALETTE['text']).pack(anchor='w')
        ctk.CTkLabel(head, text='Сценарии управления экраном', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w')

        top = ctk.CTkFrame(bar, fg_color='transparent')
        top.grid(row=1, column=0, sticky='ew', padx=PAD, pady=(4, 8))
        top.grid_columnconfigure(0, weight=1)
        b_new = ctk.CTkButton(top, text='+  Новый сценарий', font=self.f['body_b'],
                              height=38, corner_radius=RADIUS, command=self._new_scenario,
                              fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                              text_color=PALETTE['accent_text'])
        b_new.grid(row=0, column=0, sticky='ew')
        b_team = ctk.CTkButton(top, text='🏢 Команда', font=self.f['body_b'], height=38, width=120,
                               corner_radius=RADIUS, command=self._show_team,
                               fg_color=PALETTE['surface_muted'], hover_color=PALETTE['border'],
                               text_color=PALETTE['text'])
        b_team.grid(row=0, column=1, padx=(8, 0))
        b_rec = ctk.CTkButton(top, text='🔴 Записать сценарий (показать как надо)', font=self.f['small'],
                              height=32, corner_radius=RADIUS, command=self._record_scenario,
                              fg_color=PALETTE['surface_muted'], hover_color=PALETTE['border'],
                              text_color=PALETTE['text'])
        b_rec.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        tip(b_new, 'Создать новый сценарий')
        tip(b_team, 'Команда ИИ-агентов: задачи, ресурсы, проекты')
        tip(b_rec, 'Показать сценарий руками — запишется как образец')

        self.list_frame = ctk.CTkScrollableFrame(bar, fg_color='transparent')
        self.list_frame.grid(row=2, column=0, sticky='nsew', padx=8)
        bar.grid_columnconfigure(0, weight=1)

        foot = ctk.CTkFrame(bar, fg_color=PALETTE['surface_muted'], corner_radius=RADIUS)
        foot.grid(row=3, column=0, sticky='ew', padx=PAD, pady=PAD)
        ctk.CTkLabel(foot, text='Браузер по умолчанию', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=12, pady=(10, 2))
        names = [b.name for b in self.browsers] or ['браузеры не найдены']
        self.default_browser = ctk.CTkOptionMenu(
            foot, values=names, font=self.f['body'], command=self._set_default_browser,
            fg_color=PALETTE['field'], button_color=PALETTE['border'],
            button_hover_color=PALETTE['border'], text_color=PALETTE['text'],
            dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
        self.default_browser.pack(fill='x', padx=12, pady=(0, 12))
        cur = self.store.default_browser or (self.browsers[0].name if self.browsers else '')
        if cur in names:
            self.default_browser.set(cur)

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._row_widgets.clear()
        for s in self.store.scenarios:
            self._add_row(s)

    def _add_row(self, s: Scenario):
        active = self.current and self.current.id == s.id
        row = ctk.CTkFrame(self.list_frame, corner_radius=RADIUS,
                           fg_color=PALETTE['surface_muted'] if active else 'transparent')
        row.pack(fill='x', pady=3)
        inner = ctk.CTkFrame(row, fg_color='transparent')
        inner.pack(fill='x', padx=12, pady=9)
        self.name_lbl = ctk.CTkLabel(inner, text=s.name or 'Без названия', font=self.f['body_b'],
                                     text_color=PALETTE['text'], anchor='w')
        self.name_lbl.pack(fill='x')
        running = s.id in self.runs
        queued = s.id in self.queue
        if running:
            sub, color = '● идёт…', PALETTE['ok']
        elif queued:
            sub, color = f'⏳ в очереди ({self.queue.index(s.id) + 1})', PALETTE['text']
        else:
            sub, color = s.browser or 'браузер по умолчанию', PALETTE['text_muted']
        ctk.CTkLabel(inner, text=f'{sub} · {s.max_steps} шагов', font=self.f['small'],
                     text_color=color, anchor='w').pack(fill='x')
        for w in (row, inner, *inner.winfo_children()):
            w.bind('<Button-1>', lambda _e, sc=s: self._select(sc))
            w.bind('<Double-Button-1>', lambda _e, sc=s: self._rename_inline(sc))
            w.bind('<Enter>', lambda _e, r=row: self._row_hover(r, True))
            w.bind('<Leave>', lambda _e, r=row: self._row_hover(r, False))
            try:
                w.configure(cursor='hand2')
            except Exception:
                pass
        if active:
            self._row_actions(row, s)
        self._row_widgets[s.id] = row

    def _row_hover(self, row, on: bool):
        """Плавная подсветка строки под курсором (кроме выбранной)."""
        if not row.winfo_exists():
            return
        active = self.current and self._row_widgets.get(self.current.id) is row
        if active:
            return
        row.configure(fg_color=PALETTE['surface_muted'] if on else 'transparent')

    def _row_actions(self, row, s: Scenario):
        """Кнопки управления на выбранной строке: вверх/вниз, дублировать, удалить."""
        bar = ctk.CTkFrame(row, fg_color='transparent')
        bar.pack(fill='x', padx=8, pady=(0, 8))
        buttons = [('↑', lambda: self._reorder(s, -1), PALETTE['text_muted'], 'Выше в списке'),
                   ('↓', lambda: self._reorder(s, 1), PALETTE['text_muted'], 'Ниже в списке'),
                   ('⧉', lambda: self._duplicate(s), PALETTE['text_muted'], 'Дублировать сценарий'),
                   ('🗑', lambda: self._delete(s), PALETTE['danger'], 'Удалить сценарий')]
        if s.id in self.queue:
            buttons.append(('✕ из очереди', lambda: self._unqueue(s), PALETTE['danger'], 'Убрать из очереди'))
        for txt, cmd, color, hint in buttons:
            b = ctk.CTkButton(bar, text=txt, width=30 if len(txt) < 3 else 90, height=26,
                              font=self.f['small'], corner_radius=8, fg_color=PALETTE['surface'],
                              text_color=color, hover_color=PALETTE['border'], command=cmd)
            b.pack(side='left', padx=2)
            tip(b, hint)

    def _rename_inline(self, s: Scenario):
        """Двойной клик по строке — переименование прямо в сайдбаре."""
        self._select(s)
        row = self._row_widgets.get(s.id)
        if not row:
            return
        entry = ctk.CTkEntry(row, font=self.f['body_b'], height=30, corner_radius=8,
                             fg_color=PALETTE['field'], border_color=PALETTE['accent'],
                             text_color=PALETTE['text'])
        entry.insert(0, s.name)
        entry.pack(fill='x', padx=10, pady=(8, 2))
        entry.focus_set()
        entry.select_range(0, 'end')

        def commit(_e=None):
            s.name = entry.get().strip() or 'Без названия'
            self.store.update(s)
            if self.current and self.current.id == s.id:
                self.name_entry_set(s.name)
            self._refresh_list()
        entry.bind('<Return>', commit)
        entry.bind('<Escape>', lambda _e: self._refresh_list())
        entry.bind('<FocusOut>', commit)

    def name_entry_set(self, text: str):
        if getattr(self, 'name_entry', None) and self.name_entry.winfo_exists():
            self.name_entry.delete(0, 'end')
            self.name_entry.insert(0, text)

    def _reorder(self, s: Scenario, delta: int):
        self._apply_and_save()
        if self.store.move(s.id, delta):
            self._refresh_list()

    def _unqueue(self, s: Scenario):
        if s.id in self.queue:
            self.queue.remove(s.id)
            self._refresh_list()

    def _borrow_hints(self, name: str, others):
        """Дописать подсказки выбранного сценария к текущим (без дублей)."""
        src = next((o for o in others if o.name == name), None)
        if not src:
            return
        have = {ln.strip() for ln in self.hints_box.get('1.0', 'end').splitlines() if ln.strip()}
        add = [h for h in src.hints if h not in have]
        if add:
            cur = self.hints_box.get('1.0', 'end').rstrip()
            self.hints_box.delete('1.0', 'end')
            self.hints_box.insert('1.0', ('\n'.join([cur] + add)).strip() + '\n')

    # ── Вкладки (Редактор / Схема / Запись) ──────────────────────────────────
    # Свой переключатель вместо CTkTabview: он сжимает вкладку до содержимого,
    # и «Схема» с grid-колонками схлопывается в 1×1. Здесь все панели лежат в
    # одной ячейке с весом, а активная поднимается наверх.
    def _build_editor(self):
        right = ctk.CTkFrame(self, fg_color='transparent')
        right.grid(row=1, column=1, sticky='nsew', padx=(0, PAD), pady=PAD)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        self._team_view = None

        self.tabbar = ctk.CTkSegmentedButton(
            right, values=['Редактор', 'Схема', 'Запись'], font=self.f['body_b'],
            command=self._switch_tab, fg_color=PALETTE['surface_muted'],
            selected_color=PALETTE['surface'], selected_hover_color=PALETTE['border'],
            unselected_color=PALETTE['surface_muted'], unselected_hover_color=PALETTE['border'],
            text_color=PALETTE['text'])
        self.tabbar.set('Редактор')
        self.tabbar.grid(row=0, column=0, sticky='w', pady=(0, 10))

        self.content = ctk.CTkFrame(right, fg_color='transparent')
        self.content.grid(row=1, column=0, sticky='nsew')
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.editor = ctk.CTkScrollableFrame(self.content, fg_color='transparent')
        self.editor.grid(row=0, column=0, sticky='nsew')
        self._tab = 'Редактор'
        self._scheme_view = None
        self._scheme_sid = None
        self._timeline_view = None
        self._timeline_sid = None

    def _switch_tab(self, name: str):
        self._tab = name
        self.tabbar.grid()                 # вернуть вкладки сценария, если были скрыты «Командой»
        if self._team_view is not None:
            self._team_view.grid_remove()
        if name == 'Схема':
            self._mount_scheme()
        elif name == 'Запись':
            self._mount_timeline()
        self._raise_tab()

    def _show_team(self):
        from .team_view import TeamView
        if self._team_view is None:
            self._team_view = TeamView(self.content, self.f)
        else:
            self._team_view.refresh_org()
        self._tab = 'Команда'
        self.tabbar.grid_remove()          # «Команда» — не про конкретный сценарий, прячем вкладки
        for v in (self.editor, self._scheme_view, self._timeline_view):
            if v is not None:
                v.grid_remove()
        self._team_view.grid(row=0, column=0, sticky='nsew')
        self._team_view.tkraise()

    def _raise_tab(self):
        for view, name in ((self.editor, 'Редактор'), (self._scheme_view, 'Схема'),
                           (self._timeline_view, 'Запись')):
            if view is None:
                continue
            if name == self._tab:
                view.grid(row=0, column=0, sticky='nsew')
                view.tkraise()
            else:
                view.grid_remove()

    def _goto_tab(self, name: str):
        self.tabbar.set(name)
        self._switch_tab(name)

    def _drop_views(self):
        """Панели прошлого сценария больше не актуальны — освобождаем (в т.ч. видео)."""
        for attr in ('_scheme_view', '_timeline_view'):
            v = getattr(self, attr)
            if v is not None:
                v.destroy()
                setattr(self, attr, None)
        self._scheme_sid = self._timeline_sid = None

    def _mount_scheme(self, live: bool = False):
        if not self.current:
            return
        if self._scheme_view is not None and not live and self._scheme_sid == self.current.id:
            return
        if self._scheme_view is not None:
            self._scheme_view.destroy()
        from .scheme import SchemeView
        self._scheme_view = SchemeView(self.content, self.current, self.f, live=live)
        self._scheme_sid = self.current.id

    def _mount_timeline(self):
        if not self.current:
            return
        if self._timeline_view is not None and self._timeline_sid == self.current.id:
            return
        if self._timeline_view is not None:
            self._timeline_view.destroy()
        from .timeline import TimelineView
        self._timeline_view = TimelineView(self.content, self.current, self.f)
        self._timeline_sid = self.current.id

    def _show_placeholder(self):
        for w in self.editor.winfo_children():
            w.destroy()
        box = ctk.CTkFrame(self.editor, fg_color='transparent')
        box.pack(expand=True, pady=140)
        ctk.CTkLabel(box, text='Нет выбранного сценария', font=self.f['h2'],
                     text_color=PALETTE['text']).pack()
        ctk.CTkLabel(box, text='Создайте сценарий слева, чтобы начать.',
                     font=self.f['body'], text_color=PALETTE['text_muted']).pack(pady=4)

    def _card(self, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.editor, fg_color=PALETTE['surface'],
                            corner_radius=RADIUS, border_width=1,
                            border_color=PALETTE['border'])
        card.pack(fill='x', pady=(0, 12))
        if title:
            ctk.CTkLabel(card, text=title, font=self.f['small'],
                         text_color=PALETTE['text_muted']).pack(anchor='w', padx=14, pady=(12, 0))
        return card

    def _entry(self, parent, value: str) -> ctk.CTkEntry:
        e = ctk.CTkEntry(parent, font=self.f['body'], height=36, corner_radius=10,
                         fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                         text_color=PALETTE['text'])
        e.insert(0, value)
        return e

    def _select(self, s: Scenario):
        self.current = s
        self._refresh_list()
        self._render_editor(s)
        self._drop_views()                 # «Схема»/«Запись» были про прошлый сценарий
        if self._tab == 'Команда':         # выбрали сценарий — уходим из «Команды» в редактор
            self.tabbar.set('Редактор')
            self._switch_tab('Редактор')
        elif self._tab != 'Редактор':
            self._switch_tab(self._tab)

    def _render_editor(self, s: Scenario):
        for w in self.editor.winfo_children():
            w.destroy()

        header = ctk.CTkFrame(self.editor, fg_color='transparent')
        header.pack(fill='x', pady=(0, 12))
        self.name_entry = ctk.CTkEntry(
            header, font=self.f['title'], height=44, corner_radius=10,
            fg_color=PALETTE['surface'], border_color=PALETTE['border'],
            text_color=PALETTE['text'])
        self.name_entry.insert(0, s.name)
        self.name_entry.pack(side='left', fill='x', expand=True)
        self.name_entry.bind('<FocusOut>', lambda _e: self._apply_and_save())

        # Цель (промт)
        goal_card = self._card('Цель — что должен сделать агент (обычный промт)')
        self.goal_box = ctk.CTkTextbox(goal_card, font=self.f['body'], height=90,
                                       corner_radius=10, fg_color=PALETTE['field'],
                                       border_color=PALETTE['field_border'], border_width=1,
                                       text_color=PALETTE['text'], wrap='word')
        self.goal_box.insert('1.0', s.goal)
        self.goal_box.pack(fill='x', padx=14, pady=(6, 14))

        # Подсказки
        hint_card = self._card('Подсказки — как это делается, человеческим языком')
        self.hints_box = ctk.CTkTextbox(hint_card, font=self.f['body'], height=70,
                                        corner_radius=10, fg_color=PALETTE['field'],
                                        border_color=PALETTE['field_border'], border_width=1,
                                        text_color=PALETTE['text'], wrap='word')
        self.hints_box.insert('1.0', '\n'.join(s.hints))
        self.hints_box.pack(fill='x', padx=14, pady=(6, 2))
        foot = ctk.CTkFrame(hint_card, fg_color='transparent')
        foot.pack(fill='x', padx=14, pady=(0, 12))
        ctk.CTkLabel(foot, text='по одной подсказке на строку', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(side='left')
        # Перенести кусочек из другого сценария — его подсказки дописываются сюда
        others = [o for o in self.store.scenarios if o.id != s.id and o.hints]
        if others:
            menu = ctk.CTkOptionMenu(
                foot, values=['＋ взять из…'] + [o.name for o in others], font=self.f['small'],
                width=150, height=26, command=lambda name: self._borrow_hints(name, others),
                fg_color=PALETTE['surface_muted'], button_color=PALETTE['border'],
                button_hover_color=PALETTE['border'], text_color=PALETTE['text_muted'],
                dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
            menu.set('＋ взять из…')
            menu.pack(side='right')

        # Браузер и URL
        bcard = self._card('Браузер и старт')
        brow = ctk.CTkFrame(bcard, fg_color='transparent')
        brow.pack(fill='x', padx=14, pady=(8, 14))
        brow.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(brow, text='Браузер', font=self.f['body'],
                     text_color=PALETTE['text']).grid(row=0, column=0, sticky='w')
        names = [DEFAULT_LABEL] + [b.name for b in self.browsers]
        self.browser_menu = ctk.CTkOptionMenu(
            brow, values=names, font=self.f['body'], width=220,
            fg_color=PALETTE['field'], button_color=PALETTE['border'],
            button_hover_color=PALETTE['border'], text_color=PALETTE['text'],
            dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
        self.browser_menu.set(s.browser if s.browser in names else DEFAULT_LABEL)
        self.browser_menu.grid(row=0, column=1, sticky='e')
        ctk.CTkLabel(brow, text='Стартовый URL', font=self.f['body'],
                     text_color=PALETTE['text']).grid(row=1, column=0, sticky='w', pady=(12, 0))
        self.url_entry = self._entry(brow, s.start_url)
        self.url_entry.grid(row=1, column=1, sticky='ew', padx=(12, 0), pady=(12, 0))
        self.incognito_val = ctk.BooleanVar(value=s.incognito)
        ctk.CTkCheckBox(brow, text='Приватное окно (без закладок и истории в снимках)',
                        variable=self.incognito_val, font=self.f['body'],
                        text_color=PALETTE['text'], fg_color=PALETTE['accent'],
                        hover_color=PALETTE['accent_hover'],
                        border_color=PALETTE['field_border']).grid(
            row=2, column=1, sticky='w', padx=(12, 0), pady=(10, 0))

        # Параметры движения
        self._render_params(s)

        # Кнопки действий
        actions = ctk.CTkFrame(self.editor, fg_color='transparent')
        actions.pack(fill='x', pady=(4, 24))
        self.run_btn = ctk.CTkButton(
            actions, text='▶  Запустить', font=self.f['body_b'], height=44, width=180,
            corner_radius=RADIUS, fg_color=PALETTE['accent'],
            hover_color=PALETTE['accent_hover'], text_color=PALETTE['accent_text'],
            command=self._run)
        self.run_btn.pack(side='left')
        tip(self.run_btn, 'Запустить сценарий сейчас (или поставить в очередь, если один уже идёт)')
        b_dup = ctk.CTkButton(actions, text='Дублировать', font=self.f['body'], height=44, width=120,
                              corner_radius=RADIUS, fg_color='transparent',
                              hover_color=PALETTE['surface_muted'], text_color=PALETTE['text_muted'],
                              command=self._duplicate)
        b_dup.pack(side='left', padx=(0, 8))
        tip(b_dup, 'Создать копию этого сценария')
        b_del = ctk.CTkButton(actions, text='Удалить', font=self.f['body'], height=44, width=110,
                              corner_radius=RADIUS, fg_color='transparent',
                              hover_color=PALETTE['danger_soft'], text_color=PALETTE['danger'],
                              command=self._delete)
        b_del.pack(side='right')
        tip(b_del, 'Удалить этот сценарий безвозвратно')

        self.status = ctk.CTkLabel(self.editor, text='', font=self.f['small'],
                                   text_color=PALETTE['text_muted'])
        self.status.pack(anchor='w', pady=(0, 20))

    def _render_params(self, s: Scenario):
        card = self._card('Параметры запуска')
        grid = ctk.CTkFrame(card, fg_color='transparent')
        grid.pack(fill='x', padx=14, pady=(8, 14))
        grid.grid_columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(grid, text='Макс. шагов', font=self.f['body'],
                     text_color=PALETTE['text']).grid(row=0, column=0, sticky='w', pady=6)
        self.steps_entry = self._entry(grid, str(s.max_steps))
        self.steps_entry.configure(width=80)
        self.steps_entry.grid(row=0, column=1, sticky='w', padx=(12, 24))

        ctk.CTkLabel(grid, text='Монитор', font=self.f['body'],
                     text_color=PALETTE['text']).grid(row=0, column=2, sticky='w', pady=6)
        self.monitor_names = self._monitor_names()
        self.monitor_menu = ctk.CTkOptionMenu(
            grid, values=self.monitor_names, font=self.f['body'], width=170,
            fg_color=PALETTE['field'], button_color=PALETTE['border'],
            button_hover_color=PALETTE['border'], text_color=PALETTE['text'],
            dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
        idx = s.monitor if 0 <= s.monitor < len(self.monitor_names) else 0
        self.monitor_menu.set(self.monitor_names[idx])
        self.monitor_menu.grid(row=0, column=3, sticky='w', padx=12)

        self.speed_val = ctk.DoubleVar(value=s.speed)
        self._slider_row(grid, 1, 'Скорость', self.speed_val, 0.5, 2.0,
                         lambda v: f'{v:.1f}×')
        self.noise_val = ctk.DoubleVar(value=s.noise)
        self._slider_row(grid, 2, 'Шум траектории', self.noise_val, 0.0, 3.0,
                         lambda v: 'ровно' if v < 0.05 else f'{v:.1f}×')

        checks = ctk.CTkFrame(card, fg_color='transparent')
        checks.pack(fill='x', padx=14, pady=(0, 12))
        self.refine_val = ctk.BooleanVar(value=s.refine)
        ctk.CTkCheckBox(checks, text='Уточнять иконки (zoom)', variable=self.refine_val,
                        font=self.f['body'], text_color=PALETTE['text'],
                        fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                        border_color=PALETTE['field_border']).pack(side='left', padx=(0, 20))
        self.learn_val = ctk.BooleanVar(value=s.learn)
        ctk.CTkCheckBox(checks, text='Учиться на опыте', variable=self.learn_val,
                        font=self.f['body'], text_color=PALETTE['text'],
                        fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                        border_color=PALETTE['field_border']).pack(side='left', padx=(0, 20))
        self.record_val = ctk.BooleanVar(value=s.record)
        ctk.CTkCheckBox(checks, text='Запись экрана', variable=self.record_val,
                        font=self.f['body'], text_color=PALETTE['text'],
                        fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                        border_color=PALETTE['field_border']).pack(side='left', padx=(0, 20))
        self.replay_val = ctk.BooleanVar(value=s.fast_replay)
        ctk.CTkCheckBox(checks, text='Быстрый путь (рецепт)', variable=self.replay_val,
                        font=self.f['body'], text_color=PALETTE['text'],
                        fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                        border_color=PALETTE['field_border']).pack(side='left')

        # Статус рецепта + сброс
        from . import recipe as recipe_mod
        rp = recipe_mod.load(s.id)
        rrow = ctk.CTkFrame(card, fg_color='transparent')
        rrow.pack(fill='x', padx=14, pady=(0, 12))
        if rp:
            ctk.CTkLabel(rrow, text=f'📝 Рецепт записан: {len(rp.steps)} шагов — похожий промт пойдёт быстрым путём',
                         font=self.f['small'], text_color=PALETTE['text_muted']).pack(side='left')
            ctk.CTkButton(rrow, text='Забыть рецепт', font=self.f['small'], height=26, width=120,
                          corner_radius=8, fg_color='transparent', text_color=PALETTE['danger'],
                          hover_color=PALETTE['danger_soft'],
                          command=lambda: self._forget_recipe(s)).pack(side='right')
        else:
            ctk.CTkLabel(rrow, text='Рецепта пока нет — после первого удачного прогона он запишется сам',
                         font=self.f['small'], text_color=PALETTE['text_muted']).pack(side='left')

    def _forget_recipe(self, s: Scenario):
        from . import recipe as recipe_mod
        recipe_mod.delete(s.id)
        self._set_status('Рецепт забыт — следующий прогон снова разберёт по шагам.', PALETTE['text_muted'])
        self._render_editor(s)

    @staticmethod
    def _monitor_names():
        """'Авто' + мониторы в нумерации mss (индекс в списке = номер монитора)."""
        from .capture import monitors
        names = ['Авто']
        for i, m in enumerate(monitors()[1:], 1):
            main = ' · основной' if m.get('is_primary') else ''
            names.append(f"{i}: {m['width']}×{m['height']}{main}")
        return names

    def _slider_row(self, grid, row, label, var, lo, hi, fmt):
        ctk.CTkLabel(grid, text=label, font=self.f['body'],
                     text_color=PALETTE['text']).grid(row=row, column=0, sticky='w', pady=8)
        val_lbl = ctk.CTkLabel(grid, text=fmt(var.get()), font=self.f['body_b'],
                               text_color=PALETTE['text'], width=56)
        val_lbl.grid(row=row, column=3, sticky='e', padx=12)
        sl = ctk.CTkSlider(grid, from_=lo, to=hi, variable=var,
                           progress_color=PALETTE['accent'], button_color=PALETTE['accent'],
                           button_hover_color=PALETTE['accent_hover'], fg_color=PALETTE['track'],
                           command=lambda v: val_lbl.configure(text=fmt(float(v))))
        sl.grid(row=row, column=1, columnspan=2, sticky='ew', padx=12)

    # ── Сбор формы → объект ──────────────────────────────────────────────────
    def _collect(self, s: Scenario):
        s.name = self.name_entry.get().strip() or 'Без названия'
        s.goal = self.goal_box.get('1.0', 'end').strip()
        s.hints = [ln.strip() for ln in self.hints_box.get('1.0', 'end').splitlines() if ln.strip()]
        choice = self.browser_menu.get()
        s.browser = '' if choice == DEFAULT_LABEL else choice
        s.start_url = self.url_entry.get().strip()
        s.incognito = self.incognito_val.get()
        s.max_steps = _int(self.steps_entry.get(), s.max_steps, 1, 60)
        choice = self.monitor_menu.get()
        s.monitor = self.monitor_names.index(choice) if choice in self.monitor_names else 0
        s.speed = round(self.speed_val.get(), 2)
        s.noise = round(self.noise_val.get(), 2)
        s.refine = self.refine_val.get()
        s.learn = self.learn_val.get()
        s.record = self.record_val.get()
        s.fast_replay = self.replay_val.get()

    def _apply_and_save(self):
        if self.current:
            self._collect(self.current)
            self.store.update(self.current)
            self._refresh_list()

    # ── Действия ─────────────────────────────────────────────────────────────
    def _new_scenario(self):
        s = self.store.add(Scenario())
        self._select(s)

    # ── Запись демонстрации человека → сценарий ──────────────────────────────
    def _record_scenario(self):
        if getattr(self, '_recording', False):
            return
        win = ctk.CTkToplevel(self)
        win.title('Запись сценария')
        win.geometry('500x230')
        win.configure(fg_color=PALETTE['bg'])
        win.transient(self)
        ctk.CTkLabel(win, text='Что показываешь? (цель сценария)', font=self.f['body_b'],
                     text_color=PALETTE['text']).pack(anchor='w', padx=16, pady=(18, 4))
        ent = ctk.CTkEntry(win, font=self.f['body'], height=40, corner_radius=10, fg_color=PALETTE['field'],
                           border_color=PALETTE['field_border'], text_color=PALETTE['text'],
                           placeholder_text='напр.: оформить заказ в корзине')
        ent.pack(fill='x', padx=16)
        ent.focus()
        ctk.CTkLabel(win, text='Нажми «Начать», переключись в нужное приложение и покажи действия\n'
                     'мышью и клавиатурой. Остановка — кнопкой «⏹ Стоп запись» (висит поверх окон).',
                     font=self.f['small'], text_color=PALETTE['text_muted'], justify='left').pack(anchor='w', padx=16, pady=(10, 0))

        def begin():
            goal = ent.get().strip() or 'Записанный сценарий'
            win.destroy()
            self._start_recording(goal)
        ctk.CTkButton(win, text='🔴 Начать запись', font=self.f['body_b'], height=40, corner_radius=10,
                      fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                      text_color=PALETTE['accent_text'], command=begin).pack(fill='x', padx=16, pady=18)
        win.after(150, win.lift)

    def _start_recording(self, goal: str):
        from . import demo
        try:
            self._rec = demo.Recorder().start()
        except Exception as e:
            self._set_status(f'Не удалось начать запись: {e}', PALETTE['danger'])
            return
        self._rec_goal = goal
        self._recording = True
        self._set_status('🔴 Идёт запись — покажи действия, затем «⏹ Стоп запись».', PALETTE['danger'])
        sw = ctk.CTkToplevel(self)
        sw.title('Запись')
        sw.geometry('220x76+40+40')
        sw.configure(fg_color=PALETTE['bg'])
        sw.attributes('-topmost', True)
        sw.protocol('WM_DELETE_WINDOW', self._stop_recording)
        ctk.CTkButton(sw, text='⏹ Стоп запись', font=self.f['body_b'], height=48, corner_radius=10,
                      fg_color=PALETTE['danger'], hover_color=PALETTE['danger'], text_color='#ffffff',
                      command=self._stop_recording).pack(fill='both', expand=True, padx=8, pady=8)
        self._rec_win = sw

    def _stop_recording(self):
        if not getattr(self, '_recording', False):
            return
        self._recording = False
        sw = getattr(self, '_rec_win', None)
        rect = (0, 0, 0, 0)
        if sw is not None:
            try:
                rect = (sw.winfo_rootx(), sw.winfo_rooty(),
                        sw.winfo_rootx() + sw.winfo_width(), sw.winfo_rooty() + sw.winfo_height())
            except Exception:
                pass
        try:
            self._rec.stop()
        except Exception:
            pass
        if sw is not None:
            sw.destroy()
            self._rec_win = None
        evs, shots, goal = self._rec.events, self._rec.shots, self._rec_goal
        s = self.store.add(Scenario(name='🔴 ' + goal[:28], goal=goal))
        self._set_status('Собираю сценарий из записи…', PALETTE['text'])

        def work():
            from . import demo
            try:
                kept, kshots = [], {}
                for i, e in enumerate(evs):               # выкинуть клик по самой кнопке «Стоп»
                    if e['kind'] in ('click', 'double', 'right', 'drag') \
                            and rect[0] <= e.get('x', -1) <= rect[2] and rect[1] <= e.get('y', -1) <= rect[3]:
                        continue
                    j = len(kept)
                    kept.append(e)
                    if i in shots:
                        kshots[j] = shots[i]
                r = demo.build_recipe(kept, kshots, s.id, goal)
                n = len(r.steps) if r else 0
                self.after(0, lambda: (self._select(s),
                                       self._set_status(f'✅ Записан сценарий: {n} шаг(ов). Правь в «Схеме».', PALETTE['ok'])))
            except Exception as ex:
                msg = str(ex)                         # ex исчезает после except — лямбде нужна копия
                self.after(0, lambda: self._set_status(f'Ошибка сборки записи: {msg}', PALETTE['danger']))
        threading.Thread(target=work, daemon=True).start()

    def _duplicate(self, s: Scenario = None):
        s = s or self.current
        if s:
            self._apply_and_save()
            clone = self.store.duplicate(s.id)
            if clone:
                self._select(clone)

    def _delete(self, s: Scenario = None):
        s = s or self.current
        if not s:
            return
        if s.id in self.runs:
            self._set_status('Нельзя удалить сценарий, пока он выполняется.', PALETTE['danger'])
            return
        self.store.remove(s.id)
        if self.current and self.current.id == s.id:
            self.current = None
        self._refresh_list()
        if not self.current:
            if self.store.scenarios:
                self._select(self.store.scenarios[0])
            else:
                self._show_placeholder()

    def _set_default_browser(self, name: str):
        self.store.default_browser = name
        self.store.save()

    def _open_scheme(self):
        if self.current:
            self._apply_and_save()
            self._goto_tab('Схема')

    def _open_timeline(self):
        if self.current:
            self._apply_and_save()
            self._goto_tab('Запись')

    # ── Запуск сценария (очередь) ────────────────────────────────────────────
    # Мышь и клавиатура физически одни на всех — параллельно сценарии идти не могут.
    # Поэтому «несколько сценариев» = очередь: ставим в неё и выполняем по одному.
    def _run(self):
        if not self.current:
            return
        self._apply_and_save()
        s = self.current
        if not s.goal:
            self._set_status('Задайте цель сценария.', PALETTE['danger'])
            return
        if s.id in self.runs or s.id in self.queue:
            self._set_status('Этот сценарий уже запущен или в очереди.', PALETTE['text_muted'])
            return
        self.queue.append(s.id)
        if self.runs:
            self._set_status(f'В очереди ({len(self.queue)}). Запустится после текущего.',
                             PALETTE['text_muted'])
        self._refresh_list()
        self._pump_queue()

    def _pump_queue(self):
        if self.runs or not self.queue:            # одновременно — только один прогон
            return
        s = self.store.get(self.queue.pop(0))
        if s is None:
            return self._pump_queue()
        self.runs[s.id] = 'running'
        run_dir = RUNS / datetime.now().strftime('%Y%m%d-%H%M%S')
        run_dir.mkdir(parents=True, exist_ok=True)
        s.last_run = str(run_dir)
        from .capture import window_monitor
        here = window_monitor(ctypes.windll.user32.GetParent(self.winfo_id()))
        self._select(s)                            # следим за тем, что выполняется
        self._mount_scheme(live=True)
        self._goto_tab('Схема')
        self._refresh_list()
        if getattr(self, 'run_btn', None) and self.run_btn.winfo_exists():
            self.run_btn.configure(state='disabled', text='Идёт…')
        threading.Thread(target=self._run_worker, args=(s, run_dir, here), daemon=True).start()

    def _run_worker(self, s: Scenario, run_dir: Path, here: int):
        from . import recipe as recipe_mod
        from .capture import dpi_aware, monitors
        from .memory import Memory
        from .replay import run_scenario
        from .screen import RealEnv

        logs: list[str] = []
        rec = None
        try:
            dpi_aware()
            mons = monitors()
            mon = s.monitor if 0 < s.monitor < len(mons) else 0
            if s.start_url:
                br = browsers_mod.find(s.browser) or browsers_mod.find(self.store.default_browser) \
                    or browsers_mod.default_browser(self.browsers)
                if not mon:
                    # авто: браузер — на экран без лаунчера, чтобы живая «Схема» была видна рядом
                    mon = next((i for i in range(1, len(mons)) if i != here), here)
                if br:
                    browsers_mod.open_on_monitor(br, s.start_url, mons[mon], incognito=s.incognito)
                else:
                    webbrowser.open(s.start_url)
                time.sleep(2.5)

            for c in range(3, 0, -1):
                self._set_status(f'Старт через {c}… переключитесь в нужное окно', PALETTE['text_muted'])
                time.sleep(1)
            env = RealEnv(mon, s.speed)               # 0 — авто: следует за рабочим окном
            mon = env.monitor
            memory = Memory() if s.learn else None
            if s.record:
                from .recorder import ScreenRecorder
                rec = ScreenRecorder(run_dir, mon).start()
            rp = recipe_mod.load(s.id)
            r = run_scenario(s.goal, env, rp, s.fast_replay, run_dir=run_dir, log=logs.append,
                             memory=memory, hints=s.hints, noise=s.noise,
                             on_step=rec.mark_step if rec else None,
                             max_steps=s.max_steps, refine=s.refine)
            s.last_run = str(run_dir)
            self.store.update(s)
            if r.get('fresh_recipe'):               # разбор прошёл целиком — запоминаем рецепт
                recipe_mod.from_run(s.id, s.goal, run_dir)
                logs.append('  📝 рецепт записан — дальше этот сценарий пойдёт быстрым путём')
            icon = {'done': '✅', 'fail': '🛑', 'stuck': '🧱', 'limit': '⏱',
                    'aborted': '⏹', 'blind': '🙈'}.get(r['status'], '•')
            via = {'recipe': ' · по рецепту', 'recipe+analysis': ' · рецепт+разбор'}.get(r.get('via'), '')
            color = PALETTE['ok'] if r['status'] == 'done' else PALETTE['danger']
            self._set_status(f"{icon} {r['status']}{via} · {r['steps']} шагов · {r['seconds']} с · {r['answer'][:70]}", color)
        except Exception as e:
            self._set_status(f'Ошибка: {e}', PALETTE['danger'])
        finally:
            if rec:
                rec.stop()
            self.runs.pop(s.id, None)
            self.after(0, self._run_finished)

    def _run_finished(self):
        if getattr(self, 'run_btn', None) and self.run_btn.winfo_exists():
            self.run_btn.configure(state='normal', text='▶  Запустить')
        self._refresh_list()
        self._pump_queue()                         # следующий из очереди, если есть

    def _set_status(self, text: str, color: str):
        self.after(0, lambda: self.status.configure(text=text, text_color=color))


def _int(text: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(text))))
    except (ValueError, TypeError):
        return default


def main():
    from . import single
    app = App()
    single.serve(lambda: app.after(0, lambda: (app.deiconify(), app.lift(), app.focus_force())))
    app.mainloop()


if __name__ == '__main__':
    main()
