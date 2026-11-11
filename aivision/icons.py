"""
Иконки без подписи: как назвать кнопку-пиктограмму (⚙, ×, ⋮, 🔍), у которой нет текста.

От дешёвого к дорогому:
  1. Словарь иконок — уже опознанные: миниатюра + подпись + программа. Сравнение
     картинок нормированной корреляцией (numpy), без модели — миллисекунды.
  2. Наведение — навести курсор и прочитать всплывающую подсказку через Retina.
     Без модели; найденная подпись сразу пополняет словарь, второй раз — п. 1.
  3. Не вышло — решает большая модель (она видит скриншот с метками).

Словарь — вырезки экрана, личные данные: лежит в experience/icons (вне git).
"""

import ctypes
import json
import re
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .paths import ICONS

SIZE = 32                    # миниатюра для сравнения, px
MATCH = 0.9                  # корреляция, начиная с которой иконка — «та же»
SAME = 0.97                  # почти копия уже записанной — обновляем, не плодим
PROBE_MAX = 8                # сколько безымянных иконок наводить за один поиск
MIN_SIDE, MAX_SIDE = 10, 96  # размеры, похожие на иконку-кнопку


def _fold(s: str) -> str:
    return ' '.join(re.sub(r'[^\w]+', ' ', (s or '').lower().replace('ё', 'е')).split())


def vec(img: Image.Image) -> Optional[np.ndarray]:
    """Миниатюра → единичный вектор с вычтенным средним (корреляция = скалярное произведение).
    Однотонная вырезка (пустое место) — None: сравнивать нечего."""
    a = np.asarray(img.convert('L').resize((SIZE, SIZE), Image.LANCZOS), dtype=np.float32).ravel()
    a = a - a.mean()
    n = float(np.linalg.norm(a))
    return a / n if n > 1e-3 * SIZE * SIZE else None


class IconBook:
    def __init__(self, root: Path = ICONS):
        self.root = Path(root)
        self.entries: List[dict] = []          # {'label', 'app', 'file', 'vec'}
        self._load()

    def _index(self) -> Path:
        return self.root / 'index.json'

    def _load(self):
        try:
            items = json.loads(self._index().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        for it in items:
            try:
                v = vec(Image.open(self.root / it['file']))
            except OSError:
                continue
            if v is not None:
                self.entries.append({**it, 'vec': v})

    def _save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        data = [{k: e[k] for k in ('label', 'app', 'file')} for e in self.entries]
        self._index().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')

    def name(self, crop: Image.Image, app: str = '') -> Tuple[str, float]:
        """(подпись, сходство) лучшей записи; ('', 0) — не узнали. Своя программа чуть в приоритете."""
        v = vec(crop)
        if v is None or not self.entries:
            return '', 0.0
        best, label = 0.0, ''
        for e in self.entries:
            s = float(v @ e['vec']) + (0.02 if app and e['app'] == app else 0.0)
            if s > best:
                best, label = s, e['label']
        return (label, best) if best >= MATCH else ('', best)

    def learn(self, crop: Image.Image, label: str, app: str = '') -> bool:
        label = ' '.join((label or '').split())[:60]
        v = vec(crop)
        if v is None or not label:
            return False
        for e in self.entries:
            if e['app'] == app and float(v @ e['vec']) >= SAME:
                if e['label'] != label:
                    e['label'] = label
                    self._save()
                return True
        self.root.mkdir(parents=True, exist_ok=True)
        f = f'{int(time.time() * 1000)}_{len(self.entries)}.png'
        crop.convert('RGB').save(self.root / f)
        self.entries.append({'label': label, 'app': app, 'file': f, 'vec': v})
        self._save()
        return True


_books: Dict[str, IconBook] = {}


def book(root: Path = ICONS) -> IconBook:
    key = str(root)
    if key not in _books:
        _books[key] = IconBook(root)
    return _books[key]


def process_name(hwnd: int) -> str:
    """Имя exe окна (chrome.exe) — словарь иконок свой у каждой программы."""
    try:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ''
        try:
            buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name.lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass
    return ''


def iconlike(box) -> bool:
    w, h = box[2] - box[0], box[3] - box[1]
    return MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE


def annotate(elements: List[dict], unnamed: List[dict], img: Image.Image, app: str = '',
             root: Path = ICONS) -> List[dict]:
    """Добавить в список элементов безымянные иконки, которые словарь узнал, — с подписью.

    Так узнанная иконка находится обычным поиском по тексту, а большая модель видит
    «кнопка «Настройки»» вместо безымянной рамки.
    """
    b = book(root)
    if not b.entries:
        return elements
    added = []
    for u in unnamed:
        label, score = b.name(img.crop(tuple(u['box'])), app)
        if label:
            added.append({'text': label, 'role': u.get('role') or 'кнопка', 'box': list(u['box']), 'icon': True})
    if not added:
        return elements
    from .detect import _number
    return _number(list(elements) + added)


def _same(text: str, label: str) -> bool:
    a, b = _fold(text), _fold(label)
    if not a or not b:
        return False
    if a == b:
        return True
    pa, pb = f' {a} ', f' {b} '
    return (len(a) >= 3 and pa in pb) or (len(b) >= 3 and pb in pa)


_verified: Dict[Tuple[str, str], bool] = {}


def means(text: str, label: str, role: str = 'кнопка') -> bool:
    """Подпись подходит под намерение: текстом или (перевод, синоним) — проверкой локальной модели."""
    if _same(text, label):
        return True
    from . import local_brain
    if not local_brain.enabled():
        return False
    key = (_fold(text), _fold(label))
    if key not in _verified:
        _verified[key] = local_brain.verify(text, {'text': label, 'role': role})
    return _verified[key]


def resolve(text: str, role: str, env, img: Image.Image, offset, log: Callable[[str], None] = lambda m: None,
            root: Path = ICONS) -> Optional[dict]:
    """Безымянная иконка под намерение: словарь → наведение. None — пусть решает большая модель."""
    unnamed = getattr(env, 'unnamed', None)
    if unnamed is None:
        return None
    cands = [c for c in unnamed(img, offset) if iconlike(c['box'])]
    if not cands:
        return None
    app = env.app() if hasattr(env, 'app') else ''
    b = book(root)
    known = []
    for c in cands:
        label, score = b.name(img.crop(tuple(c['box'])), app)
        if label:
            known.append((score, label, c))
    for score, label, c in sorted(known, key=lambda x: -x[0]):
        if means(text, label, c.get('role') or 'кнопка'):
            log(f'  🖼 иконка из словаря: «{text}» = «{label}» ({score:.2f})')
            return {**c, 'text': label}
    probe = getattr(env, 'probe', None)
    if probe is None:
        return None
    seen = {id(c) for _, _, c in known}
    order = [c for c in cands if id(c) not in seen]           # знакомые иконки — уже не то
    if role:
        order.sort(key=lambda c: c.get('role') != role)
    for c in order[:PROBE_MAX]:
        crop = img.crop(tuple(c['box']))
        tip = probe(c['box'])
        if not tip:
            continue
        b.learn(crop, tip, app)
        log(f'  🖼 подсказка у иконки: «{tip}» — запомнил')
        if means(text, tip, c.get('role') or 'кнопка'):
            return {**c, 'text': tip}
    return None
