"""
Лаунчер AI Vision — одно окно на веб-виде (pywebview / Edge WebView2) в дизайне
лаунчера Откликса (webui/): рейка разделов, панель с пунктами и счётчиками,
рабочая область с карточками. Логика — движок AI Vision (оркестратор, сценарии,
одобрения, пульс); здесь только мост JS ↔ Python, окно и трей.

Службы (встроенная Screen Retina, Ollama) поднимаются сами, без отдельных окон;
что видит Retina — во вкладке «Зрение».

Запуск:  python -m aivision          (старый вид: python -m aivision --classic)
"""
import json
import logging
import os
import re
import threading
import time
import webbrowser
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

import webview

# pywebview на бэкенде EdgeChromium иногда пытается залогировать нативный объект окна
# и уходит в рекурсию по дереву WinForms-контролов — косметический шум, глушим.
logging.getLogger('pywebview').setLevel(logging.CRITICAL)

from . import browsers as browsers_mod
from .scenarios import Scenario, Store

WEBUI = Path(__file__).parent / 'webui' / 'index.html'
TITLE = 'AI Vision — Лаунчер'          # «AI Vision…» Retina не смотрит: там лог с подписями

# Поля сценария, которые редактор вправе менять.
EDITABLE = ('name', 'goal', 'hints', 'browser', 'start_url', 'incognito', 'max_steps',
            'monitor', 'speed', 'noise', 'refine', 'learn', 'record', 'fast_replay')
LOG_KEEP = 3000
QUICK = 'Быстрая задача'           # исполнитель быстрых задач в учёте: без оркестратора и агентов
QUICK_STEPS = 20
# Статусы, после которых задачу можно запустить заново одним кликом
RERUNNABLE = {'aborted', 'held', 'fail', 'stuck', 'limit', 'blind', 'skipped', 'escalated', 'error'}
JOURNAL_RE = re.compile(r'^- (\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) \[([^\]]+)\] (.+?): (.*?)(?: → (.*))?$')


def _day(ts) -> date:
    try:
        return date.fromtimestamp(float(ts or 0))
    except (OverflowError, OSError, ValueError):
        return date(1970, 1, 1)


