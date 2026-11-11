"""
Картинки команды — генерация через Stable Diffusion WebUI (локальный HTTP API).
Инструмент агента-художника: шапки под видео/посты, иллюстрации.

WebUI должен быть запущен с флагом --api (в stable-diffusion-webui/webui-user.bat
добавить --api в COMMANDLINE_ARGS). Адрес берётся из SD_WEBUI_URL или по умолчанию
http://127.0.0.1:7860. Эндпойнт /sdapi/v1/txt2img возвращает картинку в base64.

  enabled()                         → поднят ли WebUI с API
  generate(prompt, out_path, …)     → путь к PNG
"""

import base64
import os
from pathlib import Path

import requests

BASE_URL = os.environ.get('SD_WEBUI_URL', 'http://127.0.0.1:7860').rstrip('/')
NEGATIVE = 'lowres, blurry, text, watermark, deformed, extra limbs'


def enabled(base_url: str = BASE_URL, timeout: float = 3.0) -> bool:
    try:
        return requests.get(f'{base_url}/sdapi/v1/sd-models', timeout=timeout).ok
    except requests.RequestException:
        return False


def generate(prompt: str, out_path, width: int = 1280, height: int = 720, steps: int = 28,
             cfg: float = 6.5, negative: str = NEGATIVE, base_url: str = BASE_URL,
             timeout: float = 300.0) -> Path:
    """Сгенерировать картинку по промту и сохранить PNG → путь."""
    body = {'prompt': prompt, 'negative_prompt': negative, 'width': width, 'height': height,
            'steps': steps, 'cfg_scale': cfg, 'sampler_name': 'DPM++ 2M Karras', 'batch_size': 1}
    r = requests.post(f'{base_url}/sdapi/v1/txt2img', json=body, timeout=timeout)
    if not r.ok:
        raise RuntimeError(f'SD WebUI {r.status_code}: {r.text[:200]}')
    images = r.json().get('images') or []
    if not images:
        raise RuntimeError('SD WebUI не вернул картинку')
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(images[0].split(',', 1)[-1]))
    return out
