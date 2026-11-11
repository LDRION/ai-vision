"""
Живой замер многошагового агента: настоящая Siberi проходит задачи целиком
на виртуальном экране (tests/fake_env.py). Мышь пользователя не трогается.

    python tests/bench_tasks.py [сценарий ...]          по одному разу, без памяти
    python tests/bench_tasks.py --learn [сценарий ...]  каждый дважды с общей памятью:
                                                        видно, учится ли агент на опыте
"""

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aivision.loop import run_task    # noqa: E402
from aivision.memory import Memory    # noqa: E402
from tests.fake_env import FakeEnv    # noqa: E402

# имя: (стартовая страница, цель, подсказки, проверка)
SCENARIOS = {
    'weather': (
        'home', 'Узнай через поиск Яндекса, какая сейчас погода в Москве', [],
        lambda r, env: r['status'] == 'done' and '12' in r['answer'] and env.page == 'results',
    ),
    'latest_mail': (
        'home', 'Открой Яндекс Почту и открой самое свежее письмо. Скажи, от кого оно и о чём', [],
        lambda r, env: r['status'] == 'done' and env.page == 'letter' and env.letter == 0 and 'Анн' in r['answer'],
    ),
    'delivery_date': (
        'home', 'Зайди в почту и узнай из письма Яндекс Маркета, когда доставят заказ', [],
        lambda r, env: r['status'] == 'done' and env.letter == 1 and '16' in r['answer'],
    ),
    'login_needs_password': (
        'home', 'Войди в мой аккаунт Яндекса', [],
        lambda r, env: r['status'] == 'fail',
    ),
    'kanban_drag': (
        'board', 'Перенеси задачу «Подготовить отчёт» в колонку «Готово»', [],
        lambda r, env: r['status'] == 'done' and 'Подготовить отчёт' in env.cards['done'],
    ),
    'menu_slider': (
        'home', 'Через меню «Ещё» открой настройки и поставь громкость уведомлений на 30%', [],
        lambda r, env: r['status'] == 'done' and env.page == 'settings' and 25 <= env.volume <= 35,
    ),
    'archive_card': (
        'board', 'Убери карточку «Купить домен» с доски в архив', [],
        lambda r, env: r['status'] == 'done' and 'Купить домен' in env.archived,
    ),
    'vague_hint': (
        'board', 'Созвон с дизайнером уже начался, отметь это на доске',
        ['вот тут зажимаешь карточку и ведёшь мышку правее, примерно в середину, там и отпускаешь'],
        lambda r, env: r['status'] == 'done' and 'Созвон с дизайнером' in env.cards['doing'],
    ),
}


def run(name, out, memory, tag=''):
    page, goal, hints, ok = SCENARIOS[name]
    env = FakeEnv(page)
    d = out / f'{name}{tag}'
    d.mkdir(parents=True)
    print(f'\n══ {name}{tag}: {goal}' + ''.join(f'\n   🙋 {h}' for h in hints))
    t0 = time.time()
    r = run_task(goal, env, max_steps=10, run_dir=d, memory=memory, hints=hints)
    passed = ok(r, env)
    print(f"\n  {'✅' if passed else '❌'} {r['status']} за {r['steps']} шаг(а), {time.time() - t0:.0f} с: {r['answer']}")
    return (f'{name}{tag}', passed, r['status'], r['steps'], time.time() - t0, r['answer'])


def main():
    learn = '--learn' in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or list(SCENARIOS)
    out = ROOT / 'runs' / f'tasks-{datetime.now():%Y%m%d-%H%M%S}'
    memory = Memory(out / 'lessons.json') if learn else None
    rows = []
    for name in names:
        if learn:
            rows.append(run(name, out, memory, ' #1'))
            rows.append(run(name, out, memory, ' #2'))
        else:
            rows.append(run(name, out, memory))

    print('\n══ ИТОГ')
    for name, passed, status, steps, secs, answer in rows:
        print(f"  {'✅' if passed else '❌'} {name:<24} {status:<7} шагов {steps:<2} {secs:4.0f} с  {answer[:60]}")
    print(f'  {sum(r[1] for r in rows)}/{len(rows)} · подробности: {out}')
    if memory:
        print('\n══ ОПЫТ')
        for les in memory.lessons:
            print(f"  [{les['app'] or 'общий'}] ×{les['confirmed']} {les['text']}")


if __name__ == '__main__':
    main()
