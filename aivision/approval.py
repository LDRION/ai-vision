"""
Человек в контуре — страховка для необратимых и денежных действий.

Перед таким шагом оркестратор спрашивает подтверждение. Нет ответа «да» —
шаг НЕ исполняется, а кладётся в company/approvals как ожидающий. Человек
подтверждает (в UI или файлом), и только тогда действие пойдёт. Так в потоке
24/7 автономно делается безопасное, а рискованное ждёт решения человека.
"""

import json
import time
from pathlib import Path
from typing import List

from . import paths

APPROVALS = paths.COMPANY / 'approvals'

# Подстроки-маркеры необратимого и денежного. Список расширяется под свои риски.
RISKY_WORDS = (
    'оплат', 'оплач', 'куп', 'покупк', 'заплат', 'перевед', 'перечисл', 'деньг', 'платёж',
    'платеж', 'счёт на', 'счет на', 'списан', 'спиши', 'карту', 'картой', 'вывод средств',
    'удали', 'удал', 'сотри', 'сотру', 'очист', 'безвозвратн', 'отправь деньги',
    'pay', 'purchase', 'checkout', 'delete', 'wipe', 'transfer money', 'send money',
)


def is_risky(task: str) -> bool:
    t = (task or '').lower().replace('ё', 'е')
    return any(w.replace('ё', 'е') in t for w in RISKY_WORDS)


def request(agent: str, task: str, why: str = '') -> str:
    """Положить запрос на подтверждение. → id."""
    APPROVALS.mkdir(parents=True, exist_ok=True)
    aid = time.strftime('%Y%m%d-%H%M%S-') + str(int(time.time() * 1000) % 1000)
    (APPROVALS / f'{aid}.json').write_text(
        json.dumps({'id': aid, 'agent': agent, 'task': task, 'why': why,
                    'status': 'pending', 'created': time.time()}, ensure_ascii=False, indent=1),
        encoding='utf-8')
    return aid


def pending() -> List[dict]:
    out = []
    if APPROVALS.exists():
        for p in sorted(APPROVALS.glob('*.json')):
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
                if d.get('status') == 'pending':
                    out.append(d)
            except (OSError, ValueError):
                pass
    return out


def resolve(aid: str, ok: bool):
    """Отметить запрос как одобренный/отклонённый человеком."""
    p = APPROVALS / f'{aid}.json'
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            return
        d['status'] = 'approved' if ok else 'rejected'
        d['resolved'] = time.time()
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
