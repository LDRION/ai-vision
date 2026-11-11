"""
Оркестратор — распределяет задачи между агентами-специалистами.

Две операции, обе через один запрос к Siberi:
  • route(request)  — выбрать ОДНОГО агента под запрос (и почему);
  • plan(request)   — разложить запрос на упорядоченные подзадачи, каждая закреплена
                      за агентом. Шаги могут зависеть от предыдущих (передача результата).

Оркестратор НИЧЕГО не исполняет сам — он только маршрутизирует. Исполнение подзадачи —
дело агента (его tools: scenario — движок управления экраном, groq — слух и т. д.).
"""

from dataclasses import dataclass
from typing import List, Optional

from .agents import Agent, find_agent, load_agents


@dataclass
class Routed:
    agent: Optional[Agent]
    why: str
    raw: dict


@dataclass
class Task:
    agent: Optional[Agent]
    agent_name: str
    task: str
    depends_on: List[int]          # индексы шагов, от которых зависит (передача результата)
    why: str = ''


def _agents_block(agents: List[Agent]) -> str:
    return '\n'.join(f'- {a.name}: {a.description} (умеет: {", ".join(a.capabilities) or "—"})'
                     for a in agents)


ROUTE_SYSTEM = (
    'Ты — оркестратор команды ИИ-агентов. По запросу пользователя выбери ОДНОГО агента из списка, '
    'который лучше всего подходит. Если ни один не подходит — верни agent: null и объясни. '
    'Ответ строго один JSON-объект: {"agent": "<имя агента или null>", "why": "<коротко почему>"}'
)

PLAN_SYSTEM = (
    'Ты — оркестратор команды ИИ-агентов. Разложи запрос пользователя на минимальную '
    'последовательность подзадач и закрепи каждую за подходящим агентом из списка. '
    'Не дроби без нужды: если хватает одного шага — один шаг. Если шаг использует результат '
    'предыдущего — укажи его номер в depends_on. Задачу агенту формулируй как ясный промт. '
    'ВАЖНО: любое действие В ИНТЕРФЕЙСЕ — открыть программу (Telegram, браузер), загрузить файл '
    '(видео на YouTube), опубликовать пост, отправить сообщение, нажать кнопку — поручай агенту, '
    'который умеет screen/web (управляет мышью и клавиатурой). Если предыдущий шаг сделал файл, '
    'передай его путь в задаче экранному агенту (из depends_on). '
    'Если экранный шаг — простое действие с известной подписью, пиши его командой с подписью '
    'в «ёлочках»: «Нажми «Опубликовать»», «Введи «текст» в поле «Сообщение»», «Нажми Enter» '
    '(цепочку — через «затем»). Такие шаги выполняются мгновенно, без разбора экрана моделью. '
    'Где подпись неизвестна или нужно подумать (найди, выбери подходящее) — обычный текст. '
    'depends_on — ВСЕГДА список (например [] или [0]), не оставляй пустым. '
    'Ссылки НЕ выдумывай: адрес сайта или кабинета бери только из блока «АДРЕСА ПО ЗАПРОСУ» или реестра '
    '(например, Метрика проекта — её адрес со счётчиком, а не https://yandex.ru); нет там — не пиши ссылку, '
    'назови сервис. '
    'Шаги, которые НЕ зависят друг от друга, НЕ связывай depends_on — они пойдут параллельно '
    '(например, обложку можно делать одновременно с монтажом). Связывай только настоящую зависимость '
    '(когда шагу нужен результат другого). '
    'Ответ строго один JSON-объект без текста вокруг: '
    '{"steps": [{"agent": "<имя>", "task": "<что сделать>", "depends_on": [<номера с 0>], "why": "<коротко>"}]}'
)