class Api:
    def __init__(self):
        self._store = Store()
        self.browsers = browsers_mod.list_browsers()
        self._window = None              # окно и трей — с «_»: pywebview обходит публичные поля API
        self._tray = None                # и полез бы в WinForms из чужого потока — окно зависало на старте
        # журнал строк для окна (команда, прогоны, службы) и уведомления-плашки
        self._lock = threading.Lock()
        self.logs: list[dict] = []
        self.notes: list[dict] = []
        self._seq = 0
        self._seen_approvals = None
        # прогон сценария
        self.runs: dict[str, str] = {}
        self.queue: list[str] = []
        self.run_log = ''
        self.log_for = None
        self.last_kind = ''
        self.last_text = ''
        self.scn_rev = 0                 # растёт при изменении набора сценариев
        # команда
        self.team_busy = False
        self.team_request = ''
        self.team_plan: list[dict] = []
        # быстрая задача (без оркестратора) — держит тот же «занят», что и команда
        self.quick = False
        self._quick_last = None          # (цель, папка прогона) последней удачной — для «Сохранить»
        # запись показом
        self.recording = False
        self._rec = None
        self._rec_hk = None
        self._rec_goal = ''

    # ── Задачи, оборванные прошлым запуском, и метка «занят» ─────────────
    def recover_orphans(self) -> int:
        """Задачи «в работе» от прошлого процесса лаунчера уже никто не выполняет (перезапуск,
        падение) — помечаем «остановлено», чтобы их можно было запустить заново одним кликом."""
        from . import workspace
        n = 0
        for t in workspace.list_tasks(status='in_progress'):
            workspace.update_task(t['id'], status='aborted', answer='прервано: лаунчер перезапустился')
            if t.get('assignee'):
                workspace.journal(t['assignee'], t.get('goal', ''), 'aborted', 'прервано: лаунчер перезапустился')
            n += 1
        if n:
            self._log('svc', f'{n} задач(и) оборвались при перезапуске — в «Что происходит» их можно запустить заново')
        return n

    def watch_busy(self, path: Path = None, period: float = 1.0):
        """Пока команда, сценарий или запись держат мышь — лежит файл-метка. Режим разработки
        (dev_launcher) видит её и не перезапускает окно посреди задачи."""
        from .paths import STATE
        path = path or STATE / 'launcher.busy'

        def loop():
            from . import services
            last = None
            while True:
                busy = bool(self.team_busy or self.runs or self.recording)
                if busy:
                    services.touch()          # идёт работа — проснувшееся зрение не гасим по простою
                if busy != last:
                    try:
                        if busy:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(self.team_request or 'сценарий', encoding='utf-8')
                        elif path.exists():
                            path.unlink()
                    except OSError:
                        pass
                    last = busy
                time.sleep(period)
        threading.Thread(target=loop, daemon=True, name='aivision-busy').start()

    # ── Журнал и уведомления ─────────────────────────────────────────────
    def _log(self, src: str, text) -> None:
        lines = [ln for ln in str(text).rstrip('\n').splitlines() if ln.strip()] or []
        with self._lock:
            for ln in lines:
                self._seq += 1
                self.logs.append({'seq': self._seq, 't': time.time(), 'src': src, 'text': ln})
            del self.logs[:-LOG_KEEP]

    def _notify(self, kind: str, title: str, text: str = '', panel: str = '') -> None:
        with self._lock:
            self._seq += 1
            self.notes.append({'seq': self._seq, 'kind': kind, 'title': title, 'text': text[:200], 'panel': panel})
            del self.notes[:-50]

    # ── Начальные данные ─────────────────────────────────────────────────
    def bootstrap(self):
        from . import services
        vendored = ''
        try:
            head = (Path(__file__).parent / 'vendor' / 'retina' / 'VENDORED.md').read_text(encoding='utf-8')
            m = re.search(r'коммит `([^`]+)`', head)
            vendored = f'встроенная Screen Retina @{m.group(1)}, порт {services.RETINA_PORT}' if m else ''
        except OSError:
            pass
        return {
            'browsers': self._browser_names(),
            'default_browser': self._store.default_browser,
            'vision': self._vision_info(),
            'monitors': self._monitor_count(),
            'services': services.status(),
            'vendored': vendored,
        }

    def _browser_names(self):
        return [''] + [b.name for b in self.browsers]

    def _monitor_count(self):
        try:
            from .capture import monitors
            return len(monitors())
        except Exception:
            return 1

    def _vision_info(self):
        return 'Большая модель — Siberi: решает, что делать на шаге; скриншот — только когда без него нельзя.'

    def ui_error(self, msg: str):
        """Ошибка в окне (JS) — в журнал и в консоль, чтобы её было видно."""
        self._log('ui', f'⚠️ окно: {msg}')
        print(f'[ui] {msg}', flush=True)
        return {'ok': True}

    def services_status(self):
        from . import services
        return services.describe()

    def siberi_login(self):
        """Окно Edge с профилем Siberi Local — войти в Google (без входа «Режим ИИ» не видит картинки)."""
        from . import services

        def work():
            self._log('svc', '🔑 открыл окно Edge для входа в Google — войдите, окно закроется само')
            st = services.siberi_login()
            self._log('svc', services.describe())
            ok = st == 'ok' and services.status().get('signed_in')
            self._notify('ok' if ok else 'warn', 'Мозг: вход в Google есть' if ok else 'Мозг: входа в Google нет',
                         services.describe(), 'vision')
        threading.Thread(target=work, daemon=True).start()
        return {'ok': True}

    def services_restart(self):
        from . import services

        def work():
            self._log('svc', '↻ перезапускаю зрение…')
            services.stop_retina()
            services._failed_at = 0.0
            st = services.start_retina(wait=20.0)
            services.start_ollama()
            self._log('svc', services.describe())
            self._notify('ok' if st == 'ok' else 'warn', 'Зрение перезапущено' if st == 'ok' else 'Зрение не поднялось',
                         services.describe(), 'vision')
        threading.Thread(target=work, daemon=True).start()
        return {'ok': True}

    def open_data_dir(self):
        from .paths import BASE
        try:
            os.startfile(str(BASE))
        except OSError:
            pass
        return {'ok': True}

    # ── Обзор ────────────────────────────────────────────────────────────
    def overview(self):
        from . import approval, pulse, services, workspace
        from .paths import RUNS
        tasks = workspace.list_tasks()
        top = [t for t in tasks if not t.get('parent')]           # запросы человека, без подзадач
        today = date.today()
        days = [today - timedelta(d) for d in range(13, -1, -1)]
        per_day = {d: 0 for d in days}
        for t in top:
            d = _day(t.get('created'))
            if d in per_day:
                per_day[d] += 1
        week_ago = time.time() - 7 * 86400
        runs_today = 0
        if RUNS.exists():
            pref = today.strftime('%Y%m%d')
            runs_today = sum(1 for p in RUNS.iterdir() if p.name.startswith(pref))
        recent = sorted(top, key=lambda t: t.get('created', 0), reverse=True)[:5]
        return {
            'series': [{'date': int(datetime(d.year, d.month, d.day).timestamp() * 1000), 'count': per_day[d]}
                       for d in days],
            'today': per_day[today],
            'done_today': sum(1 for t in top if t.get('status') == 'done'
                              and _day(t.get('updated', t.get('created'))) == today),
            'active': sum(1 for t in tasks if t.get('status') in ('new', 'in_progress')),
            'failed': sum(1 for t in top if t.get('status') in ('fail', 'aborted')
                          and float(t.get('updated', t.get('created', 0)) or 0) >= week_ago),
            'runs_today': runs_today,
            'approvals': len(approval.pending()),
            'inbox': len(pulse.pending()),
            'services': services.status(),
            'recent': [{'id': t['id'], 'goal': t.get('goal', ''), 'status': t.get('status', ''),
                        'assignee': t.get('assignee', ''), 'created': t.get('created', 0)} for t in recent],
        }

    # ── Что происходит (журнал команды) и задачи ─────────────────────────
    def journal(self, limit: int = 200):
        """Журнал команды, свежее сверху. У каждой записи — задача из company/tasks (там полный
        текст: в журнал он пишется обрезанным), чтобы её можно было запустить заново."""
        from . import workspace
        from .paths import STATE
        p = STATE / 'journal.md'
        if not p.exists():
            return []
        tasks = sorted(workspace.list_tasks(), key=lambda t: t.get('created', 0), reverse=True)
        out = []
        for line in p.read_text(encoding='utf-8', errors='replace').splitlines()[-limit:]:
            m = JOURNAL_RE.match(line.strip())
            if not m:
                continue
            d, t, status, agent, task, answer = m.groups()
            own = next((x for x in tasks if x.get('assignee') == agent
                        and (x.get('goal') or '').startswith(task.rstrip())), None)
            out.append({'date': d, 'time': t, 'status': status, 'agent': agent, 'task': task,
                        'answer': answer or '', 'tid': own['id'] if own else '',
                        'full': own.get('goal', task) if own else task,
                        'rerun': bool(own) and status in RERUNNABLE})
        return out[::-1]

    def tasks(self, limit: int = 120):
        from . import workspace
        items = sorted(workspace.list_tasks(), key=lambda t: t.get('created', 0), reverse=True)[:limit]
        return [{'id': t['id'], 'goal': t.get('goal', ''), 'status': t.get('status', ''),
                 'assignee': t.get('assignee', ''), 'parent': t.get('parent', ''),
                 'created': t.get('created', 0), 'answer': str(t.get('answer') or '')[:300]} for t in items]

    def task_detail(self, tid: str):
        from . import paths
        rep = paths.REPORTS / f'{tid}.md'
        log = paths.TASKS / tid / 'log.md'
        return {'report': rep.read_text(encoding='utf-8', errors='replace') if rep.exists() else '',
                'log': log.read_text(encoding='utf-8', errors='replace')[-4000:] if log.exists() else ''}

    # ── Нужно вам: одобрения и входящие ──────────────────────────────────
    def approvals(self):
        from . import approval
        return approval.pending()

    def approve(self, aid: str):
        from . import approval
        item = next((a for a in approval.pending() if a.get('id') == aid), None)
        if item is None:
            return {'error': 'Это одобрение уже обработано.'}
        busy = self._busy()
        if busy:
            return {'error': busy}
        approval.resolve(aid, True)
        self._start_agent_task(item.get('agent', ''), item.get('task', ''), self._task_id(item),
                               f'✅ одобрено: {item.get("task", "")[:200]}')
        return {'ok': True}

    def reject(self, aid: str):
        from . import approval
        approval.resolve(aid, False)
        self._log('team', '✗ отклонено человеком')
        return {'ok': True}

    def rerun(self, tid: str):
        """Запустить задачу агента заново (из «Что происходит» / «Задач»). Ждала «да» — это и есть «да»."""
        from . import approval, workspace
        t = workspace.read_task(tid)
        if not t or not t.get('assignee'):
            return {'error': 'Задача не найдена — запустить заново нельзя.'}
        busy = self._busy()
        if busy:
            return {'error': busy}
        if t['assignee'] == QUICK:                            # быстрая — снова быстрой, без агентов
            self._start_quick(t.get('goal', ''), tid, f'↻ заново: {t.get("goal", "")[:200]}')
            return {'ok': True}
        for a in approval.pending():                          # висит на одобрении — одобряем
            if a.get('agent') == t['assignee'] and a.get('task') == t.get('goal'):
                approval.resolve(a['id'], True)
        self._start_agent_task(t['assignee'], t.get('goal', ''), tid, f'↻ заново: {t.get("goal", "")[:200]}')
        return {'ok': True}

    def _busy(self) -> str:
        if self.quick:
            return 'Идёт быстрая задача — дождитесь окончания или остановите её.'
        if self.team_busy:
            return 'Команда сейчас занята — дождитесь окончания задачи или остановите её.'
        if self.runs:
            return 'Сейчас идёт сценарий — дождитесь окончания или остановите его.'
        return ''

    @staticmethod
    def _task_id(item: dict) -> str:
        """Задача, ради которой висело одобрение (тот же агент и тот же текст), — последняя такая."""
        from . import workspace
        same = [t for t in workspace.list_tasks()
                if t.get('assignee') == item.get('agent') and t.get('goal') == item.get('task')]
        return max(same, key=lambda t: t.get('created', 0))['id'] if same else ''

    def _start_agent_task(self, agent_name: str, task: str, tid: str, header: str):
        from . import control
        control.clear()
        self.team_busy = True
        self.team_request = task
        self.team_plan = []
        self._log('team', header)
        threading.Thread(target=self._agent_task_worker, args=(agent_name, task, tid), daemon=True).start()

    def _agent_task_worker(self, agent_name: str, task: str, tid: str):
        """Одна задача одному агенту (одобренная или повторная) — с учётом в задачах и журнале,
        как у задач из оркестратора: статус, отчёт, строка в «Что происходит»."""
        from . import orchestrator as O
        from . import workspace
        from .agents import find_agent, load_agents
        status, answer = 'fail', ''
        try:
            agent = find_agent(agent_name, load_agents())
            if agent is None:
                answer = f'агент «{agent_name}» не найден'
                self._log('team', f'⚠️ {answer}')
                return
            if not tid:
                tid = workspace.new_task(task, assignee=agent.name, created_by='Человек')
            workspace.update_task(tid, status='in_progress')
            res = O.tool_step(agent, task, '', env=self._team_env(agent), log=self._tlog)
            status, answer = res.get('status', 'done'), str(res.get('answer', '') or '')
        except Exception as e:
            status, answer = 'fail', f'ошибка: {e}'
        finally:
            self.team_busy = False
            if tid:
                workspace.update_task(tid, status=status, answer=answer)
                workspace.write_report(tid, agent_name, answer or status)
            workspace.journal(agent_name, task, status, answer)
            self._log('team', f'→ {status}: {answer[:300]}')
            self._notify('ok' if status == 'done' else 'warn',
                         'Задача выполнена' if status == 'done' else f'Задача: {status}', answer[:140], 'log')

    @staticmethod
    def _team_env(agent):
        if 'scenario' not in set(agent.tools):
            return None
        from .capture import dpi_aware
        from .screen import RealEnv
        dpi_aware()
        return RealEnv(0)                   # авто: монитор рабочего окна, не лаунчера

    def inbox(self):
        from . import pulse
        out = []
        for p in pulse.pending():
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            out.append({'name': p.name, 'task': d.get('task', ''), 'area': d.get('area', ''),
                        'why': d.get('why', ''), 'created': d.get('created', 0)})
        return out

    def _inbox_take(self, name: str, folder: str):
        from . import paths
        src = paths.INBOX / Path(name).name
        if not src.exists():
            return None
        try:
            item = json.loads(src.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            item = {}
        dst = paths.STATE / folder
        dst.mkdir(parents=True, exist_ok=True)
        src.replace(dst / src.name)
        return item

    def inbox_run(self, name: str):
        busy = self._busy()
        if busy:
            return {'error': busy}
        item = self._inbox_take(name, 'inbox-done')
        if not item or not item.get('task'):
            return {'error': 'Задача не найдена'}
        return self.team_go(item['task'], True)

    def inbox_drop(self, name: str):
        self._inbox_take(name, 'inbox-dropped')
        return {'ok': True}

    # ── Зрение (встроенная Screen Retina) ────────────────────────────────
    def vision(self, since: int = -1):
        from . import retina, services
        st = services.status()
        stats = services._get(services.retina_url() + '/stats') or {}
        out = {'status': st, 'text': services.describe(st),
               'stats': {k: stats.get(k) for k in ('events', 'seconds', 'latency_ms_p50', 'times_fewer', 'ticks')}}
        if since < 0:                                           # первое открытие: недавнее с временем
            f = retina.feed(10 ** 12)
            out.update(last=f[0] if f else 0, initial=(retina.recent() or [])[-150:], events=[])
        else:
            f = retina.feed(since)
            out.update(last=f[0] if f else since, events=(f[1] if f else [])[-200:], initial=[])
        return out

    def vision_log(self):
        from . import services
        p = services.RETINA_DIR / 'retina.log'
        if not p.exists():
            return 'Лог пуст — зрение ещё не запускалось.'
        return '\n'.join(p.read_text(encoding='utf-8', errors='replace').splitlines()[-150:]) or 'Лог пуст.'

    # ── Сценарии: чтение ─────────────────────────────────────────────────
    def list_scenarios(self):
        return [{'id': s.id, 'name': s.name, 'browser': s.browser, 'max_steps': s.max_steps,
                 'running': s.id in self.runs, 'queued': s.id in self.queue,
                 'queue_pos': (self.queue.index(s.id) + 1) if s.id in self.queue else 0}
                for s in self._store.scenarios]

    def get_scenario(self, sid):
        s = self._store.get(sid)
        if not s:
            return None
        d = asdict(s)
        d['_browsers'] = self._browser_names()
        d['_monitors'] = self._monitor_count()
        d['_log'] = self.run_log if self.log_for == sid else ''
        d['running'] = sid in self.runs
        return d

    # ── Сценарии: запись ─────────────────────────────────────────────────
    def save_scenario(self, sid, data):
        s = self._store.get(sid)
        if not s:
            return {'error': 'сценарий не найден'}
        for k in EDITABLE:
            if k in data:
                setattr(s, k, data[k])
        self._store.update(s)
        return {'ok': True}

    def new_scenario(self):
        s = self._store.add(Scenario())
        self.scn_rev += 1
        return {'id': s.id, 'name': s.name}

    def duplicate_scenario(self, sid):
        clone = self._store.duplicate(sid)
        self.scn_rev += 1
        return {'id': clone.id} if clone else None

    def delete_scenario(self, sid):
        if sid in self.runs:
            return {'error': 'Нельзя удалить сценарий, пока он выполняется.'}
        self._store.remove(sid)
        self.scn_rev += 1
        return {'ok': True}

    def set_default_browser(self, name):
        self._store.default_browser = name
        self._store.save()
        return {'ok': True}

    # ── Запуск сценария (очередь) ────────────────────────────────────────
    def run_scenario(self, sid):
        s = self._store.get(sid)
        if not s:
            return {'error': 'сценарий не найден'}
        if not s.goal:
            return {'error': 'Задайте цель сценария.'}
        if sid in self.runs or sid in self.queue:
            return {'error': 'Этот сценарий уже запущен или в очереди.'}
        if self.team_busy:                                     # мышь одна: команда её уже держит
            return {'error': 'Команда сейчас работает на экране — дождитесь окончания или остановите её.'}
        self.queue.append(sid)
        self._pump_queue()
        return {'ok': True}

    def stop_run(self):
        from . import control
        control.request_stop()
        return {'ok': True}

    def stop_all(self):
        """Кнопка «Остановить» в боковой панели: и прогон, и команда."""
        from . import control
        control.request_stop()
        self.queue.clear()
        self._log('team', '⏹ останавливаю… текущее действие завершится, новые не начнутся.')
        return {'ok': True}

    def _pump_queue(self):
        if self.runs or not self.queue:
            return
        s = self._store.get(self.queue.pop(0))
        if s is None:
            return self._pump_queue()
        from . import control
        control.clear()
        self.runs[s.id] = 'running'
        threading.Thread(target=self._run_worker, args=(s,), daemon=True).start()

    def _run_worker(self, s: Scenario):
        from . import recipe as recipe_mod
        from .capture import dpi_aware, monitors
        from .memory import Memory
        from .paths import RUNS
        from .replay import run_scenario
        from .screen import RealEnv

        logs: list[str] = []

        def log(m):
            logs.append(m)
            self.run_log = '\n'.join(logs)
            self._log('run', m)

        self.log_for = s.id
        self.run_log = ''
        self.last_kind = ''
        self.last_text = ''
        rec = None
        run_dir = RUNS / datetime.now().strftime('%Y%m%d-%H%M%S')
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f'▶ сценарий «{s.name}»')
        try:
            dpi_aware()
            mons = monitors()
            mon = s.monitor if 0 < s.monitor < len(mons) else 0
            if s.start_url:
                br = (browsers_mod.find(s.browser) or browsers_mod.find(self._store.default_browser)
                      or browsers_mod.default_browser(self.browsers))
                if not mon and len(mons) > 1:
                    mon = 1                         # не на экране лаунчера, если есть второй
                if br:
                    browsers_mod.open_on_monitor(br, s.start_url, mons[mon], incognito=s.incognito)
                else:
                    webbrowser.open(s.start_url)
                time.sleep(2.5)
            for c in range(3, 0, -1):
                self.last_text = f'старт через {c}…'
                time.sleep(1)
            env = RealEnv(mon, s.speed)               # 0 — авто: следует за рабочим окном
            memory = Memory() if s.learn else None
            if s.record:
                from .recorder import ScreenRecorder
                rec = ScreenRecorder(run_dir, env.monitor).start()
            rp = recipe_mod.load(s.id)
            r = run_scenario(s.goal, env, rp, s.fast_replay, run_dir=run_dir, log=log,
                             memory=memory, hints=s.hints, noise=s.noise,
                             on_step=rec.mark_step if rec else None,
                             max_steps=s.max_steps, refine=s.refine)
            if r.get('fresh_recipe'):
                recipe_mod.from_run(s.id, s.goal, run_dir)
                log('📝 рецепт записан — дальше этот сценарий пойдёт быстрым путём')
            self._finish_run(s, r, run_dir, log)
        except Exception as e:
            self.last_kind = 'error'
            self.last_text = f'ошибка: {e}'
            log(f'⚠️ {e}')
        finally:
            if rec:
                rec.stop()
            self.runs.pop(s.id, None)
            self._pump_queue()

    def _finish_run(self, s: Scenario, r: dict, run_dir, log):
        s.last_run = str(run_dir)
        self._store.update(s)
        words = {'done': 'готово', 'fail': 'не получилось', 'stuck': 'застрял', 'limit': 'кончились шаги',
                 'aborted': 'остановлен', 'blind': 'модель не видела экран'}
        via = {'recipe': ' · по рецепту', 'recipe+analysis': ' · рецепт + разбор'}.get(r.get('via'), '')
        self.last_kind = 'done' if r['status'] == 'done' else 'error'
        self.last_text = f"{words.get(r['status'], r['status'])}{via} · {r['steps']} шаг. · {r['seconds']} с"
        log(f"== {self.last_text} ==")
        if r.get('answer'):
            log(r['answer'])
        self._notify('ok' if r['status'] == 'done' else 'warn', f'Сценарий «{s.name}»: {self.last_text}',
                     str(r.get('answer') or '')[:140], 'scenarios')

    # ── Команда (оркестратор) ────────────────────────────────────────────
    def team_go(self, request, execute):
        request = (request or '').strip()
        if not request:
            return {'error': 'Опишите задачу.'}
        busy = self._busy() if execute else ('Команда уже занята' if self.team_busy else '')
        if busy:
            return {'error': busy}
        from . import control
        control.clear()
        self.team_busy = True
        self.team_request = request
        self.team_plan = []
        self._log('team', f'🧭 {request[:200]}')
        threading.Thread(target=self._team_worker, args=(request, bool(execute)), daemon=True).start()
        return {'ok': True}

    # ── Быстрая задача: без оркестратора — услышал, сразу смотрит на экран и делает ──
    def quick_go(self, goal):
        goal = (goal or '').strip()
        if not goal:
            return {'error': 'Опишите задачу.'}
        busy = self._busy()
        if busy:
            return {'error': busy}
        self._start_quick(goal, '', f'⚡ быстрая задача: {goal[:200]}')
        return {'ok': True}

    def _start_quick(self, goal: str, tid: str, header: str):
        from . import control
        control.clear()
        self.team_busy, self.quick = True, True
        self.team_request = goal
        self.team_plan = []
        self._log('team', header)
        threading.Thread(target=self._quick_worker, args=(goal, tid), daemon=True).start()

    def _quick_worker(self, goal: str, tid: str):
        """Один прогон движка экрана (loop.run_task) по словам человека: ни плана, ни подхода,
        ни агентов, ни чек-листа. Учёт — как у всех задач: «Что происходит», журнал, «заново»."""
        from . import workspace
        from .capture import dpi_aware
        from .loop import run_task
        from .memory import Memory
        from .paths import RUNS
        from .screen import RealEnv
        who = QUICK
        status, answer = 'fail', ''
        run_dir = RUNS / datetime.now().strftime('%Y%m%d-%H%M%S')
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            if not tid:
                tid = workspace.new_task(goal, assignee=who, created_by='Человек')
            workspace.update_task(tid, status='in_progress')
            dpi_aware()
            r = run_task(goal, RealEnv(0), max_steps=QUICK_STEPS, run_dir=run_dir, log=self._tlog,
                         memory=Memory())
            status, answer = r['status'], str(r.get('answer') or '')
            if status == 'done':
                self._quick_last = (goal, run_dir)             # «Сохранить как сценарий» возьмёт и рецепт
            self._tlog(f'== итог: {status} ==')
            if answer:
                self._tlog(answer)
        except Exception as e:
            status, answer = 'fail', f'ошибка: {e}'
            self._tlog(f'⚠️ {answer}')
        finally:
            self.team_busy, self.quick = False, False
            if tid:
                workspace.update_task(tid, status=status, answer=answer)
                workspace.write_report(tid, who, answer or status)
            workspace.journal(who, goal, status, answer)
            self._notify('ok' if status == 'done' else 'warn',
                         'Готово' if status == 'done' else f'{who}: {status}', answer[:140], 'log')

    def quick_save(self, goal):
        """Быструю задачу — в сценарии. Только что выполнена успешно — вместе с рецептом:
        повтор пойдёт быстрым путём, без пошагового разбора."""
        from . import recipe as recipe_mod
        goal = (goal or '').strip() or (self._quick_last[0] if self._quick_last else '')
        if not goal:
            return {'error': 'Опишите задачу, чтобы сохранить её как сценарий.'}
        name = goal if len(goal) <= 40 else goal[:39].rstrip() + '…'
        s = self._store.add(Scenario(name=name, goal=goal))
        with_recipe = False
        if self._quick_last and self._quick_last[0] == goal:
            try:
                with_recipe = recipe_mod.from_run(s.id, goal, self._quick_last[1]) is not None
            except Exception:
                with_recipe = False
        self.scn_rev += 1
        return {'ok': True, 'id': s.id, 'name': s.name, 'recipe': with_recipe}

    def team_stop(self):
        return self.stop_all()

    def _tlog(self, t):
        self._log('team', t)

    def _team_worker(self, request, execute):
        from . import orchestrator as O
        from .agents import load_agents
        status, answer = '', ''
        try:
            agents = load_agents()
            steps = O.plan(request, agents)
            if not steps:
                r = O.route(request, agents)
                steps = [O.Task(agent=r.agent, agent_name=r.agent.name if r.agent else '',
                                task=request, depends_on=[], why=r.why)]
            self.team_plan = [{'name': (st.agent.name if st.agent else st.agent_name),
                               'task': st.task, 'depends_on': st.depends_on, 'why': st.why or ''} for st in steps]
            self._tlog(f'план готов · {len(steps)} шаг(ов)')
            if not execute:
                self._tlog('нажмите «Выполнить», чтобы запустить план')
                return
            out = O.orchestrate(request, self._team_run_step(), agents=agents, log=self._tlog, strategy=True)
            status, answer = out['status'], str(out.get('answer') or '')
            self._tlog(f'== итог: {status} ==')
            if answer:
                self._tlog(answer)
        except Exception as e:
            status, answer = 'fail', str(e)
            self._tlog(f'⚠️ ошибка: {e}')
        finally:
            self.team_busy = False
            if execute and status:
                self._notify('ok' if status == 'done' else 'warn',
                             'Команда закончила' if status == 'done' else f'Команда: {status}', answer[:160], 'task')

    def _team_run_step(self):
        from . import orchestrator as O

        def run_step(task, context):
            agent = task.agent
            if agent is None:
                return {'status': 'fail', 'answer': f'агент «{task.agent_name}» не найден'}
            return O.tool_step(agent, task.task, context, env=self._team_env(agent), log=self._tlog)
        return run_step

    # ── Запись показом ───────────────────────────────────────────────────
    def record_start(self, goal):
        if self.recording:
            return {'error': 'Запись уже идёт'}
        from . import demo
        try:
            self._rec = demo.Recorder().start()
        except Exception as e:
            return {'error': f'Не удалось начать запись: {e}'}
        self._rec_goal = goal or 'Записанный сценарий'
        self.recording = True
        from . import services                  # подписи кликов запись берёт у зрения — будим его
        threading.Thread(target=services.wake, kwargs={'wait': 20.0}, daemon=True).start()
        try:
            from pynput import keyboard
            self._rec_hk = keyboard.GlobalHotKeys({'<ctrl>+<alt>+s': self.record_stop})
            self._rec_hk.start()
        except Exception:
            self._rec_hk = None
        self._refresh_tray()
        return {'ok': True, 'msg': 'Идёт запись. Покажите действия мышью и клавиатурой, '
                'затем нажмите Ctrl+Alt+S (или пункт трея «Стоп запись»).'}

    def record_stop(self):
        if not self.recording:
            return {'ok': True}
        self.recording = False
        try:
            if self._rec_hk:
                self._rec_hk.stop()
        except Exception:
            pass
        try:
            self._rec.stop()
        except Exception:
            pass
        evs, shots, goal = self._rec.events, self._rec.shots, self._rec_goal
        s = self._store.add(Scenario(name='🔴 ' + goal[:28], goal=goal))
        self.scn_rev += 1

        def work():
            from . import demo
            try:
                r = demo.build_recipe(evs, shots, s.id, goal)
                n = len(r.steps) if r else 0
                self.last_kind, self.last_text = 'done', f'записан сценарий: {n} шаг.'
                self._notify('ok', 'Сценарий записан', f'«{goal[:60]}» — {n} шаг(ов). Поправьте в редакторе.', 'scenarios')
            except Exception as ex:
                self.last_kind, self.last_text = 'error', f'ошибка сборки записи: {ex}'
                self._notify('warn', 'Запись не собралась', str(ex)[:140], 'scenarios')
            self.scn_rev += 1
            self._refresh_tray()
        threading.Thread(target=work, daemon=True).start()
        return {'ok': True}

    # ── Опрос из окна (раз в секунду) ────────────────────────────────────
    def poll(self, since: int = 0):
        from . import approval, pulse, services
        n_appr = len(approval.pending())
        if self._seen_approvals is not None and n_appr > self._seen_approvals:
            self._notify('warn', 'Нужно ваше «да»', 'Команда ждёт одобрения рискованного действия.', 'approvals')
        self._seen_approvals = n_appr
        with self._lock:
            logs = [x for x in self.logs if x['seq'] > since][-400:]
            notes = [x for x in self.notes if x['seq'] > since]
            last = self._seq
        return {
            'last': last, 'logs': logs, 'notes': notes,
            'running_id': next(iter(self.runs), None), 'queue': list(self.queue),
            'run_log': self.run_log, 'log_for': self.log_for,
            'last_kind': self.last_kind, 'last_text': self.last_text,
            'team_busy': self.team_busy, 'team_request': self.team_request, 'team_plan': self.team_plan,
            'quick': self.quick,
            'scn_rev': self.scn_rev, 'recording': self.recording,
            'approvals': n_appr, 'inbox': len(pulse.pending()),
            'services': services.status(),
        }

    # ── Управление окном ─────────────────────────────────────────────────
    def win_minimize(self):
        if self._window:
            self._window.minimize()
        return {'ok': True}

    def win_hide(self):
        if self._window:
            self._window.hide()
        return {'ok': True}

    def win_show(self):
        if self._window:
            self._window.show()
            self._window.restore()
            try:                                   # вперёд, даже если было под другими окнами
                from . import windows
                h = next((w['hwnd'] for w in windows.list_windows(include_own=True) if w['title'] == TITLE), 0)
                if h:
                    windows.focus(h)
            except Exception:
                pass
        return {'ok': True}

    def _refresh_tray(self):
        if self._tray:
            try:
                self._tray.update_menu()
            except Exception:
                pass


