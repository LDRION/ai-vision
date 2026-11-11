"""
Claude как мозг приложения — альтернатива Siberi с той же сигнатурой ask().
Думает и планирует Claude (Anthropic API), а экраном/мышью по-прежнему управляет
наш движок. Включается одной переменной: AV_BRAIN=claude (siberi.ask делегирует сюда).

Ключ ANTHROPIC_API_KEY — в .env рядом с проектом (подхватывается как SIBERI_API_KEY).
Модель — ANTHROPIC_MODEL (по умолчанию claude-opus-5-5).
"""
import os
import re
from typing import Callable, List, Optional

try:
    # Kaspersky подменяет корневой сертификат — truststore доверяет хранилищу Windows.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

_DATA_URL = re.compile(r'^data:(?P<mt>[\w/.+-]+);base64,(?P<data>.+)$', re.DOTALL)
_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic                         # ключ из ANTHROPIC_API_KEY или профиля `ant`
        _client = anthropic.Anthropic()
    return _client


def _image_block(data_url: str) -> dict:
    m = _DATA_URL.match((data_url or '').strip())
    mt, data = (m.group('mt'), m.group('data')) if m else ('image/jpeg', data_url)
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': data}}


def ask(system: str, prompt: str, images: Optional[List[str]] = None,
        timeout: float = 120.0, on_progress: Optional[Callable[[str], None]] = None) -> str:
    """Та же сигнатура, что siberi.ask: images — data URL'ы (до 4). → текст ответа."""
    model = os.environ.get('ANTHROPIC_MODEL', 'claude-opus-5-5')
    content = [_image_block(i) for i in (images or [])[:4]]
    content.append({'type': 'text', 'text': prompt})
    client = _get_client().with_options(timeout=timeout)
    resp = client.messages.create(
        model=model, max_tokens=8192, system=system,
        messages=[{'role': 'user', 'content': content}])
    return ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
