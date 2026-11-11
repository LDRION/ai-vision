"""
Разметка скриншота для ИИ.

Не «сетка до сотых пикселя»: картинку модель всё равно ужимает, а линии
плотнее ~100 px превращаются в шум поверх кнопок. Работает другое:

  1. Номерные метки (Set-of-Mark) — у каждого найденного элемента рамка и
     номер. ИИ называет номер, точку клика берём из рамки → точность 1 px.
  2. Координатная сетка с линейками по краям — подписи в ЭКРАННЫХ пикселях,
     поэтому ответ ИИ не зависит от того, во сколько раз ужата картинка.
  3. Zoom — для целей без метки: вырез вокруг грубой оценки, увеличение ×4,
     мелкая сетка → второй ответ точнее в разы.
"""

from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

FONT_B = 'C:/Windows/Fonts/segoeuib.ttf'
PALETTE = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
           '#008080', '#9a6324', '#800000', '#000075', '#d6008f']
BAND = '#1e1e1e'
GRID_RGBA = (255, 0, 170, 110)


def _font(size: int):
    try:
        return ImageFont.truetype(FONT_B, size)
    except OSError:
        return ImageFont.load_default()


def _rulers(d: ImageDraw.ImageDraw, w: int, h: int, pad: int, origin: Tuple[int, int],
            scale: float, step: int, label_every: int, font):
    """Подписи линий сетки на полях: сверху/снизу — x, слева/справа — y (экранные px)."""
    ox, oy = origin
    first_x = -(-ox // step) * step
    for sx in range(first_x, ox + int(w / scale) + 1, step):
        if (sx - first_x) % label_every:
            continue
        px = pad + (sx - ox) * scale
        for y in (pad / 2, pad + h + pad / 2):
            d.text((px, y), str(sx), font=font, fill='white', anchor='mm')
    first_y = -(-oy // step) * step
    for sy in range(first_y, oy + int(h / scale) + 1, step):
        if (sy - first_y) % label_every:
            continue
        py = pad + (sy - oy) * scale
        for x in (pad / 2, pad + w + pad / 2):
            d.text((x, py), str(sy), font=font, fill='white', anchor='mm')


def _grid(layer: Image.Image, pad: int, origin, scale: float, step: int, w: int, h: int):
    g = ImageDraw.Draw(layer)
    ox, oy = origin
    first_x = -(-ox // step) * step
    for sx in range(first_x, ox + int(w / scale) + 1, step):
        px = pad + (sx - ox) * scale
        g.line([(px, pad), (px, pad + h)], fill=GRID_RGBA, width=1)
    first_y = -(-oy // step) * step
    for sy in range(first_y, oy + int(h / scale) + 1, step):
        py = pad + (sy - oy) * scale
        g.line([(pad, py), (pad + w, py)], fill=GRID_RGBA, width=1)


def _with_code(canvas: Image.Image, code: Optional[str]) -> Image.Image:
    """
    Полоса сверху с проверочным кодом. Кода нет в тексте запроса — вернуть его
    модель может, только если реально видит картинку. Siberi крепит картинку
    в браузерный чат и иногда отправляет вопрос без неё — тогда модель отвечает
    вслепую по списку меток. Код это ловит.
    """
    if not code:
        return canvas
    out = Image.new('RGB', (canvas.width, canvas.height + 40), BAND)
    out.paste(canvas, (0, 40))
    ImageDraw.Draw(out).text((12, 20), f'ПРОВЕРОЧНЫЙ КОД: {code}', font=_font(22), fill='#ffd400', anchor='lm')
    return out


REGION_MARGIN = 24          # вокруг выреза окна — полоска затемнённого фона, чтобы видеть край
REGION_ZOOM = 1.6           # маленькое окно показываем крупнее, но не больше чем во столько раз


def compose(img: Image.Image, elements: List[dict], step: int = 100, pad: int = 48,
            code: Optional[str] = None, cursor: Optional[Tuple[int, int]] = None,
            region: Optional[List[int]] = None) -> Image.Image:
    """
    Скриншот + сетка с линейками + номерные метки. Одна картинка — Siberi берёт одну.
    cursor — где сейчас курсор (снимок экрана его не содержит): голубое кольцо,
    чтобы подсказки «сюда / отсюда» имели точку отсчёта.
    region — [x1, y1, x2, y2]: показать только этот вырез (открытое всплывающее окно),
    по возможности крупнее; линейки и сетка — по-прежнему в пикселях экрана.
    """
    img = img.convert('RGB')
    ox, oy, scale = 0, 0, 1.0
    if region:
        W, H = img.size
        ox, oy = max(0, region[0] - REGION_MARGIN), max(0, region[1] - REGION_MARGIN)
        img = img.crop((ox, oy, min(W, region[2] + REGION_MARGIN), min(H, region[3] + REGION_MARGIN)))
        scale = max(1.0, min(REGION_ZOOM, H / img.height, W / img.width))
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    w, h = img.size
    canvas = Image.new('RGB', (w + 2 * pad, h + 2 * pad), BAND)
    canvas.paste(img, (pad, pad))

    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    _grid(layer, pad, (ox, oy), scale, step, w, h)
    canvas = Image.alpha_composite(canvas.convert('RGBA'), layer).convert('RGB')

    d = ImageDraw.Draw(canvas)
    _rulers(d, w, h, pad, (ox, oy), scale, step, 1, _font(17))

    def at(x, y):
        return pad + (x - ox) * scale, pad + (y - oy) * scale

    tag_font = _font(15)
    for el in elements:
        x1, y1 = at(*el['box'][:2])
        x2, y2 = at(*el['box'][2:])
        if x2 < pad or y2 < pad or x1 > pad + w or y1 > pad + h:
            continue
        color = PALETTE[el['id'] % len(PALETTE)]
        d.rectangle([x1 - 2, y1 - 2, x2 + 2, y2 + 2], outline=color, width=2)
        label = str(el['id'])
        tw = d.textlength(label, font=tag_font) + 8
        tx, ty = x1 - 2, y1 - 2 - 19
        if ty < pad:
            ty = y2 + 3
        d.rectangle([tx, ty, tx + tw, ty + 19], fill=color)
        d.text((tx + 4, ty + 9.5), label, font=tag_font, fill='white', anchor='lm')
    if cursor and pad <= at(*cursor)[0] < pad + w and pad <= at(*cursor)[1] < pad + h:
        cx, cy = at(*cursor)
        d.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], outline='#00b8ff', width=3)
        d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill='#00b8ff')
        d.text((cx + 17, cy - 17), 'курсор', font=_font(14), fill='#00b8ff')
    return _with_code(canvas, code)


def zoom_view(img: Image.Image, cx: int, cy: int, half: int = 120, scale: int = 4,
              step: int = 10, label_every: int = 2, pad: int = 48,
              mark: Optional[Tuple[int, int]] = None, code: Optional[str] = None) -> Image.Image:
    """
    Увеличенный вырез вокруг (cx, cy) с мелкой сеткой. Подписи — экранные px,
    поэтому ответ по нему сразу пригоден для клика. mark — красный крестик
    на прошлой оценке, чтобы ИИ видел, что именно уточняет.
    """
    W, H = img.size
    x0, y0 = max(0, cx - half), max(0, cy - half)
    x1, y1 = min(W, cx + half), min(H, cy + half)
    crop = img.convert('RGB').crop((x0, y0, x1, y1))
    big = crop.resize((crop.width * scale, crop.height * scale), Image.NEAREST)
    w, h = big.size

    canvas = Image.new('RGB', (w + 2 * pad, h + 2 * pad), BAND)
    canvas.paste(big, (pad, pad))
    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    _grid(layer, pad, (x0, y0), scale, step, w, h)
    canvas = Image.alpha_composite(canvas.convert('RGBA'), layer).convert('RGB')

    d = ImageDraw.Draw(canvas)
    _rulers(d, w, h, pad, (x0, y0), scale, step, label_every, _font(16))
    if mark:
        mx, my = pad + (mark[0] - x0) * scale, pad + (mark[1] - y0) * scale
        d.line([(mx - 14, my), (mx + 14, my)], fill='red', width=3)
        d.line([(mx, my - 14), (mx, my + 14)], fill='red', width=3)
    return _with_code(canvas, code)
