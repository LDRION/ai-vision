"""
Маскировка секретов в тексте с экрана.

Retina и OCR читают всё, что видно, — в том числе открытый .env с ключами. Такой
текст идёт в ленту «Зрение» и в промпты модели (список элементов, изменения после
шага), поэтому ключи, токены и пароли закрываем до того, как текст куда-то уйдёт.
Ложное срабатывание (длинный хеш, id) безопасно: модели он почти никогда не нужен.
"""

import re

MASK = '••••'
_RULES = [
    # «API_KEY=…», «token: …», «пароль = …» — оставляем имя, прячем значение
    (re.compile(r'(?i)\b((?:[a-z]+[_\s-]?)?(?:api[_\s-]?key|key|token|secret|password|passwd|pwd)\b\s*[=:]\s*)\S+'),
     r'\1' + MASK),
    (re.compile(r'(?i)((?:пароль|ключ|токен)\s*[=:]\s*)\S+'), r'\1' + MASK),
    # ключи с узнаваемым префиксом: sk-…, ghp_…, xoxb-…, AKIA…
    (re.compile(r'\b(?:sk|pk|rk|gsk|ghp|gho|ghs|github_pat|glpat|xox[abpr])[-_][A-Za-z0-9_\-]{12,}'), MASK),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), MASK),
    # длинная сплошная строка из букв-цифр — токен или хеш
    (re.compile(r'\b[A-Za-z0-9_\-+/]{32,}={0,2}'), MASK),
]


def redact(text: str) -> str:
    if not text:
        return text
    for rx, rep in _RULES:
        text = rx.sub(rep, text)
    return text
