"""
Оркестратор из командной строки — увидеть, как он планирует и (по флагу) исполняет.

  python -m aivision.orchestrate_cli "расшифруй голосовое и отправь текст в вк"      план
  python -m aivision.orchestrate_cli "..." --run                                     план + исполнение
  python -m aivision.orchestrate_cli --agents                                        показать команду

По умолчанию — только план (мышь не трогается, безопасно). С --run scenario-агенты
работают через движок (нужен свободный экран); инструменты, которых ещё нет
(groq, image, video), помечаются как неподключённые и возвращают fail.
"""

import sys

from . import orchestrator as O
from .agents import load_agents
from .team import org_chart


def _run_step_factory(dry: bool):
    def run_step(task, context):
        agent = task.agent
        if agent is None:
            return {'status': 'fail', 'answer': f'агент «{task.agent_name}» не найден'}
        tools = set(agent.tools)
        if dry:
            return {'status': 'done', 'answer': f'[план] {agent.name} выполнил бы: {task.task}'}
        env = None
        if 'scenario' in tools:
            from .capture import dpi_aware
            from .screen import RealEnv
            dpi_aware()
            env = RealEnv(0)                # авто: монитор рабочего окна, не лаунчера
        return O.tool_step(agent, task.task, context, env=env, log=lambda m: print('      ' + m))
    return run_step


def main():
    from . import services
    services.start_background()                  # Retina и локальная модель — к первому экранному шагу
    args = sys.argv[1:]
    if not args or args[0] in ('--agents', '-a'):
        print('\n=== Команда ИИ ===')
        print(org_chart(load_agents()))
        return
    request = args[0]
    dry = '--run' not in args
    print(f'\n🧭 Оркестратор · запрос: {request}\n{"(план, без исполнения)" if dry else "(с исполнением)"}\n')

    def confirm(agent, task):                       # необратимое/денежное — спросить человека
        try:
            return input(f'  ✋ {agent}: «{task}» — выполнить? [y/N] ').strip().lower() in ('y', 'yes', 'да', 'д')
        except EOFError:
            return False
    out = O.orchestrate(request, _run_step_factory(dry), log=print, confirm=None if dry else confirm, strategy=not dry)
    print(f'\n== ИТОГ: {out["status"]} ==')
    print(out['answer'])
    print(f'Задача: company/tasks/{out["parent"]}/  ·  отчёты: company/reports/')


if __name__ == '__main__':
    main()
