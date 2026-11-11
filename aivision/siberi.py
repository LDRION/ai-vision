"""
Клиент Siberi AI — порт unusual-ai/src/core/siberi-engine.js.

По умолчанию мозг — Siberi Local на этом компьютере (aivision/vendor/siberi_local, тот же
драйвер Google «Режима ИИ», что у siberi.online): он поднимается сам перед первым вопросом.
SIBERI_BASE_URL в .env — другой адрес (например, https://siberi.online/api/v1/ai).

POST /generate {prompt, system, image | images, wait:false} → taskId,
затем поллинг GET /result/{taskId} до status=completed.
Картинки — data URL ("data:image/jpeg;base64,..."): одна в поле image,
несколько (до 4) — списком images. Предел запроса 10 МБ; JPEG быстрее PNG.
Основной движок — Google «Режим ИИ» (картинку читает), запасные Mistral/Groq.
"""

import base64
import io
import os
import time
from typing import Callable, List, Optional

import requests

try:
    # Kaspersky подменяет сертификат своим корневым — certifi о нём не знает.
    # truststore заставляет ssl доверять хранилищу Windows, как браузер.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

POLL_INTERVAL = 0.4
START_RETRIES = 4


def _load_env():
    """Подхватывает .env рядом с проектом/exe (без зависимости от python-dotenv)."""
    from .paths import ENV_FILE
    env = ENV_FILE
    if not env.exists():
        return
    for line in env.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"\''))


_load_env()
LOCAL_URL = f"http://127.0.0.1:{os.environ.get('SIBERI_LOCAL_PORT', '8770')}/api/v1/ai"
BASE_URL = os.environ.get('SIBERI_BASE_URL', LOCAL_URL)
LOGIN_NEEDED = 'google_login_required'


def _api_key() -> str:
    key = os.environ.get('SIBERI_API_KEY')
    if not key:
        raise RuntimeError('SIBERI_API_KEY не задан (.env в корне проекта)')
    return key


def image_to_data_url(img, quality: int = 90) -> str:
    """PIL.Image → data URL в JPEG. q90 сохраняет мелкие подписи разметки читаемыми."""
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=quality)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


def _start(body: dict) -> str:
    last_err = None
    for attempt in range(1, START_RETRIES + 1):
        try:
            r = requests.post(
                f'{BASE_URL}/generate',
                json=body,
                headers={'Authorization': f'Bearer {_api_key()}'},
                timeout=30,
            )
            if not r.ok:
                raise RuntimeError(f'Siberi {r.status_code} {r.text[:200]}')
            task_id = r.json().get('taskId')
            if not task_id:
                raise RuntimeError('Siberi: нет taskId в ответе /generate')
            return task_id
        except Exception as e:
            last_err = e
            if attempt < START_RETRIES:
                time.sleep(0.6 * attempt)
    raise last_err


def _poll(task_id: str, deadline: float, on_progress: Optional[Callable[[str], None]]) -> str:
    last_len = 0
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            r = requests.get(
                f'{BASE_URL}/result/{task_id}',
                headers={'Authorization': f'Bearer {_api_key()}'},
                timeout=10,
            )
        except requests.RequestException:
            continue
        if not r.ok:
            continue
        data = r.json()
        partial = data.get('partialResult') or ''
        if on_progress and len(partial) > last_len:
            last_len = len(partial)
            try:
                on_progress(partial)
            except Exception:
                pass
        if data.get('status') == 'completed':
            return (data.get('result') or '').strip()
        if data.get('status') == 'error':
            err = str(data.get('error') or 'неизвестно')
            if err.startswith(LOGIN_NEEDED):
                raise RuntimeError('Siberi Local: Google понимает картинки только после входа в аккаунт — '
                                   'нажмите «Войти в Google» в лаунчере (Настройки → Службы) и войдите один раз')
            if 'капча' in err and LOCAL_URL in BASE_URL:
                raise RuntimeError('Siberi Local: Google просит проверку «я не робот» — нажмите «Войти в Google» '
                                   'в лаунчере (Настройки → Службы), пройдите проверку и войдите в аккаунт')
            raise RuntimeError(f"Siberi: ошибка задачи — {err}")
    raise TimeoutError('Siberi: таймаут ожидания результата')


def ask(system: str, prompt: str, images: Optional[List[str]] = None,
        timeout: float = 120.0, on_progress: Optional[Callable[[str], None]] = None) -> str:
    """images — data URL'ы (image_to_data_url), до 4 штук."""
    body = {'prompt': prompt, 'system': system, 'wait': False}
    if images:
        if len(images) > 4:
            raise ValueError('Siberi принимает до 4 картинок в запросе')
        if len(images) == 1:
            body['image'] = images[0]
        else:
            body['images'] = list(images)
    deadline = time.time() + timeout
    _ensure_local()
    return _poll(_start(body), deadline, on_progress)


def _ensure_local():
    """Мозг на этом компьютере — поднять, если ещё не отвечает (первый раз ~5–10 с)."""
    from . import services
    if services.siberi_local() and services.start_siberi() not in ('ok',):
        raise RuntimeError('Siberi Local не запустился: ' + services.describe().split(' · ')[-1])
