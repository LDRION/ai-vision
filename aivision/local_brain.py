"""
Спинной мозг — локальная модель (Ollama) выбирает элемент экрана по смыслу.

Рефлекс ищет подпись текстом; когда текстом не нашлось («Sign in» при кнопке
«Войти», «Сохранить» при «Применить изменения»), локальная модель получает
список подписей с номерами и называет номер. Это классификация, а не план:
координаты она не выдумывает, план и тип действия остаются за большой моделью.
Ответ проверяется: номер должен быть из списка, иначе считаем, что не нашла.

    AV_LOCAL_BRAIN=0           — не звать локальную модель
    AV_LOCAL_MODEL=qwen2.5:7b  — какую модель Ollama
    AV_OLLAMA_URL=...          — адрес Ollama (по умолчанию http://127.0.0.1:11434)
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional

DEFAULT_MODEL = 'qwen2.5:7b'
MAX_ITEMS = 160
LABEL_LEN = 48
TIMEOUT = 25.0               # первый вызов грузит модель в видеопамять
DOWN_FOR = 30.0

SYSTEM = (
    'Ты выбираешь элемент интерфейса на экране. Дано намерение пользователя и пронумерованный '
    'список элементов (роль и подпись). Найди элемент, нажатие на который выполняет намерение. '
    'Подпись может отличаться: синоним, перевод, другая форма слова, лишние уточняющие слова. '
    'Примеры: «Sign in» → «Войти»; «Settings» → «Настройки почты»; «Сохранить» → «Применить изменения»; '
    '«Delete» → «Удалить письмо»; «Корзина» → «Удалённые». '
    'Ответ — один JSON: {"id": <номер>}. Если ни один элемент не подходит по смыслу — {"id": 0}. '
    'Не угадывай: лучше 0, чем не тот элемент.'
)

VERIFY_SYSTEM = (
    'Ты проверяешь выбор элемента интерфейса. Дано намерение пользователя и подпись элемента. '
    'Ответь, выполнит ли нажатие на этот элемент именно это намерение: подпись означает то же '
    'самое (синоним, перевод, уточнение). Похожая тема, соседнее действие или программа, в которой '
    'это можно сделать, — НЕ то же самое. Ответ — один JSON: {"same": true} или {"same": false}.'
)

_down_until = 0.0
stats = {'calls': 0, 'hits': 0, 'rejected': 0, 'ms': 0.0, 'prompt_tokens': 0}


def model() -> str:
    return os.environ.get('AV_LOCAL_MODEL', DEFAULT_MODEL)


def enabled() -> bool:
    return os.environ.get('AV_LOCAL_BRAIN', '1') != '0' and time.time() >= _down_until


def _url() -> str:
    return os.environ.get('AV_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')


def _chat(system: str, user: str, timeout: float = TIMEOUT, predict: int = 48) -> Optional[dict]:
    """Один запрос к Ollama /api/chat. None — Ollama недоступна (помечаем на DOWN_FOR секунд)."""
    global _down_until
    body = json.dumps({
        'model': model(), 'stream': False, 'format': 'json', 'keep_alive': '30m',
        **({'think': False} if 'qwen3' in model().lower() else {}),
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'options': {'temperature': 0, 'num_ctx': 6144, 'num_predict': predict},
    }).encode('utf-8')
    req = urllib.request.Request(_url() + '/api/chat', data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        _down_until = time.time() + DOWN_FOR
        return None


def _label(e: dict) -> str:
    text = ' '.join((e.get('text') or '').split())[:LABEL_LEN]
    if not text and e.get('value'):
        text = 'значение: ' + ' '.join(str(e['value']).split())[:LABEL_LEN - 10]
    return f"{e.get('role') or 'элемент'} «{text}»" if text else (e.get('role') or 'элемент')


def prompt(intent: str, items: List[dict], role: str = '', window: str = '') -> str:
    want = f'нажать «{intent}»' + (f' ({role})' if role else '')
    lines = [f'Намерение: {want}']
    if window:
        lines.append(f'Окно: «{window[:80]}»')
    lines.append('Элементы:')
    lines += [f'{i}. {_label(e)}' for i, e in enumerate(items, 1)]
    return '\n'.join(lines)


def candidates(elements: List[dict], role: str = '') -> List[dict]:
    """Что показать модели: подписанные элементы (и поля ввода) без повторов.

    Порядок: нужная роль → остальные интерактивные → OCR-текст. Лимит режет хвост,
    а хвост в порядке чтения — это низ экрана (панель задач), поэтому сортируем по ценности.
    """
    seen, items = set(), []
    for e in elements:
        text = ' '.join((e.get('text') or '').split())
        if not text and e.get('role') != 'поле ввода':
            continue
        key = (e.get('role'), text.lower())
        if text and key in seen:
            continue
        seen.add(key)
        items.append(e)
    items.sort(key=lambda e: (bool(role) and e.get('role') != role, e.get('role') == 'текст'))
    return items[:MAX_ITEMS]


def warmup() -> None:
    """Загрузить модель в видеопамять заранее (фоном), чтобы первый выбор не ждал 5 с."""
    global _down_until
    if not enabled():
        return
    body = json.dumps({'model': model(), 'keep_alive': '30m', 'prompt': ''}).encode('utf-8')
    req = urllib.request.Request(_url() + '/api/generate', data=body, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except (urllib.error.URLError, OSError, ValueError):
        _down_until = time.time() + DOWN_FOR


def choose(intent: str, elements: List[dict], role: str = '', window: str = '',
           log: Callable[[str], None] = lambda m: None) -> Optional[dict]:
    """Элемент из elements под намерение или None (не уверена, Ollama нет, ответ мимо списка)."""
    if not enabled() or not intent.strip():
        return None
    items = candidates(elements, role)
    if not items:
        return None
    t = time.perf_counter()
    r = _chat(SYSTEM, prompt(intent, items, role, window))
    ms = (time.perf_counter() - t) * 1000
    if r is None:
        return None
    stats['calls'] += 1
    stats['ms'] += ms
    stats['prompt_tokens'] += int(r.get('prompt_eval_count') or 0)
    el = _resolve(r, items)
    if el is None:
        log(f'  🧠 локальная модель: «{intent}» — подходящего нет ({ms:.0f} мс)')
        return None
    if not verify(intent, el):
        stats['rejected'] += 1
        ms = (time.perf_counter() - t) * 1000
        log(f'  🧠 локальная модель: «{intent}» → {_label(el)} — проверка не подтвердила ({ms:.0f} мс)')
        return None
    stats['hits'] += 1
    ms = (time.perf_counter() - t) * 1000
    log(f'  🧠 локальная модель: «{intent}» → {_label(el)} ({ms:.0f} мс)')
    return el


def verify(intent: str, el: dict) -> bool:
    """Второй, короткий вопрос: «это то самое?». Выбор из длинного списка у малой модели
    ошибается чаще, чем да/нет по одной паре, поэтому кликаем только подтверждённое."""
    r = _chat(VERIFY_SYSTEM, f'Намерение: нажать «{intent}»\nЭлемент: {_label(el)}', predict=12)
    try:
        return json.loads(r['message']['content']).get('same') is True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _fold(s: str) -> str:
    return ' '.join(str(s or '').lower().replace('ё', 'е').split())


def _resolve(reply: dict, items: List[dict]) -> Optional[dict]:
    """Ответ модели → элемент из списка или None (номер вне списка, мусор вместо JSON).

    Если модель всё же повторила подпись и та не совпала с подписью под номером —
    номер сдвинут (частая ошибка на длинном списке): берём элемент с этой подписью.
    """
    try:
        data = json.loads(reply['message']['content'])
        idx = int(data.get('id') or 0)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    label = _fold(data.get('label')) if isinstance(data.get('label'), str) else ''
    if label:
        same = [e for e in items if _fold(e.get('text')) == label]
        if 1 <= idx <= len(items) and _fold(items[idx - 1].get('text')) == label:
            return items[idx - 1]
        return same[0] if len(same) == 1 else None
    return items[idx - 1] if 1 <= idx <= len(items) else None