def _tray_image():
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, 61, 61], radius=16, fill=(10, 143, 126, 255))     # бирюзовый
    d.ellipse([12, 22, 52, 42], outline=(255, 255, 255, 255), width=4)           # глаз
    d.ellipse([25, 25, 39, 39], fill=(255, 255, 255, 255))
    return img


def _start_tray(api: Api):
    try:
        from pystray import Icon, Menu, MenuItem
    except ImportError:
        return None
    menu = Menu(
        MenuItem('Показать', lambda i, it: api.win_show(), default=True),
        MenuItem('Свернуть', lambda i, it: api.win_hide()),
        MenuItem('⏹ Стоп запись', lambda i, it: api.record_stop(), visible=lambda it: api.recording),
        Menu.SEPARATOR,
        MenuItem('Выход', lambda i, it: (i.stop(), api._window.destroy() if api._window else None)),
    )
    icon = Icon('aivision', _tray_image(), 'AI Vision', menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def main():
    api = Api()
    window = webview.create_window(
        TITLE, url=str(WEBUI), js_api=api, width=1320, height=860, min_size=(1000, 660),
        frameless=True, easy_drag=False, background_color='#eef0f4',
    )
    api._window = window
    api.recover_orphans()
    api.watch_busy()
    api._tray = _start_tray(api)
    from . import services, single               # Retina и локальная модель — сами, фоном
    single.serve(api.win_show)                   # повторный запуск — показать это окно

    def ready(st):
        api._log('svc', services.describe(st))
    services.start_background(ready)
    webview.start()


if __name__ == '__main__':
    main()