def _prompt(request: str, agents: List[Agent]) -> str:
    from . import knowledge, resources
    know = knowledge.context()
    res = resources.context()
    pre = '\n\n'.join(b for b in (know, res) if b)
    links = resources.relevant(request)
    addr = ('АДРЕСА ПО ЗАПРОСУ (точные, из реестра — используй ровно их):\n'
            + '\n'.join(f'- {x}' for x in links) + '\n\n') if links else ''
    return (f'{pre}\n\n' if pre else '') + \
        f'АГЕНТЫ:\n{_agents_block(agents)}\n\n{addr}ЗАПРОС: {request}\n\nОтветь одним JSON.'


def route(request: str, agents: Optional[List[Agent]] = None, ask=None) -> Routed:
    from .agent import parse_json
    agents = agents if agents is not None else load_agents()
    ask = ask or _default_ask
    data = parse_json(ask(ROUTE_SYSTEM, _prompt(request, agents)))
    name = data.get('agent')
    return Routed(agent=find_agent(name, agents) if name else None,
                  why=data.get('why', ''), raw=data)


GUI_WORDS = ('открой', 'открыть', 'загрузи', 'залей', 'выложи', 'опубликуй', 'запость', 'постни',
             'отправь в', 'нажми', 'кликни', 'зайди на', 'upload', 'publish', 'post ')
CAP_WORDS = {'groq': ('расшифр', 'аудио', 'транскри', 'голос', 'послуша'),
             'image': ('картинк', 'изображен', 'обложк', 'шапк', 'иллюстрац', 'нарисуй'),
             'video': ('видео', 'ролик', 'монтаж', 'смонтируй', 'шортс'),
             'text': ('текст', 'пост', 'напиши', 'сценарий', 'описани', 'промт')}


def _best_agent(task: str, agents: List[Agent]) -> Optional[Agent]:
    """Подобрать агента под задачу по инструменту/ключевым словам — без обращения к модели."""
    low = (task or '').lower()
    if any(w in low for w in GUI_WORDS):               # действие в интерфейсе → экранный агент
        scr = [a for a in agents if 'scenario' in a.tools]
        pub = [a for a in scr if 'web' in a.capabilities]
        return (pub or scr or [None])[0]
    for tool, words in CAP_WORDS.items():
        if any(w in low for w in words):
            cand = [a for a in agents if tool in a.tools]
            if cand:
                return cand[0]
    return next((a for a in agents if 'scenario' in a.tools), agents[0] if agents else None)


def _repair_steps(steps: List['Task'], agents: List[Agent]):
    """Починить план: несуществующий агент или GUI-шаг не у экранного → переназначить."""
    for s in steps:
        low = s.task.lower()
        gui = any(w in low for w in GUI_WORDS)
        if s.agent is None or (gui and 'scenario' not in s.agent.tools):
            better = _best_agent(s.task, agents)
            if better is not None:
                s.agent, s.agent_name = better, better.name


# Шаг опирается на чужой результат («изучи полученную от Аналитика сводку»), а планировщик
# не связал его depends_on — тогда шаги идут параллельно, и второй получает пустой контекст.
USES_RESULT = ('полученн', 'результат', 'сводк', 'по итогам', 'на основе', 'на основании', 'из предыдущ',
               'данные от', 'данных от', 'собранн', 'найденн', 'выгруженн', 'отчет от')


def _repair_deps(steps: List['Task']):
    for i, s in enumerate(steps):
        if i == 0 or s.depends_on:
            continue
        low = s.task.lower().replace('ё', 'е')
        named = [j for j in range(i) if len(steps[j].agent_name) >= 4
                 and steps[j].agent_name.lower().replace('ё', 'е')[:6] in low]
        if named:
            s.depends_on = named
        elif any(w in low for w in USES_RESULT):
            s.depends_on = [i - 1]


