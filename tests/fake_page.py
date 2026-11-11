"""
Синтетическая «главная Яндекса» 1920×1080 с известными координатами элементов —
стенд для замера точности наведения ИИ без реального экрана.

    python tests/fake_page.py out_dir   →  page.png + truth.json
"""

import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = 'C:/Windows/Fonts/segoeui.ttf'
FONT_B = 'C:/Windows/Fonts/segoeuib.ttf'


def _f(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def make_page():
    W, H = 1920, 1080
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)
    truth = {}

    def text_el(name, xy, text, size=18, fill='#222', bold=False):
        font = _f(size, bold)
        d.text(xy, text, font=font, fill=fill)
        x1, y1, x2, y2 = d.textbbox(xy, text, font=font)
        truth[name] = [x1, y1, x2, y2]

    # Верхнее меню сервисов
    x = 40
    for name in ['Почта', 'Карты', 'Маркет', 'Музыка', 'Кинопоиск', 'Погода']:
        text_el(f'link_{name}', (x, 24), name, 17, '#333')
        x = truth[f'link_{name}'][2] + 36

    # Справа: шестерёнка (только иконка) и кнопка «Войти»
    gx, gy = 1700, 38
    for i in range(8):
        a = i * math.pi / 4
        d.line([(gx, gy), (gx + 14 * math.cos(a), gy + 14 * math.sin(a))], fill='#555', width=5)
    d.ellipse([gx - 10, gy - 10, gx + 10, gy + 10], fill='#555')
    d.ellipse([gx - 4, gy - 4, gx + 4, gy + 4], fill='white')
    truth['icon_settings'] = [gx - 16, gy - 16, gx + 16, gy + 16]

    d.rounded_rectangle([1750, 18, 1880, 60], 10, fill='#ffdb4d')
    d.text((1815, 39), 'Войти', font=_f(18, True), fill='#000', anchor='mm')
    truth['btn_login'] = [1750, 18, 1880, 60]

    # Логотип
    d.text((960, 300), 'Яндекс', font=_f(72, True), fill='#fc3f1d', anchor='mm')

    # Поисковая строка: поле, микрофон (иконка), кнопка «Найти»
    d.rounded_rectangle([560, 380, 1360, 440], 12, outline='#ffcc00', width=3)
    d.text((585, 410), 'Найдётся всё', font=_f(20), fill='#999', anchor='lm')
    truth['search_input'] = [563, 383, 1225, 437]
    mx, my = 1195, 410
    d.rounded_rectangle([mx - 6, my - 14, mx + 6, my + 4], 6, fill='#444')
    d.arc([mx - 11, my - 8, mx + 11, my + 10], 0, 180, fill='#444', width=3)
    d.line([(mx, my + 10), (mx, my + 16)], fill='#444', width=3)
    truth['icon_mic'] = [mx - 13, my - 16, mx + 13, my + 18]
    d.rounded_rectangle([1230, 384, 1356, 436], 10, fill='#ffdb4d')
    d.text((1293, 410), 'Найти', font=_f(20, True), fill='#000', anchor='mm')
    truth['btn_search'] = [1230, 384, 1356, 436]

    # Плитки сервисов (иконки с подписями)
    colors = ['#4b8bf5', '#34a853', '#fbbc05', '#ea4335', '#9c27b0', '#00acc1']
    labels = ['Диск', 'Такси', 'Еда', 'Афиша', 'Путешествия', 'Здоровье']
    for i, (c, lbl) in enumerate(zip(colors, labels)):
        cx = 660 + i * 120
        d.rounded_rectangle([cx - 30, 500, cx + 30, 560], 14, fill=c)
        d.text((cx, 580), lbl, font=_f(15), fill='#444', anchor='mt')
        truth[f'tile_{lbl}'] = [cx - 44, 500, cx + 44, 596]  # иконка + подпись

    # Новости
    news = [
        'Курс рубля на 14 октября: доллар и евро на Московской бирже',
        'В Москве ожидается до +12 градусов и небольшой дождь',
        'Учёные представили новый способ хранения энергии',
        'Сборная провела первую тренировку перед матчем',
    ]
    d.text((560, 660), 'Новости', font=_f(22, True), fill='#000')
    for i, line in enumerate(news):
        text_el(f'news_{i + 1}', (560, 705 + i * 38), line, 18, '#1a0dab')

    # Крестик закрытия рекламного баннера (только иконка)
    d.rectangle([1480, 660, 1860, 900], fill='#f2f2f2')
    d.text((1670, 780), 'Реклама', font=_f(20), fill='#aaa', anchor='mm')
    d.line([(1832, 672), (1848, 688)], fill='#666', width=3)
    d.line([(1848, 672), (1832, 688)], fill='#666', width=3)
    truth['icon_close_ad'] = [1828, 668, 1852, 692]

    return img, truth


if __name__ == '__main__':
    out = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    out.mkdir(parents=True, exist_ok=True)
    img, truth = make_page()
    img.save(out / 'page.png')
    (out / 'truth.json').write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{out / "page.png"}  ({len(truth)} элементов)')
