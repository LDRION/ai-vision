"""
Доходит ли картинка до Siberi: изображения с крупным случайным кодом, простой вопрос.
Варианты: одна JPEG / одна PNG / две картинки в одном запросе. Последовательно.

    python tests/probe_image.py [jpeg png two]
"""

import base64
import io
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from aivision import siberi  # noqa: E402


def card(code: str, fmt: str = 'JPEG') -> str:
    img = Image.new('RGB', (480, 200), '#1e3a8a')
    ImageDraw.Draw(img).text((240, 100), code, font=ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf', 96),
                             fill='white', anchor='mm')
    buf = io.BytesIO()
    img.save(buf, fmt)
    return f'data:image/{fmt.lower()};base64,' + base64.b64encode(buf.getvalue()).decode()


def _code():
    return f'{random.randint(0, 99999):05d}'


def probe(name):
    codes = [_code(), _code()] if name == 'two' else [_code()]
    images = [card(c, 'PNG' if name == 'png' else 'JPEG') for c in codes]
    question = ('На картинках по одному числу. Перечисли их через пробел по порядку картинок.'
                if len(codes) > 1 else 'Какое число написано на картинке? Ответь только числом.')
    t0 = time.time()
    try:
        ans = siberi.ask('', question + ' Если картинки нет — ответь НЕТ.', images=images)
    except Exception as e:
        ans = f'ошибка: {e}'
    ok = all(c in ans.replace(' ', '') for c in codes)
    print(f"{'✅' if ok else '❌'} {name:<5} коды {' '.join(codes)} → {ans[:80]!r}  ({time.time() - t0:.1f} с)",
          flush=True)


if __name__ == '__main__':
    for name in sys.argv[1:] or ['jpeg', 'png', 'two']:
        probe(name)