def plan(request: str, agents: Optional[List[Agent]] = None, ask=None) -> List[Task]:
    from .agent import parse_json
    agents = agents if agents is not None else load_agents()
    ask = ask or _default_ask
    data = parse_json(ask(PLAN_SYSTEM, _prompt(request, agents)))
    steps = []
    for s in data.get('steps') or []:
        name = str(s.get('agent') or '').strip()
        dep = s.get('depends_on') or []
        steps.append(Task(
            agent=find_agent(name, agents), agent_name=name, task=str(s.get('task', '')).strip(),
            depends_on=[int(d) for d in dep if isinstance(d, (int, float))], why=s.get('why', ''),
        ))
    _repair_steps(steps, agents)                        # устойчивость к огрехам планировщика
    _repair_deps(steps)
    from . import resources
    for s in steps:
        s.task = resources.fix_urls(s.task)             # «по ссылке https://yandex.ru» → адрес Метрики
    return steps


CREATE_SYSTEM = (
    'Ты — главный оркестратор команды ИИ. По запросу опиши НОВОГО агента-сотрудника: '
    'кто он, что умеет, в каком отделе, какими инструментами пользуется и его системную роль. '
    'capabilities — из: screen (видит экран и управляет мышью/клавиатурой), hearing (распознаёт речь), '
    'image (генерирует картинки), text (пишет тексты/промты), web (работает в браузере). '
    'tools — из: scenario (движок управления экраном), groq (слух), text (пишет тексты), '
    'image (генерирует картинки), video (монтаж ролика), ssh (работа на хостинге по SSH), '
    'orchestrate (может руководить отделом). '
    'Ответ строго один JSON-объект: {"name": "<имя>", "description": "<кратко>", '
    '"department": "<отдел>", "capabilities": ["…"], "tools": ["…"], "app": "<приложение или пусто>", '
    '"reports_to": "<руководитель или пусто>", "prompt": "<системная роль агента: кто он и как работает>"}'
)


def propose_agent(request: str, context: str = '', ask=None) -> dict:
    """
    Спроектировать нового агента по запросу (одна Siberi-заявка). Возвращает
    спецификацию — её передают в agents.write_agent, чтобы создать манифест.
    context — например, текущая оргструктура (team.org_chart), чтобы не плодить дубли.
    """
    from .agent import parse_json
    ask = ask or _default_ask
    user = (f'ТЕКУЩАЯ КОМАНДА:\n{context}\n\n' if context else '') + f'ЗАПРОС: {request}\n\nОтветь одним JSON.'
    spec = parse_json(ask(CREATE_SYSTEM, user))
    return {k: spec.get(k) for k in ('name', 'description', 'department', 'capabilities',
                                     'tools', 'app', 'reports_to', 'prompt') if spec.get(k) is not None}


def _escalate(task: 'Task', agent_name: str, reason: str, log=print, agents=None) -> str:
    """
    Шаг провалился после повторов — передать выше: лиду отдела, иначе человеку.
    Кладём задачу-эскалацию во входящие (её разберёт лид/оркестратор/человек).
    """
    from . import pulse
    from .team import build_departments
    lead = None
    for d in build_departments(agents):
        names = [m.name for m in d.roster()]
        if agent_name in names and d.lead and d.lead.name != agent_name:
            lead = d.lead.name
            break
    to = lead or 'человек'
    pulse.enqueue(f'[Эскалация от «{agent_name}» → {to}] Не удалось: {task.task}. Причина: {reason[:120]}. '
                  f'Прими решение или назначь иначе.', area='Эскалации', why='шаг провалился после повторов')
    log(f'  ⤴ эскалация {to}: {task.task[:50]}')
    return f'передано {to}'


