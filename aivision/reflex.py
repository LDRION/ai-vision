"""
Рефлекс — простые экранные команды без модели.

«Нажми «Отправить»», «введи «привет» в поле «Сообщение»», «нажми Enter»,
цепочки через «затем»/«потом»/«;» — разбираем текстом, цель ищем по подписи в
живом индексе Retina (env.find), не нашли текстом — локальная модель выбирает
по смыслу (aivision.local_brain); кликаем и ждём реакции экрана. Ни скриншота,
ни облачной модели. Что не разобрали или не нашли — отдаём обычному
разбору (loop.run_task) вместе с историей того, что уже сделано.

Наугад не кликаем: две равные цели или слабое совпадение → разбор решает сам.
"""

import os
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from .motion import Aborted, build_events
from .screen import describe_change

Q = r'[«"“„]([^»"”“]+)[»"”“]'
CLICK = r'(?:нажми|нажать|кликни|кликнуть|щ[её]лкни|щ[её]лкнуть|выбери|выбрать|открой|открыть|перейди|перейти|зайди|зайти)'
DOUBLE = r'(?:дважды\s+(?:кликни|щ[её]лкни|нажми)|двойн\w*\s+клик\w*|сделай\s+двойной\s+клик)'
RIGHT = r'(?:правой\s+кнопкой\s+(?:мыши\s+)?(?:кликни|щ[её]лкни|нажми)|правый\s+клик|кликни\s+правой\s+кнопкой(?:\s+мыши)?)'
TYPE = r'(?:введи|ввести|напиши|написать|впиши|вписать|набери|набрать)'
PREP = r'(?:\s+(?:на|в|во|по))?'
NOUNS = {'кнопк': 'кнопка', 'ссылк': 'ссылка', 'вкладк': 'вкладка', 'пункт': 'пункт меню',
         'поле': 'поле ввода', 'флажок': 'флажок', 'галочк': 'флажок', 'переключател': 'переключатель'}
NOUN = r'(?:\s+(кнопк[уаеи]|ссылк[уаеи]|вкладк[уаеи]|пункт(?:\s+меню)?|меню|раздел|элемент|иконк[уаеи]|значок|поле|флажок|галочк[уаеи]|переключатель))?'
KEYS = {'enter': 'enter', 'ввод': 'enter', 'энтер': 'enter', 'esc': 'esc', 'escape': 'esc',
        'эскейп': 'esc', 'tab': 'tab', 'таб': 'tab', 'пробел': 'space', 'space': 'space',
        'backspace': 'backspace', 'delete': 'delete', 'del': 'delete'}
SPLIT = re.compile(r'\s*(?:[;→]|,?\s+(?:а\s+)?(?:затем|потом|после\s+этого|далее)\s*,?)\s*'
                   r'|\.\s+|\s+и\s+(?=(?:' + CLICK[3:-1] + '|' + TYPE[3:-1] + r')\b)', re.I)


@dataclass
class Op:
    kind: str                  # click | double_click | right_click | type | key
    label: str = ''            # подпись цели (для type — поле; пусто — печатаем в фокус)
    role: str = ''             # роль из слова-уточнения («кнопку», «поле»)
    text: str = ''             # что печатать
    keys: str = ''

    def describe(self) -> str:
        if self.kind == 'key':
            return f'клавиша {self.keys}'
        if self.kind == 'type':
            return f'ввод «{self.text}»' + (f' в «{self.label}»' if self.label else '')
        return {'click': 'клик', 'double_click': 'двойной клик', 'right_click': 'правый клик'}[self.kind] \
            + f' «{self.label}»'


def enabled() -> bool:
    return os.environ.get('AV_REFLEX', '1') != '0'


def _role(noun: Optional[str]) -> str:
    n = (noun or '').lower()
    return next((v for k, v in NOUNS.items() if n.startswith(k)), '')


