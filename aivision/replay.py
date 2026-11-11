"""
Переигровка рецепта — быстрый путь без пошагового разбора.

Для каждого шага рецепта заново находим цель на текущем экране ПО ТЕКСТУ/РОЛИ
(а не по старым координатам — интерфейс мог сдвинуться) и выполняем действие,
проверяя, что экран отреагировал как тогда. Если цель не найдена или реакция
не та — возвращаем 'fallback' с номером шага: дальше доигрывает полный разбор
(aivision.loop.run_task). Переменные части (кому писать, что писать) подставляет
одна адаптация (adapt_recipe) — один запрос к Siberi вместо запроса на каждый шаг.
"""

import re
from pathlib import Path
from typing import Callable, List, Optional

from .motion import build_events
from .recipe import Recipe, Step
from .screen import describe_change

VERIFY_WAIT = 0.0


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '').lower().replace('ё', 'е')).strip()


def find_target(text: str, role: str, elements: List[dict]) -> Optional[dict]:
    """Элемент с таким же текстом: точное совпадение → вхождение → по роли+частично."""
    t = _norm(text)
    if not t:
        return None
    exact = [e for e in elements if _norm(e['text']) == t]
    if exact:
        return exact[0]
    # Пустая подпись «входит» в любой текст — такие элементы в обратное вхождение не берём.
    part = [e for e in elements if t in _norm(e['text'])
            or (len(_norm(e['text'])) >= 2 and _norm(e['text']) in t)]
    if role:
        part.sort(key=lambda e: e.get('role', '') != role)      # сперва с нужной ролью
    return part[0] if part else None