def orchestrate(request: str, run_step, agents: Optional[List[Agent]] = None,
                ask=None, log=print, confirm=None, strategy=False, max_parallel: int = 3,
                retries: int = 1, use_memory: bool = True) -> dict:
    """
    Сквозной прогон: план → задачи в рабочем пространстве → исполнение агентами → сведение.
    Это и есть «оркестратор работает»: один вход (запрос человека) — один результат.

    run_step(task, context) -> dict — как исполнить подзадачу (обычно tool_step).
    confirm(agent_name, task) -> bool — подтверждение человека для необратимых/денежных
    шагов; нет подтверждения — шаг не исполняется, кладётся в company/approvals. Каждый
    шаг пишется в журнал (company/state/journal.md).

    → {'parent': <id родительской задачи>, 'status', 'answer', 'steps': [...]}
    """
    from . import approval, plan_memory, workspace
    agents = agents if agents is not None else load_agents()

    steps = plan_memory.recall(request, agents) if use_memory else None
    if steps:                                           # похожую задачу уже решали — берём план из памяти
        log(f'  🧠 план из памяти ({len(steps)} шаг.) — без повторного планирования')
    else:
        plan_request = request
        if strategy:                                    # обсудить подход до плана (п.4)
            note = strategize(request, ask)
            if note:
                log(f'  🎯 подход: {note[:120]}')
                plan_request = f'{request}\n\nПОДХОД (следуй ему): {note}'
        steps = plan(plan_request, agents, ask)
        if not steps:                                   # план пуст — направим одному агенту
            r = route(request, agents, ask)
            steps = [Task(agent=r.agent, agent_name=r.agent.name if r.agent else '',
                          task=request, depends_on=[], why=r.why)]
    log(f'  🧭 план на {len(steps)} шаг(ов): ' + ' → '.join(
        f'[{s.agent.name if s.agent else s.agent_name}] {s.task[:90]}' for s in steps))

    parent = workspace.new_task(request, created_by='Оркестратор')

    def tracked(task: Task, context: str) -> dict:
        from . import control
        if control.stopped():
            return {'status': 'aborted', 'answer': 'остановлено пользователем'}
        name = task.agent.name if task.agent else task.agent_name
        tid = workspace.new_task(task.task, assignee=name, parent=parent, created_by='Оркестратор')
        # Человек в контуре: необратимое/денежное не делаем без явного «да»
        if approval.is_risky(task.task) and not (confirm and confirm(name, task.task)):
            approval.request(name, task.task, why='необратимое/денежное действие')
            workspace.update_task(tid, status='held', answer='ждёт подтверждения человека')
            workspace.journal(name, task.task, 'held', 'на подтверждении')
            log(f'  ✋ шаг удержан — нужно подтверждение человека: {task.task[:140]}')
            return {'status': 'held', 'answer': 'ждёт подтверждения человека'}
        # Разбор оркестратора: серьёзное действие нижнего агента (создать бота, выдать
        # права, привязать аккаунт, настроить платежи/канал) — только с визой человека.
        from . import governance
        if governance.review(task.agent, task.task) == 'approve' and not (confirm and confirm(name, task.task)):
            approval.request(name, task.task, why='серьёзное действие — нужна виза оркестратора/человека')
            workspace.update_task(tid, status='held', answer='ждёт визы оркестратора/человека')
            workspace.journal(name, task.task, 'held', 'на визе оркестратора')
            log(f'  🛡 шаг на визе оркестратора: {task.task[:140]}')
            return {'status': 'held', 'answer': 'ждёт визы оркестратора/человека'}
        # Ритм «без фанатизма»: публикация/видео сверх дневного лимита — не делаем
        from . import policy
        act = policy.action_of(task.task)
        if act and not policy.allow(act):
            workspace.update_task(tid, status='skipped', answer=f'дневной лимит «{act}» исчерпан')
            workspace.journal(name, task.task, 'skipped', f'лимит {act}')
            log(f'  🚦 {act}: дневной лимит исчерпан — пропуск')
            return {'status': 'skipped', 'answer': f'сегодня лимит «{act}» уже использован'}
        workspace.update_task(tid, status='in_progress')
        res = run_step(task, context)
        # Ретрай при сбое инструмента (fail/error) — вдруг временное
        attempt = 0
        while res.get('status') in ('fail', 'error') and attempt < retries:
            attempt += 1
            log(f'  ↻ шаг не удался ({res.get("answer", "")[:50]}) — повтор {attempt}/{retries}')
            res = run_step(task, context)
        status = res.get('status', 'done')
        # Не вышло и после повторов — эскалация лиду/человеку, а не молчаливый провал
        if status in ('fail', 'error'):
            esc = _escalate(task, name, res.get('answer', ''), log, agents)
            res = {**res, 'answer': res.get('answer', '') + f'\n[эскалация: {esc}]'}
            status = 'escalated'
        if act and status == 'done':                    # учли выполненное в дневном ритме
            policy.record(act)
        workspace.update_task(tid, status=status, answer=res.get('answer', ''))
        workspace.write_report(tid, name, res.get('answer', '') or status)
        workspace.journal(name, task.task, status, res.get('answer', ''))
        return {**res, 'status': status}

    out = dispatch(steps, tracked, log, max_parallel=max_parallel)
    workspace.update_task(parent, status=out['status'], answer=out['answer'])
    workspace.log_task(parent, f'итог: {out["status"]}')
    if use_memory and out['status'] == 'done' and len(steps) >= 2:
        plan_memory.remember(request, steps)            # удачный план — запомнить на будущее
    return {'parent': parent, 'status': out['status'], 'answer': out['answer'], 'steps': out['results']}