def _one(seg: str) -> Optional[Op]:
    s = seg.strip().rstrip('.!').strip()
    s = re.sub(r'^(?:(?:пожалуйста|теперь|сначала|потом|затем|далее|после\s+этого|и),?\s+)+', '', s, flags=re.I)
    m = re.fullmatch(DOUBLE + PREP + NOUN + r'\s*' + Q, s, re.I)
    if m:
        return Op('double_click', m.group(2).strip(), _role(m.group(1)))
    m = re.fullmatch(RIGHT + PREP + NOUN + r'\s*' + Q, s, re.I)
    if m:
        return Op('right_click', m.group(2).strip(), _role(m.group(1)))
    m = re.fullmatch(TYPE + r'\s+' + Q + r'\s+(?:в|во)' + NOUN + r'\s*' + Q, s, re.I)
    if m:
        return Op('type', m.group(3).strip(), _role(m.group(2)) or 'поле ввода', text=m.group(1))
    m = re.fullmatch(TYPE + r'\s+' + Q, s, re.I)
    if m:
        return Op('type', text=m.group(1))
    m = re.fullmatch(r'(?:нажми|нажать)\s+(?:клавишу\s+)?([a-zа-я]+)', s, re.I)
    if m and m.group(1).lower() in KEYS:
        return Op('key', keys=KEYS[m.group(1).lower()])
    m = re.fullmatch(r'(?:нажми|нажать)\s+(?:сочетание\s+)?((?:ctrl|alt|shift|win)(?:\s*\+\s*[a-z0-9]+)+)', s, re.I)
    if m:
        return Op('key', keys=re.sub(r'\s+', '', m.group(1).lower()))
    m = re.fullmatch(CLICK + PREP + NOUN + r'\s*' + Q, s, re.I)
    if m:
        return Op('click', m.group(2).strip(), _role(m.group(1)))
    return None


def parse(goal: str) -> Optional[List[Op]]:
    """Цель целиком из простых команд → список операций; иначе None (решает модель)."""
    text = ' '.join((goal or '').split())
    if not text or len(text) > 300 or '\n' in (goal or '').strip():
        return None
    quoted: List[str] = []

    def hide(m):                       # «Т. Иванов; ООО» — разделители внутри подписи не режут
        quoted.append(m.group(0))
        return f'\x00{len(quoted) - 1}\x00'
    masked = re.sub(Q, hide, text)
    ops = []
    for seg in SPLIT.split(masked):
        if not seg or not seg.strip(' ,.'):
            continue
        seg = re.sub(r'\x00(\d+)\x00', lambda m: quoted[int(m.group(1))], seg)
        op = _one(seg)
        if op is None:
            return None
        ops.append(op)
    return ops or None


def run(ops: List[Op], env, log: Callable[[str], None] = print, noise: float = 1.0) -> dict:
    """
    Исполнить операции. → {'status': done | partial | aborted, 'done': k, 'history': [...],
    'reason': str}. partial — с операции k дальше решает модель (история ей пригодится).
    """
    from . import control
    from .replay import _lookup
    history: List[str] = []
    img, offset = env.grab()
    ox, oy = offset
    for k, op in enumerate(ops):
        if control.stopped():
            return {'status': 'aborted', 'done': k, 'history': history, 'reason': 'остановлено пользователем'}
        act = {'action': op.kind, 'x': None, 'y': None, 'box': None, 'text': op.text, 'keys': op.keys}
        if op.label:
            el = _lookup(env, img, offset, log)(op.label, op.role)
            if el is None:
                return {'status': 'partial', 'done': k, 'history': history,
                        'reason': f'цель «{op.label}» не найдена однозначно'}
            x1, y1, x2, y2 = el['box']
            act.update(x=(x1 + x2) // 2 + ox, y=(y1 + y2) // 2 + oy,
                       box=[x1 + ox, y1 + oy, x2 + ox, y2 + oy], clear=op.kind == 'type')
        try:
            env.perform(build_events(env.cursor(), act, noise=noise))
        except Aborted as e:
            return {'status': 'aborted', 'done': k, 'history': history, 'reason': str(e)}
        after, offset = env.settle(img)
        ox, oy = offset
        change = describe_change(img, after)
        history.append(f'рефлекс: {op.describe()} → {change}')
        log(f'  ⚡ рефлекс: {op.describe()} → {change}')
        img = after
        # Клик без видимой реакции — промах или не та цель: дальше пусть смотрит модель.
        if op.kind in ('click', 'double_click', 'right_click') and change.startswith('экран НЕ'):
            return {'status': 'partial', 'done': k + 1, 'history': history,
                    'reason': f'после клика «{op.label}» экран не изменился'}
    return {'status': 'done', 'done': len(ops), 'history': history, 'reason': ''}
