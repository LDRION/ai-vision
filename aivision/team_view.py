"""
Панель «Команда» — оркестрация из окна: оргсхема, ввод запроса, план по агентам
и запуск. По умолчанию «Спланировать» (мышь не трогается). «Выполнить» гонит план
через агентов; переключатель «по-настоящему» включает боевое исполнение scenario-
агентов через движок (нужен свободный экран) — иначе сухой прогон.
"""

import json
import threading
from pathlib import Path

import customtkinter as ctk

from .ui_theme import PAD, PALETTE, RADIUS


def _clipboard_fix(e):
    """Ctrl+C/V/X/A в любой раскладке. На русской раскладке <Control-v> не срабатывает
    (клавиша даёт кириллический keysym), поэтому латиницу отдаём штатному биндингу, а
    кириллицу ловим по keycode физической клавиши (Windows: V=86, C=67, X=88, A=65)."""
    if not (e.state & 0x0004):                  # зажат ли Control
        return
    if (e.keysym or '').lower() in ('v', 'c', 'x', 'a'):
        return                                  # латиница — обработает стандартный биндинг
    w = e.widget
    try:
        if e.keycode == 86:
            w.event_generate('<<Paste>>'); return 'break'
        if e.keycode == 67:
            w.event_generate('<<Copy>>'); return 'break'
        if e.keycode == 88:
            w.event_generate('<<Cut>>'); return 'break'
        if e.keycode == 65:
            try:
                w.select_range(0, 'end')
            except Exception:
                try:
                    w.tag_add('sel', '1.0', 'end')
                except Exception:
                    pass
            return 'break'
    except Exception:
        pass