def scenario_step(agent: Agent, task: str, context: str, env, log=print, **run_kwargs) -> dict:
    """
    Исполнить подзадачу агентом через движок управления экраном: роль агента
    (с учётом правок и «карты места») идёт в системный промт, задача — как цель.
    Для агентов с инструментом scenario. Остальные инструменты (groq, image…) —
    свои адаптеры по тому же контракту (task, context) → {'status','answer'}.
    """
    from . import knowledge, resources, workspace
    from .loop import run_task
    role = agent.effective_prompt()
    card = workspace.card(agent)
    know = knowledge.context()
    task = resources.fix_urls(task)                      # и у старых задач («заново»): адрес по реестру
    goal = task if not context else f'{task}\n\nКОНТЕКСТ ОТ ПРЕДЫДУЩИХ ШАГОВ:\n{context}'
    links = resources.relevant(task)
    if links:                                            # точный адрес — открыть его, а не искать
        goal += ('\n\nАДРЕСА ИЗ РЕЕСТРА ПРОЕКТОВ (открывай ровно их — в адресной строке браузера):\n'
                 + '\n'.join(f'- {x}' for x in links))
    items = checklist(task)                              # задача-настройка → конкретный чек-лист в цель
    if items:
        goal += ('\n\nЧЕК-ЛИСТ (пройди по пунктам, не пропускай; «Сохранить» — ОТДЕЛЬНЫМ шагом после группы):\n'
                 + '\n'.join(f'{i}. {it}' for i, it in enumerate(items, 1)))
        run_kwargs.setdefault('max_steps', min(40, 8 + len(items) * 2))   # шагов под размер чек-листа
        log(f'  📋 чек-лист на {len(items)} пункт(ов), до {run_kwargs["max_steps"]} шагов')
    system = f'{role}\n\n{card}' + (f'\n\n{know}' if know else '')
    # Простая команда («Нажми «Опубликовать»») — рефлексом, без модели; контекст ей не нужен.
    # Задача-настройка с чек-листом — всегда через модель.
    run_kwargs.setdefault('command', '' if items else task)
    return run_task(goal, env, role=system, log=log, **run_kwargs)


import re

AUDIO_RE = re.compile(r'[^\s"\'<>]+\.(?:wav|mp3|ogg|webm|m4a|flac)', re.IGNORECASE)

# Задачи-настройки: вместо расплывчатого «настрой по уму» оркестратор раскладывает
# конкретный чек-лист — чтобы экранный агент не бродил, а шёл по пунктам.
CONFIG_RE = re.compile(r'настро|сконфигур|оформ|заполн|наведи\s+порядок|приведи\s+в\s+порядок|по\s+уму', re.IGNORECASE)
# Поисковый мозг (Google-режим) отказывает на «команды… ответь JSON», но отвечает на
# обычный вопрос прозой. Поэтому спрашиваем по-человечески, а список парсим из строк.
CHECKLIST_SYSTEM = 'Ты даёшь практичный список настроек: по одному конкретному пункту в строке, без вступлений и воды.'


