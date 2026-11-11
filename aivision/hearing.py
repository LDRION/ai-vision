"""
Слух команды — распознавание речи через Groq Whisper (порт логики из unusual-ai:
transcriber.js + groq-engine.js), ключ GROQ_API_KEY в .env. Groq не пускает из
некоторых сетей (403) — тогда слух недоступен, нужен VPN. Выжимку из текста делает
Siberi, она от Groq не зависит.

  transcribe(path)            → текст
  summarize(text, 'выдели задачи списком') → выжимка (Siberi)
"""

import os
from pathlib import Path

import requests

try:
    import truststore
    truststore.inject_into_ssl()                 # доверять хранилищу Windows (антивирус-MITM)
except ImportError:
    pass

from . import siberi  # его импорт заодно грузит .env в окружение

TRANSCRIBE_URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
TRANSCRIBE_MODEL = 'whisper-large-v3-turbo'
MIME = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg',
        '.webm': 'audio/webm', '.m4a': 'audio/mp4', '.flac': 'audio/flac'}


def _key() -> str:
    k = os.environ.get('GROQ_API_KEY')
    if not k:
        raise RuntimeError('GROQ_API_KEY не задан (.env в корне проекта)')
    return k


def enabled() -> bool:
    return bool(os.environ.get('GROQ_API_KEY'))


def transcribe(path, language: str = 'ru', timeout: float = 120.0) -> str:
    """Распознать речь из аудиофайла → текст."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f'аудио не найдено: {p}')
    with open(p, 'rb') as f:
        files = {'file': (p.name, f, MIME.get(p.suffix.lower(), 'audio/webm'))}
        data = {'model': TRANSCRIBE_MODEL, 'language': language, 'response_format': 'json'}
        r = requests.post(TRANSCRIBE_URL, headers={'Authorization': f'Bearer {_key()}'},
                          files=files, data=data, timeout=timeout)
    if r.status_code == 403:
        raise RuntimeError('Groq не пускает из этой сети (403) — расшифровке аудио нужен VPN')
    if not r.ok:
        raise RuntimeError(f'Groq транскрипция {r.status_code}: {r.text[:200]}')
    return (r.json().get('text') or '').strip()


def summarize(text: str, instruction: str = 'Коротко перескажи и выдели задачи списком.',
              timeout: float = 90.0) -> str:
    """Выжимка / список задач из текста — через Siberi."""
    return (siberi.ask(instruction, text, timeout=timeout) or '').strip()