def resolve_step(st: Step, elements, size, offset) -> Optional[dict]:
    """Шаг рецепта → действие в координатах ЭКРАНА, или None если цель не найдена.

    elements — список элементов экрана или функция look(text, role) → элемент
    (так replay сперва спрашивает живой индекс Retina, а полный список собирает лишь при промахе).
    """
    look = elements if callable(elements) else (lambda text, role: find_target(text, role, elements))
    ox, oy = offset
    w, h = size
    act = {'action': st.action, 'x': None, 'y': None, 'box': None, 'to': None,
           'points': None, 'text': st.text, 'keys': st.keys,
           'scroll': st.scroll, 'seconds': st.seconds}

    if st.action == 'stroke':
        act['points'] = [[int(p[0]) + ox, int(p[1]) + oy] for p in (st.points or [])]
        return act if len(act['points']) >= 2 else None

    if st.target_text:
        el = look(st.target_text, st.target_role)
        if el is None:
            return None
        x1, y1, x2, y2 = el['box']
        act['x'], act['y'] = (x1 + x2) // 2 + ox, (y1 + y2) // 2 + oy
        act['box'] = [x1 + ox, y1 + oy, x2 + ox, y2 + oy]
    elif st.rel:
        act['x'], act['y'] = int(st.rel[0] * w) + ox, int(st.rel[1] * h) + oy
    elif st.action in ('key', 'scroll'):
        pass                                        # действие без точки
    else:
        return None

    if st.action == 'drag':
        if st.to_text:
            el = look(st.to_text, '')
            if el is None:
                return None
            x1, y1, x2, y2 = el['box']
            act['to'] = {'x': (x1 + x2) // 2 + ox, 'y': (y1 + y2) // 2 + oy, 'box': None}
        elif st.to_rel:
            act['to'] = {'x': int(st.to_rel[0] * w) + ox, 'y': int(st.to_rel[1] * h) + oy, 'box': None}
        else:
            return None
    return act


def _lookup(env, img, offset, log: Callable[[str], None] = lambda m: None):
    """
    Поиск цели по подписи, от дешёвого к дорогому:
      1. живой индекс Retina (env.find) — десятки мс;
      2. полный список элементов экрана (OCR + UIA) — текстом;
      3. локальная модель (aivision.local_brain) — по смыслу: синоним, перевод;
      4. иконка без подписи (aivision.icons) — словарь иконок, затем наведение и подсказка.
    Две равные цели (три «Удалить») — None: выбрать между ними без контекста нельзя,
    это решает большая модель, наугад не кликаем.
    """
    from . import icons, local_brain
    cache = {}

    def els():
        if 'els' not in cache:
            cache['els'] = env.elements(img, offset)
        return cache['els']

    def look(text, role):
        fast = getattr(env, 'find', None)
        el = fast(text, role) if fast else None
        if el is not None:
            return el
        if getattr(env, 'last_find', '') == 'ambiguous':
            return None
        el = find_target(text, role, els())
        if el is None:
            title = env.title() if hasattr(env, 'title') else ''
            el = local_brain.choose(text, els(), role, title, log)
        if el is None:
            el = icons.resolve(text, role, env, img, offset, log)      # иконка без подписи
        return el
    return look


def run_scenario(goal: str, env, recipe: Optional[Recipe], fast_replay: bool,
                 run_dir: Optional[Path] = None, log: Callable[[str], None] = print,
                 memory=None, hints=(), noise: float = 1.0, ask_hint=None, on_step=None,
                 max_steps: int = 15, refine: bool = True) -> dict:
    """
    Умный запуск: есть рецепт и включён быстрый путь → адаптируем и проигрываем;
    не сошёлся — доигрываем полным разбором (aivision.loop.run_task). Нет рецепта —
    сразу полный разбор. В результат добавлено 'via' (recipe/analysis) и
    'fresh_recipe' (можно ли перезаписать рецепт этим прогоном).
    """
    from .loop import run_task

    def full(via, record):
        r = run_task(goal, env, max_steps=max_steps, refine=refine, run_dir=run_dir, log=log,
                     memory=memory, hints=hints, noise=noise, ask_hint=ask_hint, on_step=on_step)
        r['via'] = via
        r['fresh_recipe'] = record and r['status'] == 'done'
        return r

    if not (recipe and fast_replay and recipe.steps):
        return full('analysis', record=True)

    log(f'  ⚡ быстрый путь: рецепт из {len(recipe.steps)} шагов')
    steps = adapt_recipe(recipe, goal, log)
    res = run_recipe(recipe, goal, env, steps=steps, run_dir=run_dir, log=log, noise=noise)
    if res['status'] == 'done':
        return {'status': 'done', 'answer': 'выполнено по рецепту (быстрый путь)',
                'steps': res['done'], 'history': [], 'seconds': 0, 'lessons': [],
                'hints': list(hints), 'via': 'recipe', 'fresh_recipe': False}
    if res['status'] == 'aborted':
        return {'status': 'aborted', 'answer': res['reason'], 'steps': res['done'],
                'history': [], 'seconds': 0, 'lessons': [], 'hints': list(hints),
                'via': 'recipe', 'fresh_recipe': False}
    advanced = res.get('done', 0) > 0
    log('  ↪ доигрываю полным разбором')
    # перезаписывать рецепт можно только если быстрый путь НЕ сдвинул экран
    # (иначе разбор видит лишь «хвост» задачи и рецепт выйдет обрезанным)
    return full('recipe+analysis' if advanced else 'analysis', record=not advanced)


def adapt_recipe(recipe: Recipe, new_goal: str, log: Callable[[str], None] = print) -> List[Step]:
    """
    Подставить переменные части рецепта под новую задачу одним запросом к Siberi.
    Цель та же, промт другой → меняем только цели-элементы и вводимый текст по шагам.
    Если задача совпадает с записанной или адаптация не удалась — рецепт как есть.
    """
    from dataclasses import replace
    from . import siberi
    from .agent import parse_json
    from .prompt import ADAPT_SYSTEM, adapt_prompt

    steps = [replace(s) for s in recipe.steps]
    if _norm(new_goal) == _norm(recipe.goal):
        return steps
    try:
        raw = siberi.ask(ADAPT_SYSTEM, adapt_prompt(recipe.goal, new_goal, steps))
        overrides = parse_json(raw).get('steps') or []
    except Exception as e:
        log(f'  ⚠️ адаптация не удалась ({e}) — проигрываю рецепт как есть')
        return steps
    changed = 0
    for ov in overrides:
        try:
            idx = int(ov.get('i')) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(steps):
            if ov.get('target_text'):
                steps[idx].target_text = str(ov['target_text'])
                changed += 1
            if ov.get('text') is not None:
                steps[idx].text = str(ov['text'])
                changed += 1
    if changed:
        log(f'  ✨ адаптировано под новую задачу: {changed} подстановок')
    return steps


def run_recipe(recipe: Recipe, goal: str, env, steps: Optional[List[Step]] = None,
               run_dir: Optional[Path] = None, log: Callable[[str], None] = print,
               noise: float = 1.0) -> dict:
    """
    → {'status': 'done' | 'fallback' | 'aborted', 'from_step': i, 'reason': str, 'done': N}
    'fallback' — с шага from_step рецепт не сошёлся, доигрывает полный разбор.
    """
    from .motion import Aborted, save_rec
    steps = steps if steps is not None else recipe.steps
    img, offset = env.grab()
    for i, st in enumerate(steps):
        act = resolve_step(st, _lookup(env, img, offset, log), img.size, offset)
        if act is None:
            log(f'  ⟲ шаг {i + 1}: цель «{st.target_text or "?"}» не найдена — перехожу в разбор')
            return {'status': 'fallback', 'from_step': i, 'done': i,
                    'reason': f'цель «{st.target_text or "?"}» не найдена'}
        log(f"  ▶ шаг {i + 1}/{len(steps)}: {st.action} «{st.target_text[:30]}» {st.text[:20]}")
        try:
            events = build_events(env.cursor(), act, noise=noise)
            if run_dir and events:
                sd = run_dir / f'step_{i + 1:02d}'
                sd.mkdir(parents=True, exist_ok=True)
                img.save(sd / 'screen.png')
                save_rec(sd / 'action.rec', events, env.cursor())
            env.perform(events)
        except Aborted as e:
            return {'status': 'aborted', 'from_step': i, 'done': i, 'reason': str(e)}

        after, offset = env.settle(img)
        change = describe_change(img, after)
        # ждали видимой реакции, а её нет — рецепт разошёлся с реальностью
        if st.change and not st.change.startswith('экран НЕ') and change.startswith('экран НЕ'):
            log(f'  ⟲ шаг {i + 1}: экран не отреагировал как в рецепте — перехожу в разбор')
            return {'status': 'fallback', 'from_step': i + 1, 'done': i + 1,
                    'reason': 'шаг не дал ожидаемой реакции'}
        img = after
    return {'status': 'done', 'from_step': len(steps), 'done': len(steps), 'reason': ''}