def _parse_items(text: str) -> list:
    out = []
    for line in (text or '').splitlines():
        line = re.sub(r'^\s*(?:\d+[.)]|[-•*–—·])\s*', '', line.strip()).strip(' .')
        if 3 <= len(line) <= 140 and not line.endswith(':'):
            out.append(line)
    return out[:15]


def checklist(task: str, ask=None) -> list:
    """Для задачи-настройки — конкретный чек-лист пунктов (одна заявка к мозгу). Иначе []."""
    if not CONFIG_RE.search(task or ''):
        return []
    ask = ask or _default_ask
    user = f'Что именно нужно настроить: {task}. Дай подробный список конкретных пунктов, по одному в строке.'
    try:
        raw = ask(CHECKLIST_SYSTEM, user)
    except Exception:
        return []
    if '{' in raw and 'items' in raw:                   # умный мозг (Claude) может вернуть JSON
        try:
            from .agent import parse_json
            items = [str(x).strip() for x in (parse_json(raw).get('items') or []) if str(x).strip()]
            if items:
                return items[:15]
        except Exception:
            pass
    return _parse_items(raw)


def groq_step(agent: Agent, task: str, context: str, log=print, **_) -> dict:
    """
    Инструмент «слух»: находит в задаче/контексте путь к аудиофайлу, распознаёт речь
    через Groq и, если просили, делает выжимку/список задач. Контракт тот же, что у
    scenario_step: (task, context) → {'status', 'answer'}.
    """
    import os

    from . import hearing
    cand = next((m.group(0) for text in (task, context) for m in AUDIO_RE.finditer(text or '')
                 if os.path.exists(m.group(0))), None)
    if not cand:
        return {'status': 'fail', 'answer': 'агенту-слуху нужен путь к аудиофайлу в задаче'}
    log(f'  🎧 распознаю {cand}')
    text = hearing.transcribe(cand)
    low = (task or '').lower()
    if any(w in low for w in ('задач', 'выжим', 'сводк', 'кратко', 'суть', 'список')):
        text = f'{text}\n\n[Выжимка]\n{hearing.summarize(text)}'
    return {'status': 'done', 'answer': text}


def image_step(agent: Agent, task: str, context: str, log=print, **_) -> dict:
    """
    Инструмент «картинка»: генерирует изображение по промту (шапка, иллюстрация)
    через Stable Diffusion WebUI и кладёт файл в company/outbox. task — описание
    картинки; при желании агент-художник сам уточнит промт заранее.
    """
    import time

    from . import imaging, paths
    if not imaging.enabled():
        return {'status': 'fail',
                'answer': 'Stable Diffusion WebUI не запущен с --api (см. imaging.py)'}
    prompt = f'{task}. {context}'.strip() if context else task
    paths.OUTBOX.mkdir(parents=True, exist_ok=True)
    out = paths.OUTBOX / f'image-{time.strftime("%Y%m%d-%H%M%S")}.png'
    log(f'  🎨 генерирую картинку → {out.name}')
    path = imaging.generate(prompt, out)
    return {'status': 'done', 'answer': f'картинка готова: {path}'}


def text_step(agent: Agent, task: str, context: str, log=print, **_) -> dict:
    """
    Инструмент «текст»: агент-копирайтер пишет текст или ПРОМТ для другого агента
    через Siberi. Роль агента (с правками и «картой места») — системный промт.
    """
    from . import knowledge, workspace, writing
    know = knowledge.context()
    system = f'{agent.effective_prompt()}\n\n{workspace.card(agent)}' + (f'\n\n{know}' if know else '')
    log('  ✍️ пишу текст…')
    text = writing.write(task, material=context, system=system)
    return {'status': 'done', 'answer': text}


