"""
Пульс компании — работа в потоке 24/7. На каждом «тике» оркестратор оценивает,
где у нас просадка (воронки, контент, соцсети, картинки-шапки, обратная связь),
и ставит ОДНУ конкретную задачу во входящие (company/inbox). Отдельно входящие
разбираются: каждая задача уходит в сквозной orchestrate.

Так механизм сам замечает, чего не хватает, и подкидывает работу — а не ждёт
человека. Человек задаёт направления (AREAS) и ритм (интервал).
"""

import json
import time
from pathlib import Path
from typing import Callable, List, Optional

from . import paths

# Направления внимания. Человек правит этот список под свои приоритеты.
AREAS = [
    'Воронки и лиды: хватает ли свежих заказов, не простаивает ли радар',
    'Контент: выходят ли посты и ролики, нет ли простоя в публикациях',
    'Картинки-шапки: есть ли обложки под свежий контент',
    'Обратная связь: отвечено ли на сообщения и комментарии',
    'Проекты на хостинге (Откликс, siberi.online): всё ли живо, нет ли простоя/ошибок, что доработать',
    'Аналитика: есть ли свежий замер метрик (Яндекс Метрика, Google Analytics), что проседает по данным',
]

ASSESS_SYSTEM = (
    'Ты — оркестратор компании ИИ, следишь за потоком работы 24/7. По списку направлений '
    'и недавней активности определи ОДНО место с самой заметной просадкой и сформулируй одну '
    'конкретную задачу, которую стоит сделать прямо сейчас. Если всё в норме и новая задача '
    'сейчас не нужна — верни need:false. Ответ строго один JSON-объект: '
    '{"need": true|false, "area": "<направление>", "task": "<конкретная задача>", "why": "<коротко>"}'
)


def recent_activity(limit: int = 12) -> str:
    """Короткая сводка недавних задач — чтобы оценка опиралась на факты, а не на пустоту."""
    from . import workspace
    tasks = sorted(workspace.list_tasks(), key=lambda t: t.get('created', 0), reverse=True)[:limit]
    if not tasks:
        return '(задач ещё не было)'
    return '\n'.join(f'- [{t.get("status")}] {t.get("goal", "")[:70]}' for t in tasks)


def assess(areas: Optional[List[str]] = None, ask=None) -> dict:
    """Спросить оркестратора, где просадка и какую задачу поставить. → dict."""
    from .agent import parse_json
    from . import siberi
    from . import projects
    areas = areas or AREAS
    ask = ask or (lambda s, u: siberi.ask(s, u))
    user = ('НАПРАВЛЕНИЯ:\n' + '\n'.join(f'- {a}' for a in areas)
            + f'\n\nПРОЕКТЫ:\n{projects.summary()}'
            + f'\n\nНЕДАВНЯЯ АКТИВНОСТЬ:\n{recent_activity()}\n\nОтветь одним JSON.')
    try:
        return parse_json(ask(ASSESS_SYSTEM, user))
    except Exception:
        return {'need': False}


def enqueue(task: str, area: str = '', why: str = '') -> str:
    """Положить задачу во входящие. → id файла во входящих."""
    paths.INBOX.mkdir(parents=True, exist_ok=True)
    iid = time.strftime('%Y%m%d-%H%M%S')
    (paths.INBOX / f'{iid}.json').write_text(
        json.dumps({'task': task, 'area': area, 'why': why, 'created': time.time()},
                   ensure_ascii=False, indent=1), encoding='utf-8')
    return iid


def pending() -> List[Path]:
    return sorted(paths.INBOX.glob('*.json')) if paths.INBOX.exists() else []


def tick(ask=None, areas: Optional[List[str]] = None, log=print) -> Optional[str]:
    """Один такт оценки: нашлась просадка → поставить задачу во входящие. → id или None."""
    a = assess(areas, ask)
    if not a.get('need') or not a.get('task'):
        log('  ✓ пульс: просадок нет, новых задач не ставлю')
        return None
    iid = enqueue(a['task'], a.get('area', ''), a.get('why', ''))
    log(f'  ➕ пульс: задача во входящих [{a.get("area", "")}]: {a["task"][:70]}')
    return iid


def drain_inbox(run_request: Callable[[str], dict], log=print) -> List[dict]:
    """Разобрать входящие: каждую задачу — в работу, файл убрать в state/done. → результаты."""
    done_dir = paths.STATE / 'inbox-done'
    done_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for p in pending():
        try:
            item = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        log(f'  ▶ вход: {item.get("task", "")[:70]}')
        res = run_request(item['task'])
        results.append({'task': item.get('task', ''), 'status': res.get('status')})
        p.rename(done_dir / p.name)
    return results


def run(interval: float, ask=None, run_request: Optional[Callable[[str], dict]] = None,
        areas: Optional[List[str]] = None, log=print, stop=None):
    """
    Цикл пульса: каждые interval секунд — оценка и, если задан run_request, разбор входящих.
    stop() → True прерывает цикл. Без run_request — только ставит задачи (человек разберёт).
    """
    log(f'❤️ пульс запущен, интервал {interval:g} с. Ctrl+C — стоп.')
    while not (stop and stop()):
        tick(ask, areas, log)
        if run_request:
            drain_inbox(run_request, log)
        for _ in range(int(max(1, interval))):
            if stop and stop():
                break
            time.sleep(1)
    log('⏹ пульс остановлен.')


def main():
    """
    python -m aivision.pulse            один такт оценки (ставит задачу во входящие)
    python -m aivision.pulse --loop 300 цикл каждые 5 минут
    python -m aivision.pulse --run      разобрать входящие (план, без мыши); с --execute — по-настоящему
    """
    import sys

    args = sys.argv[1:]
    execute = '--execute' in args
    if execute:                                  # по-настоящему на экране — службы нужны сразу
        from . import services
        services.start_background()
    run_request = None
    if '--run' in args or '--loop' in args:
        from . import orchestrator as O
        from .agents import load_agents

        def run_request(task: str) -> dict:
            def step(t, ctx):
                if t.agent is None:
                    return {'status': 'fail', 'answer': 'нет агента'}
                if not execute:
                    return {'status': 'done', 'answer': f'[план] {t.agent.name}: {t.task}'}
                return O.tool_step(t.agent, t.task, ctx, env=None)
            return O.orchestrate(task, step, agents=load_agents(), log=lambda m: print("   ", m), strategy=True)

    if '--loop' in args:
        i = args.index('--loop')
        interval = float(args[i + 1]) if i + 1 < len(args) else 300
        run(interval, run_request=run_request)
    else:
        tick()
        if run_request:
            drain_inbox(run_request)


if __name__ == '__main__':
    main()