class TeamView(ctk.CTkFrame):
    def __init__(self, master, f):
        super().__init__(master, fg_color=PALETTE['bg'], corner_radius=0)
        self.f = f
        self.busy = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_bar()
        self._build_body()
        self.refresh_org()

    # ── Ввод запроса ─────────────────────────────────────────────────────────
    def _build_bar(self):
        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.grid(row=0, column=0, sticky='ew', padx=PAD, pady=(PAD, 6))
        bar.grid_columnconfigure(0, weight=1)
        self.req = ctk.CTkEntry(bar, font=self.f['body'], height=42, corner_radius=10,
                                placeholder_text='Что поручить команде? Напр.: найди 5 заказов по Telegram-ботам и сделай сводку',
                                fg_color=PALETTE['surface'], border_color=PALETTE['border'],
                                text_color=PALETTE['text'])
        self.req.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.req.bind('<Return>', lambda _e: self._go(execute=False))
        self.req.bind('<Control-KeyPress>', _clipboard_fix, add='+')   # вставка на рус. раскладке
        ctk.CTkButton(bar, text='Спланировать', font=self.f['body_b'], height=42, width=130,
                      corner_radius=RADIUS, fg_color=PALETTE['surface'], text_color=PALETTE['text'],
                      hover_color=PALETTE['surface_muted'], border_width=1, border_color=PALETTE['border'],
                      command=lambda: self._go(execute=False)).grid(row=0, column=1)
        self.run_btn = ctk.CTkButton(bar, text='▶ Выполнить', font=self.f['body_b'], height=42, width=130,
                                     corner_radius=RADIUS, fg_color=PALETTE['accent'],
                                     hover_color=PALETTE['accent_hover'], text_color=PALETTE['accent_text'],
                                     command=lambda: self._go(execute=True))
        self.run_btn.grid(row=0, column=2, padx=(8, 0))
        self.stop_btn = ctk.CTkButton(bar, text='⏹ Стоп', font=self.f['body_b'], height=42, width=92,
                                      corner_radius=RADIUS, fg_color=PALETTE['surface'], text_color=PALETTE['text'],
                                      hover_color=PALETTE['surface_muted'], border_width=1,
                                      border_color=PALETTE['border'], command=self._stop, state='disabled')
        self.stop_btn.grid(row=0, column=3, padx=(8, 0))
        self.real = ctk.BooleanVar(value=True)       # всегда по-настоящему (галку убрали)

    # ── Оргсхема + план/лог ─────────────────────────────────────────────────
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.grid(row=2, column=0, sticky='nsew', padx=PAD, pady=(8, PAD))
        body.grid_columnconfigure(0, weight=0, minsize=320)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=PALETTE['surface'], corner_radius=RADIUS,
                            border_width=1, border_color=PALETTE['border'])
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        lhead = ctk.CTkFrame(left, fg_color='transparent')
        lhead.grid(row=0, column=0, sticky='ew', padx=14, pady=(12, 4))
        lhead.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(lhead, text='Команда', font=self.f['h2'], text_color=PALETTE['text']).grid(
            row=0, column=0, sticky='w')
        ctk.CTkButton(lhead, text='Знания', font=self.f['small'], width=64, height=26, corner_radius=8,
                      fg_color=PALETTE['surface_muted'], text_color=PALETTE['text'],
                      hover_color=PALETTE['border'], command=self._edit_knowledge).grid(row=0, column=1, padx=4)
        ctk.CTkButton(lhead, text='+ Агент', font=self.f['small'], width=70, height=26, corner_radius=8,
                      fg_color=PALETTE['accent'], text_color=PALETTE['accent_text'],
                      hover_color=PALETTE['accent_hover'], command=lambda: self._edit_agent(None)).grid(row=0, column=2)
        self.org = ctk.CTkScrollableFrame(left, fg_color='transparent')
        self.org.grid(row=1, column=0, sticky='nsew', padx=6, pady=(0, 6))
        self._build_pulse(left)

        right = ctk.CTkFrame(body, fg_color='transparent')
        right.grid(row=0, column=1, sticky='nsew')
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        self.plan_box = ctk.CTkFrame(right, fg_color=PALETTE['surface'], corner_radius=RADIUS,
                                     border_width=1, border_color=PALETTE['border'])
        self.plan_box.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ctk.CTkLabel(self.plan_box, text='План появится здесь', font=self.f['body'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=14, pady=12)
        self.log = ctk.CTkTextbox(right, font=self.f['mono'], corner_radius=RADIUS,
                                  fg_color=PALETTE['surface'], border_color=PALETTE['border'],
                                  border_width=1, text_color=PALETTE['text'], wrap='word')
        self.log.grid(row=1, column=0, sticky='nsew')

    # ── Пульс 24/7 ───────────────────────────────────────────────────────────
    def _build_pulse(self, parent):
        box = ctk.CTkFrame(parent, fg_color=PALETTE['surface_muted'], corner_radius=RADIUS)
        box.grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 10))
        top = ctk.CTkFrame(box, fg_color='transparent')
        top.pack(fill='x', padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text='❤️ Пульс 24/7', font=self.f['body_b'],
                     text_color=PALETTE['text']).pack(side='left')
        self.pulse_on = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(top, text='', variable=self.pulse_on, width=44, command=self._toggle_pulse,
                      progress_color=PALETTE['accent'], button_color=PALETTE['surface']).pack(side='right')
        row = ctk.CTkFrame(box, fg_color='transparent')
        row.pack(fill='x', padx=12, pady=(0, 6))
        ctk.CTkLabel(row, text='каждые', font=self.f['small'], text_color=PALETTE['text_muted']).pack(side='left')
        self.interval = ctk.CTkEntry(row, width=56, height=26, font=self.f['small'],
                                     fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                                     text_color=PALETTE['text'])
        self.interval.insert(0, '300')
        self.interval.pack(side='left', padx=6)
        ctk.CTkLabel(row, text='сек', font=self.f['small'], text_color=PALETTE['text_muted']).pack(side='left')
        ctk.CTkButton(row, text='Тик', font=self.f['small'], width=50, height=26, corner_radius=8,
                      fg_color=PALETTE['surface'], text_color=PALETTE['text'], hover_color=PALETTE['border'],
                      command=self._pulse_once).pack(side='right')
        self.inbox_lbl = ctk.CTkLabel(box, text='входящие: 0', font=self.f['small'],
                                      text_color=PALETTE['text_muted'], anchor='w', justify='left',
                                      wraplength=280)
        self.inbox_lbl.pack(fill='x', padx=12, pady=(0, 4))
        btns = ctk.CTkFrame(box, fg_color='transparent')
        btns.pack(fill='x', padx=12, pady=(0, 6))
        ctk.CTkButton(btns, text='Разобрать входящие', font=self.f['small'], height=28, corner_radius=8,
                      fg_color=PALETTE['surface'], text_color=PALETTE['text'], hover_color=PALETTE['border'],
                      command=self._drain).pack(side='left', expand=True, fill='x', padx=(0, 4))
        ctk.CTkButton(btns, text='Журнал', font=self.f['small'], height=28, width=64, corner_radius=8,
                      fg_color=PALETTE['surface'], text_color=PALETTE['text'], hover_color=PALETTE['border'],
                      command=self._show_journal).pack(side='right')
        ctk.CTkButton(btns, text='Ссылки', font=self.f['small'], height=28, width=70, corner_radius=8,
                      fg_color=PALETTE['surface'], text_color=PALETTE['text'],
                      hover_color=PALETTE['border'], command=self._show_resources).pack(side='right', padx=4)
        ctk.CTkButton(btns, text='Проекты', font=self.f['small'], height=28, width=72, corner_radius=8,
                      fg_color=PALETTE['surface'], text_color=PALETTE['text'],
                      hover_color=PALETTE['border'], command=self._show_projects).pack(side='right', padx=4)
        ctk.CTkButton(btns, text='Задачи', font=self.f['small'], height=28, width=64, corner_radius=8,
                      fg_color=PALETTE['surface'], text_color=PALETTE['text'], hover_color=PALETTE['border'],
                      command=self._show_tasks).pack(side='right', padx=4)
        # Подтверждения удержанных (необратимых/денежных) действий
        self.approvals = ctk.CTkFrame(box, fg_color='transparent')
        self.approvals.pack(fill='x', padx=12, pady=(0, 10))
        self._pulse_job = None
        self._refresh_inbox()

    def refresh_org(self):
        from .team import build_departments
        for w in self.org.winfo_children():
            w.destroy()
        for d in build_departments():
            head = f'▸ {d.name}' + (f' · лид: {d.lead.name}' if d.lead else '')
            ctk.CTkLabel(self.org, text=head, font=self.f['body_b'], text_color=PALETTE['text'],
                         anchor='w').pack(fill='x', padx=8, pady=(8, 0))
            for m in d.roster():
                lbl = ctk.CTkLabel(self.org, text=f'   · {m.name} [{", ".join(m.capabilities) or "—"}]',
                                   font=self.f['small'], text_color=PALETTE['text_muted'], anchor='w',
                                   wraplength=280, justify='left', cursor='hand2')
                lbl.pack(fill='x', padx=8)
                lbl.bind('<Button-1>', lambda _e, ag=m: self._edit_agent(ag))

    # ── Редактор агента ──────────────────────────────────────────────────────
    def _edit_agent(self, agent):
        from .agents import write_agent
        win = ctk.CTkToplevel(self)
        win.title('Агент' if agent else 'Новый агент')
        win.geometry('560x620')
        win.configure(fg_color=PALETTE['bg'])
        win.transient(self.winfo_toplevel())

        def field(label, value='', h=1):
            ctk.CTkLabel(win, text=label, font=self.f['small'], text_color=PALETTE['text_muted']).pack(
                anchor='w', padx=16, pady=(8, 2))
            if h > 1:
                w = ctk.CTkTextbox(win, font=self.f['body'], height=h, corner_radius=8,
                                   fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                                   border_width=1, text_color=PALETTE['text'], wrap='word')
                w.insert('1.0', value)
            else:
                w = ctk.CTkEntry(win, font=self.f['body'], height=34, corner_radius=8,
                                 fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                                 text_color=PALETTE['text'])
                w.insert(0, value)
            w.pack(fill='x', padx=16)
            return w

        name = field('Имя', agent.name if agent else '')
        desc = field('Описание', agent.description if agent else '')
        dept = field('Отдел', agent.department if agent else '')
        caps = field('Возможности (через запятую)', ', '.join(agent.capabilities) if agent else '')
        tools = field('Инструменты (scenario, groq, text, image, video, orchestrate)',
                      ', '.join(agent.tools) if agent else '')
        reports = field('Подчиняется', agent.reports_to if agent else 'Оркестратор')
        lead_var = ctk.BooleanVar(value=('orchestrate' in agent.tools) if agent else False)
        ctk.CTkCheckBox(win, text='Лид отдела (руководит и делегирует)', variable=lead_var,
                        font=self.f['small'], text_color=PALETTE['text'], fg_color=PALETTE['accent'],
                        hover_color=PALETTE['accent_hover'], border_color=PALETTE['field_border']).pack(
            anchor='w', padx=16, pady=(8, 0))
        role = field('Роль (системный промт)', agent.prompt if agent else '', h=180)

        status = ctk.CTkLabel(win, text='', font=self.f['small'], text_color=PALETTE['text_muted'])
        status.pack(anchor='w', padx=16, pady=(6, 0))

        def save():
            toolset = [c.strip() for c in tools.get().split(',') if c.strip()]
            if lead_var.get() and 'orchestrate' not in toolset:
                toolset.append('orchestrate')
            elif not lead_var.get():
                toolset = [t for t in toolset if t != 'orchestrate']
            spec = {'name': name.get().strip(), 'description': desc.get().strip(),
                    'department': dept.get().strip(), 'reports_to': reports.get().strip(),
                    'capabilities': [c.strip() for c in caps.get().split(',') if c.strip()],
                    'tools': toolset,
                    'prompt': role.get('1.0', 'end').strip()}
            if agent and agent.slug:
                spec['slug'] = agent.slug
            if not spec['name']:
                status.configure(text='Задайте имя', text_color=PALETTE['danger'])
                return
            write_agent(spec)
            self.refresh_org()
            win.destroy()

        bar = ctk.CTkFrame(win, fg_color='transparent')
        bar.pack(fill='x', padx=16, pady=14)
        ctk.CTkButton(bar, text='Сохранить', font=self.f['body_b'], height=38, corner_radius=10,
                      fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                      text_color=PALETTE['accent_text'], command=save).pack(side='left', expand=True, fill='x')
        if agent:
            ctk.CTkButton(bar, text='Удалить', font=self.f['body'], height=38, width=100, corner_radius=10,
                          fg_color='transparent', text_color=PALETTE['danger'],
                          hover_color=PALETTE['danger_soft'],
                          command=lambda: self._delete_agent(agent, win)).pack(side='right', padx=(8, 0))
        win.after(120, win.lift)

    def _delete_agent(self, agent, win):
        if agent.source and Path(agent.source).exists():
            Path(agent.source).unlink()
        self.refresh_org()
        win.destroy()

    # ── Редактор знаний ──────────────────────────────────────────────────────
    def _edit_knowledge(self):
        from . import knowledge
        win = ctk.CTkToplevel(self)
        win.title('Знания команды')
        win.geometry('620x560')
        win.configure(fg_color=PALETTE['bg'])
        win.transient(self.winfo_toplevel())
        docs = dict(knowledge.documents())
        names = list(docs) or ['Бренд']

        ctk.CTkLabel(win, text='Документ', font=self.f['small'], text_color=PALETTE['text_muted']).pack(
            anchor='w', padx=16, pady=(12, 2))
        pick = ctk.CTkOptionMenu(win, values=names + ['+ новый'], font=self.f['body'],
                                 fg_color=PALETTE['field'], button_color=PALETTE['border'],
                                 button_hover_color=PALETTE['border'], text_color=PALETTE['text'],
                                 dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
        pick.pack(fill='x', padx=16)
        title = ctk.CTkEntry(win, font=self.f['body'], height=34, corner_radius=8,
                             fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                             text_color=PALETTE['text'])
        title.pack(fill='x', padx=16, pady=(8, 0))
        body = ctk.CTkTextbox(win, font=self.f['body'], corner_radius=8, fg_color=PALETTE['field'],
                              border_color=PALETTE['field_border'], border_width=1,
                              text_color=PALETTE['text'], wrap='word')
        body.pack(fill='both', expand=True, padx=16, pady=10)

        def load(name):
            title.delete(0, 'end'); body.delete('1.0', 'end')
            if name != '+ новый':
                title.insert(0, name)
                body.insert('1.0', docs.get(name, ''))
        pick.configure(command=load)
        pick.set(names[0]); load(names[0])

        def save():
            t = title.get().strip()
            if t:
                knowledge.remember(t, body.get('1.0', 'end').strip())
                win.destroy()
        ctk.CTkButton(win, text='Сохранить', font=self.f['body_b'], height=38, corner_radius=10,
                      fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                      text_color=PALETTE['accent_text'], command=save).pack(fill='x', padx=16, pady=(0, 14))
        win.after(120, win.lift)

    # ── Запуск ───────────────────────────────────────────────────────────────
    def _go(self, execute: bool):
        if self.busy:
            return
        request = self.req.get().strip()
        if not request:
            return
        from . import control
        control.clear()
        self.busy = True
        self.run_btn.configure(state='disabled')
        if execute:
            self.stop_btn.configure(state='normal')
        self.log.delete('1.0', 'end')
        self._log('🧭 оркестратор думает…\n')
        threading.Thread(target=self._worker, args=(request, execute), daemon=True).start()

    def _stop(self):
        from . import control
        control.request_stop()
        self._log('\n⏹ останавливаю… текущее действие завершится, новые не начнутся.\n')
        self.stop_btn.configure(state='disabled')

    def _worker(self, request: str, execute: bool):
        from . import orchestrator as O
        from .agents import load_agents
        try:
            agents = load_agents()
            steps = O.plan(request, agents)
            if not steps:
                r = O.route(request, agents)
                steps = [O.Task(agent=r.agent, agent_name=r.agent.name if r.agent else '',
                                task=request, depends_on=[], why=r.why)]
            self.after(0, lambda: self._render_plan(steps))
            if not execute:
                self._log('\nГотово. Нажми «Выполнить», чтобы запустить план.\n')
                return
            dry = not self.real.get()
            self._log(f'\n▶ исполнение {"(сухой прогон)" if dry else "(по-настоящему)"}…\n')
            out = O.orchestrate(request, self._run_step_factory(dry), agents=agents, log=self._log, strategy=True)
            self._log(f'\n== ИТОГ: {out["status"]} ==\n{out["answer"]}\n')
        except Exception as e:
            self._log(f'\n⚠️ ошибка: {e}\n')
        finally:
            self.busy = False
            self.after(0, lambda: (self.run_btn.configure(state='normal'),
                                   self.stop_btn.configure(state='disabled')))

    def _run_step_factory(self, dry: bool):
        from . import orchestrator as O

        def run_step(task, context):
            agent = task.agent
            if agent is None:
                return {'status': 'fail', 'answer': f'агент «{task.agent_name}» не найден'}
            if dry:
                return {'status': 'done', 'answer': f'[план] {agent.name} выполнил бы: {task.task}'}
            env = None
            if 'scenario' in set(agent.tools):
                from .capture import dpi_aware
                from .screen import RealEnv
                dpi_aware()
                env = RealEnv(0)                # авто: монитор рабочего окна, не лаунчера
            return O.tool_step(agent, task.task, context, env=env, log=self._log)
        return run_step

    def _render_plan(self, steps):
        for w in self.plan_box.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.plan_box, text=f'План · {len(steps)} шаг(ов)', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=14, pady=(10, 2))
        for i, s in enumerate(steps):
            name = s.agent.name if s.agent else s.agent_name
            dep = f'  ⬅ из шага {s.depends_on}' if s.depends_on else ''
            row = ctk.CTkFrame(self.plan_box, fg_color=PALETTE['surface_muted'], corner_radius=10)
            row.pack(fill='x', padx=12, pady=3)
            ctk.CTkLabel(row, text=f'{i}. [{name}]{dep}', font=self.f['body_b'], text_color=PALETTE['text'],
                         anchor='w').pack(fill='x', padx=10, pady=(6, 0))
            ctk.CTkLabel(row, text=s.task, font=self.f['small'], text_color=PALETTE['text_muted'],
                         anchor='w', wraplength=640, justify='left').pack(fill='x', padx=10, pady=(0, 6))

    def _log(self, text: str):
        def write():
            if self.log.winfo_exists():
                self.log.insert('end', text if text.endswith('\n') else text + '\n')
                self.log.see('end')
        self.after(0, write)

    # ── Пульс: логика ────────────────────────────────────────────────────────
    def _toggle_pulse(self):
        if self.pulse_on.get():
            self._log('❤️ пульс 24/7 включён\n')
            self._pulse_once()
            self._schedule_pulse()
        else:
            if self._pulse_job:
                self.after_cancel(self._pulse_job)
                self._pulse_job = None
            self._log('⏹ пульс выключен\n')

    def _schedule_pulse(self):
        if not self.pulse_on.get():
            return
        try:
            interval = max(10, int(float(self.interval.get())))
        except ValueError:
            interval = 300
        self._pulse_job = self.after(interval * 1000, self._pulse_loop)

    def _pulse_loop(self):
        self._pulse_once()
        self._schedule_pulse()

    def _pulse_once(self):
        threading.Thread(target=self._pulse_worker, daemon=True).start()

    def _pulse_worker(self):
        from . import pulse
        try:
            iid = pulse.tick(log=self._log)
        except Exception as e:
            self._log(f'⚠️ пульс: {e}')
            iid = None
        self.after(0, self._refresh_inbox)

    def _refresh_inbox(self):
        import json

        from . import pulse
        items = []
        for p in pulse.pending():
            try:
                items.append(json.loads(p.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
        if not items:
            self.inbox_lbl.configure(text='входящие: 0')
        else:
            lines = [f'входящие: {len(items)}'] + [f'• [{it.get("area", "")}] {it.get("task", "")[:50]}'
                                                   for it in items[:4]]
            self.inbox_lbl.configure(text='\n'.join(lines))
        self._refresh_approvals()

    def _refresh_approvals(self):
        from . import approval
        for w in self.approvals.winfo_children():
            w.destroy()
        pend = approval.pending()
        if not pend:
            return
        ctk.CTkLabel(self.approvals, text=f'✋ ждут подтверждения: {len(pend)}', font=self.f['small'],
                     text_color=PALETTE['danger'], anchor='w').pack(fill='x')
        for a in pend[:3]:
            row = ctk.CTkFrame(self.approvals, fg_color=PALETTE['surface'], corner_radius=8)
            row.pack(fill='x', pady=2)
            ctk.CTkLabel(row, text=f'{a.get("agent", "")}: {a.get("task", "")[:34]}', font=self.f['small'],
                         text_color=PALETTE['text'], anchor='w', wraplength=180,
                         justify='left').pack(side='left', padx=(8, 4), pady=4)
            ctk.CTkButton(row, text='✗', width=28, height=24, font=self.f['small'], corner_radius=6,
                          fg_color='transparent', text_color=PALETTE['danger'],
                          hover_color=PALETTE['danger_soft'],
                          command=lambda aid=a['id']: self._resolve(aid, False)).pack(side='right', padx=(0, 6))
            ctk.CTkButton(row, text='✓', width=28, height=24, font=self.f['small'], corner_radius=6,
                          fg_color=PALETTE['accent'], text_color=PALETTE['accent_text'],
                          hover_color=PALETTE['accent_hover'],
                          command=lambda it=a: self._resolve(it['id'], True, it)).pack(side='right', padx=4)

    def _resolve(self, aid: str, ok: bool, item=None):
        from . import approval
        approval.resolve(aid, ok)
        if ok and item:
            self._log(f'✅ одобрено: {item.get("task", "")[:50]} — выполняю\n')
            threading.Thread(target=self._approved_worker, args=(item,), daemon=True).start()
        else:
            self._log('✗ отклонено\n')
        self._refresh_approvals()

    def _approved_worker(self, item):
        from . import orchestrator as O
        from .agents import find_agent, load_agents
        agent = find_agent(item.get('agent', ''), load_agents())
        if agent is None:
            self._log('⚠️ агент не найден\n')
            return
        env = None
        if 'scenario' in set(agent.tools):
            from .capture import dpi_aware
            from .screen import RealEnv
            dpi_aware()
            env = RealEnv(0)                # авто: монитор рабочего окна, не лаунчера
        res = O.tool_step(agent, item.get('task', ''), '', env=env, log=self._log)
        self._log(f'→ {res.get("status")}: {str(res.get("answer", ""))[:80]}\n')

    def _show_journal(self):
        from . import paths
        p = paths.STATE / 'journal.md'
        self.log.delete('1.0', 'end')
        if p.exists():
            tail = p.read_text(encoding='utf-8').splitlines()[-40:]
            self.log.insert('end', 'ЖУРНАЛ ДЕЙСТВИЙ (последние):\n' + '\n'.join(tail) + '\n')
        else:
            self.log.insert('end', 'журнал пуст\n')

    # ── Задачи и отчёты ──────────────────────────────────────────────────────
    def _show_tasks(self):
        from . import paths, workspace
        win = ctk.CTkToplevel(self)
        win.title('Задачи компании')
        win.geometry('860x600')
        win.configure(fg_color=PALETTE['bg'])
        win.transient(self.winfo_toplevel())
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(win, fg_color=PALETTE['surface'], width=360, corner_radius=0)
        left.grid(row=0, column=0, sticky='nsew')
        detail = ctk.CTkTextbox(win, font=self.f['body'], corner_radius=0, fg_color=PALETTE['surface'],
                                text_color=PALETTE['text'], wrap='word')
        detail.grid(row=0, column=1, sticky='nsew')

        def open_task(t):
            detail.delete('1.0', 'end')
            rep = paths.REPORTS / f'{t["id"]}.md'
            head = f'{t.get("goal", "")}\n\nстатус: {t.get("status")}  ·  исполнитель: {t.get("assignee") or "—"}\n'
            body = rep.read_text(encoding='utf-8') if rep.exists() else '(отчёта нет)'
            detail.insert('1.0', head + '\n' + body)

        tasks = sorted(workspace.list_tasks(), key=lambda t: t.get('created', 0), reverse=True)[:60]
        icon = {'done': '✅', 'fail': '🛑', 'held': '✋', 'in_progress': '⏳', 'skipped': '⤼',
                'partial': '◐', 'new': '•'}
        if not tasks:
            ctk.CTkLabel(left, text='Задач ещё не было', font=self.f['body'],
                         text_color=PALETTE['text_muted']).pack(padx=12, pady=12)
        for t in tasks:
            parent = '' if t.get('parent') else '▸ '
            row = ctk.CTkButton(left, text=f'{parent}{icon.get(t.get("status"), "•")} {t.get("goal", "")[:40]}',
                                font=self.f['small'], anchor='w', height=30, corner_radius=8,
                                fg_color='transparent', text_color=PALETTE['text'],
                                hover_color=PALETTE['surface_muted'], command=lambda tt=t: open_task(tt))
            row.pack(fill='x', padx=6, pady=1)
        win.after(120, win.lift)

    def _show_resources(self):
        """Реестр ссылок и данных проектов (resources/*.md). Оператор у окна = оркестратор."""
        from . import resources
        win = ctk.CTkToplevel(self)
        win.title('Ссылки и данные')
        win.geometry('640x600')
        win.configure(fg_color=PALETTE['bg'])
        win.transient(self.winfo_toplevel())
        docs = {s: (m, b) for s, m, b in resources.resources()}
        names = list(docs) or ['otkliks']

        ctk.CTkLabel(win, text='Реестр', font=self.f['small'], text_color=PALETTE['text_muted']).pack(
            anchor='w', padx=16, pady=(12, 2))
        pick = ctk.CTkOptionMenu(win, values=names + ['+ новый'], font=self.f['body'],
                                 fg_color=PALETTE['field'], button_color=PALETTE['border'],
                                 button_hover_color=PALETTE['border'], text_color=PALETTE['text'],
                                 dropdown_fg_color=PALETTE['surface'], dropdown_text_color=PALETTE['text'])
        pick.pack(fill='x', padx=16)
        info = ctk.CTkLabel(win, text='', font=self.f['small'], text_color=PALETTE['text_muted'])
        info.pack(anchor='w', padx=16, pady=(6, 0))
        title = ctk.CTkEntry(win, font=self.f['body'], height=34, corner_radius=8, fg_color=PALETTE['field'],
                             border_color=PALETTE['field_border'], text_color=PALETTE['text'],
                             placeholder_text='имя файла реестра (например vk)')
        title.pack(fill='x', padx=16, pady=(6, 0))
        body = ctk.CTkTextbox(win, font=self.f['body'], corner_radius=8, fg_color=PALETTE['field'],
                              border_color=PALETTE['field_border'], border_width=1,
                              text_color=PALETTE['text'], wrap='word')
        body.pack(fill='both', expand=True, padx=16, pady=10)
        state = {'meta': {}}

        def load(name):
            title.delete(0, 'end'); body.delete('1.0', 'end')
            if name == '+ новый':
                state['meta'] = {'project': '', 'owners': [], 'approval': False}
                info.configure(text='новый реестр — укажи имя файла ниже')
                return
            m, b = docs.get(name, ({}, ''))
            state['meta'] = m or {}
            title.insert(0, name); body.insert('1.0', b)
            owners = ', '.join(m.get('owners') or []) or '—'
            appr = 'да' if m.get('approval') else 'нет'
            info.configure(text=f'правят роли: {owners}   ·   правка требует одобрения: {appr}')

        status = ctk.CTkLabel(win, text='', font=self.f['small'], text_color=PALETTE['text_muted'])
        status.pack(anchor='w', padx=16)

        def save():
            slug = title.get().strip() or pick.get()
            resources.write(slug, body.get('1.0', 'end'), state.get('meta') or None)
            status.configure(text='сохранено ✓')

        ctk.CTkButton(win, text='Сохранить', font=self.f['body_b'], height=38, corner_radius=10,
                      fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                      text_color=PALETTE['accent_text'], command=save).pack(fill='x', padx=16, pady=(0, 12))
        pick.configure(command=load)
        load(names[0])
        win.after(120, win.lift)

    def _show_projects(self):
        """Проекты «единого мозга»: где живут, SSH-хост и дневной ритм (без фанатизма)."""
        from . import policy, projects, remote
        win = ctk.CTkToplevel(self)
        win.title('Проекты и ритм')
        win.geometry('620x560')
        win.configure(fg_color=PALETTE['bg'])
        win.transient(self.winfo_toplevel())

        ctk.CTkLabel(win, text='Проекты', font=self.f['h2'], text_color=PALETTE['text']).pack(
            anchor='w', padx=16, pady=(14, 2))
        ctk.CTkLabel(win, text='Для ssh-проектов укажи хост (user@host) — туда оркестратор ходит по SSH.',
                     font=self.f['small'], text_color=PALETTE['text_muted']).pack(anchor='w', padx=16)
        host_env = remote.hosts()
        rows = []
        for p in projects.projects():
            fr = ctk.CTkFrame(win, fg_color=PALETTE['surface'], corner_radius=10)
            fr.pack(fill='x', padx=16, pady=4)
            ctk.CTkLabel(fr, text=f'{p.name}  ·  {p.kind}', font=self.f['body_b'],
                         text_color=PALETTE['text']).pack(anchor='w', padx=12, pady=(8, 0))
            ctk.CTkLabel(fr, text=p.note, font=self.f['small'], text_color=PALETTE['text_muted']).pack(
                anchor='w', padx=12)
            ent = ctk.CTkEntry(fr, font=self.f['body'], height=32, corner_radius=8,
                               fg_color=PALETTE['field'], border_color=PALETTE['field_border'],
                               text_color=PALETTE['text'], placeholder_text='user@host или путь')
            ent.insert(0, p.where)
            ent.pack(fill='x', padx=12, pady=(4, 10))
            rows.append((p, ent))

        status = ctk.CTkLabel(win, text='', font=self.f['small'], text_color=PALETTE['text_muted'])
        status.pack(anchor='w', padx=16, pady=(2, 0))

        def save():
            for p, ent in rows:
                where = ent.get().strip()
                projects.add(p.name, p.kind, where, p.note)
                if p.kind == 'ssh' and where:              # ssh-проект → в known hosts под своим именем
                    remote.HOSTS.parent.mkdir(parents=True, exist_ok=True)
                    known = remote.hosts(); known[p.name] = where
                    remote.HOSTS.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding='utf-8')
            status.configure(text='сохранено ✓')

        ctk.CTkButton(win, text='Сохранить', font=self.f['body_b'], height=38, corner_radius=10,
                      fg_color=PALETTE['accent'], hover_color=PALETTE['accent_hover'],
                      text_color=PALETTE['accent_text'], command=save).pack(fill='x', padx=16, pady=(8, 6))

        today = ', '.join(f'{a}: {policy.count_today(a)}/{lim}' for a, lim in policy.LIMITS.items())
        ctk.CTkLabel(win, text=f'Ритм сегодня — {today}', font=self.f['small'],
                     text_color=PALETTE['text_muted']).pack(anchor='w', padx=16, pady=(0, 12))
        win.after(120, win.lift)

    def _drain(self):
        if self.busy:
            return
        self.busy = True
        self._log('\n▶ разбираю входящие…\n')
        threading.Thread(target=self._drain_worker, daemon=True).start()

    def _drain_worker(self):
        from . import orchestrator as O, pulse
        from .agents import load_agents
        dry = not self.real.get()
        agents = load_agents()

        def run_request(task):
            return O.orchestrate(task, self._run_step_factory(dry), agents=agents, log=self._log)
        try:
            res = pulse.drain_inbox(run_request, log=self._log)
            self._log(f'\nразобрано задач: {len(res)}\n')
        except Exception as e:
            self._log(f'⚠️ {e}\n')
        finally:
            self.busy = False
            self.after(0, self._refresh_inbox)