def video_step(agent: Agent, task: str, context: str, duration: int = 30, log=print, **_) -> dict:
    """
    Инструмент «видео»: умный МОНТАЖ ролика из материала (dumaem edit) — биты, лица,
    движение, титры, рендер 1080×1920. task/context могут содержать длину ролика
    и явные файлы материала; иначе берётся свежий материал из media/.
    """
    import re

    from . import video
    if not video.enabled():
        return {'status': 'fail', 'answer': 'монтажёр dumaem/ffmpeg не готов (см. video.py)'}
    blob = f'{task} {context}'
    m = re.search(r'(\d{1,3})\s*(?:сек|s\b|секунд)', blob)
    dur = int(m.group(1)) if m else duration
    files = [p for p in re.findall(r'[^\s"\'<>]+\.(?:mp4|mov|mkv|mp3|wav|m4a)', blob, re.I)
             if __import__('os').path.exists(p)]
    log(f'  🎬 монтирую ролик ({dur} с){" из " + str(len(files)) + " файлов" if files else " из свежего материала"}')
    path = video.edit(material=files or None, duration=dur)
    return {'status': 'done', 'answer': f'ролик смонтирован: {path}'}


def remote_step(agent: Agent, task: str, context: str, host: str = '', confirm=None,
                log=print, **_) -> dict:
    """
    Инструмент «ssh»: выполнить команду на сервере (хостинг Откликс/Siberi).
    Команду берём из задачи (в обратных кавычках `…` или после 'команда:'); если
    её нет — это не для ssh. Изменяющие команды (deploy, rm, git push, рестарт) —
    только с подтверждением человека; чтение выполняется.
    """
    import re as _re

    from . import remote
    known = [k for k in remote.hosts() if k != 'default']
    hint = next((k for k in known if _re.search(rf'\b{_re.escape(k)}\b', task, _re.IGNORECASE)), '')
    h = host or hint or agent.app or ''                # хост: из задачи, из поля app агента, или дефолтный
    if not remote.enabled(h):
        return {'status': 'fail', 'answer': 'SSH-хост не настроен (company/state/hosts.json)'}
    m = _re.search(r'`([^`]+)`', task) or _re.search(r'команд[аы]\s*[:\-]\s*(.+)', task, _re.IGNORECASE)
    command = (m.group(1).strip() if m else '').strip()
    if not command:
        return {'status': 'fail', 'answer': 'для ssh укажи команду в задаче (в `кавычках`)'}
    if remote.is_write(command) and not (confirm and confirm(agent.name, f'ssh: {command}')):
        from . import approval
        approval.request(agent.name, f'ssh {h}: {command}', why='изменяющая команда на сервере')
        return {'status': 'held', 'answer': f'команда меняет сервер — нужно подтверждение: {command}'}
    log(f'  🖥 ssh {h}: {command[:60]}')
    r = remote.run(h, command)
    body = r['out'] or r['err'] or '(пусто)'
    return {'status': 'done' if r['rc'] == 0 else 'fail', 'answer': f'$ {command}\n{body[:1500]}'}


def tool_step(agent: Agent, task: str, context: str, env=None, log=print, confirm=None, **run_kwargs) -> dict:
    """Выбрать адаптер по инструменту агента и исполнить подзадачу."""
    tools = set(agent.tools)
    if 'ssh' in tools:
        return remote_step(agent, task, context, confirm=confirm, log=log)
    if 'groq' in tools:
        return groq_step(agent, task, context, log=log)
    if 'image' in tools:
        return image_step(agent, task, context, log=log)
    if 'text' in tools:
        return text_step(agent, task, context, log=log)
    if 'video' in tools:
        return video_step(agent, task, context, log=log)
    if 'scenario' in tools:
        if env is None:
            return {'status': 'fail', 'answer': 'для scenario-агента нужен экран (env не задан)'}
        return scenario_step(agent, task, context, env, log=log, **run_kwargs)
    return {'status': 'fail', 'answer': f'инструмент {", ".join(tools) or "—"} ещё не подключён'}


STRATEGY_SYSTEM = (
    'Ты — главный оркестратор команды ИИ. Перед многошаговой задачей коротко определи ПОДХОД: '
    'какой результат нужен, в каком тоне/стиле (с учётом знаний команды), на что обратить внимание, '
    'чего избежать. 2–4 предложения, по делу. Это ориентир для плана и агентов.'
)


def strategize(request: str, ask=None, record: bool = True) -> str:
    """Короткая стратегия/подход к задаче (один запрос). Записывается в журнал решений."""
    from . import knowledge
    ask = ask or _default_ask
    know = knowledge.context()
    user = (f'{know}\n\n' if know else '') + f'ЗАДАЧА: {request}\n\nОпиши подход (2–4 предложения).'
    try:
        note = ask(STRATEGY_SYSTEM, user).strip()
    except Exception:
        return ''
    note = re.sub(r'^[*#\s]*подход[*\s]*:[*\s]*', '', note, flags=re.IGNORECASE)   # своё «ПОДХОД:» — в журнале уже есть
    if record and note:
        knowledge.record_decision(f'[{request[:50]}] {note}')
    return note


def _default_ask(system: str, user: str) -> str:
    from . import siberi
    return siberi.ask(system, user)


def dispatch(steps: List[Task], run_step, log=print, max_parallel: int = 3) -> dict:
    """
    Исполнить план, соблюдая зависимости (depends_on) и передавая результат дальше.
    Независимые шаги идут ПАРАЛЛЕЛЬНО — до max_parallel одновременно (Siberi держит
    несколько запросов разом, но перегружать не стоит; 1 = строго по очереди).
    Шаг, чья зависимость не 'done', пропускается; независимые продолжают.

    → {'results': [...], 'status': 'done' | 'partial', 'answer': '<итог последнего шага>'}
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    n = len(steps)
    results: List[Optional[dict]] = [None] * n
    pending = set(range(n))
    deps_of = lambda i: [d for d in steps[i].depends_on if 0 <= d < n]

    def context_for(i) -> str:
        return '\n'.join(f'Результат шага {d}: {results[d].get("answer", "")}' for d in deps_of(i))

    def safe(st, ctx) -> dict:
        try:
            return run_step(st, ctx)
        except Exception as e:                      # сбой одного шага не рушит весь план
            return {'status': 'error', 'answer': str(e)}

    with ThreadPoolExecutor(max(1, max_parallel)) as ex:
        futures = {}
        while pending or futures:
            for i in sorted(pending):
                st = steps[i]
                deps = deps_of(i)
                if not all(results[d] is not None for d in deps):
                    continue                        # зависимости ещё не готовы
                pending.discard(i)
                if st.agent is None:
                    results[i] = {'status': 'skipped', 'answer': f'нет агента «{st.agent_name}»'}
                    log(f'  ⚠️ шаг {i}: агент «{st.agent_name}» не найден — пропуск')
                elif any(results[d].get('status') != 'done' for d in deps):
                    results[i] = {'status': 'skipped', 'answer': 'зависимость не выполнена'}
                    log(f'  ⤼ шаг {i}: зависимость не удалась — пропуск')
                else:
                    log(f'  ▶ шаг {i}: [{st.agent.name}] {st.task[:140]}')
                    futures[ex.submit(safe, st, context_for(i))] = i
            if not futures:                         # остались только заблокированные — пропускаем
                for i in list(pending):
                    results[i] = {'status': 'skipped', 'answer': 'не запущено'}
                pending.clear()
                break
            for f in wait(list(futures), return_when=FIRST_COMPLETED).done:
                i = futures.pop(f)
                results[i] = f.result()
                log(f'    → шаг {i}: {results[i].get("status")}: {str(results[i].get("answer", ""))[:160]}')

    results = [r if r is not None else {'status': 'skipped', 'answer': ''} for r in results]
    done = all(r.get('status') == 'done' for r in results) if results else False
    answer = results[-1].get('answer', '') if results else ''
    return {'results': results, 'status': 'done' if done else 'partial', 'answer': answer}
