"""
Палитра и шрифты лаунчера — чистый светлый дашборд (по мотивам hhpilot/Откликс):
светло-лавандовый фон, белые карточки, бирюзово-зелёный акцент, мягкие скругления,
цветные бейджи статусов. Один источник правды: меняем тему здесь, а не по месту.
"""

import tkinter.font as tkfont

# Manrope, если установлен (как в hhpilot), иначе — системный Segoe UI.
FONT_FAMILY = 'Manrope'
FALLBACK = 'Segoe UI'

PALETTE = {
    'bg':            '#eceef6',   # фон окна — мягкий лавандово-светлый
    'surface':       '#ffffff',   # карточки, панели
    'surface_muted': '#f1f3f9',   # вторичные поверхности, hover
    'border':        '#e5e8f1',   # тонкие границы
    'text':          '#1e2330',   # основной текст (тёмный сланец)
    'text_muted':    '#7b808d',   # подписи
    'accent':        '#10b981',   # акцент — бирюзово-зелёный (главные кнопки, выбор)
    'accent_hover':  '#0ea371',
    'accent_text':   '#ffffff',
    'accent_soft':   '#e3f6ee',   # светлая мята — выделение выбранного пункта
    'danger':        '#e0455c',
    'danger_soft':   '#fdeaee',
    'ok':            '#12875a',
    'field':         '#ffffff',
    'field_border':  '#dde1ec',
    'track':         '#e5e8f1',   # жёлоб слайдера
    'dark':          '#1f2430',   # тёмные карточки-подсказки (toast), статус-панель
    'dark_text':     '#e8eaf0',
}

# Бейджи статусов (фон, текст) — как цветные пилюли в hhpilot.
BADGES = {
    'green':  ('#e6f6ef', '#12875a'),   # успех / отправлено
    'teal':   ('#e3f6ee', '#0e9f6e'),   # активно / готово
    'orange': ('#fdf0e2', '#c47b2a'),   # в процессе / внимание
    'purple': ('#eee9fb', '#7c5cd6'),   # коммуникация / ответ
    'gray':   ('#eef0f6', '#6b7280'),   # нейтральное / пропуск
    'rose':   ('#fdeaee', '#d6455c'),   # счётчик / ошибка
}

RADIUS = 12
CARD_RADIUS = 18        # крупные карточки-панели
PAD = 16


def resolve_family() -> str:
    """Manrope, если шрифт реально установлен в системе, иначе Segoe UI."""
    try:
        return FONT_FAMILY if FONT_FAMILY in tkfont.families() else FALLBACK
    except Exception:
        return FALLBACK


def fonts():
    """Набор шрифтов разного размера — вызывать после создания корневого окна."""
    fam = resolve_family()
    return {
        'title':   (fam, 20, 'bold'),
        'h2':      (fam, 15, 'bold'),
        'body':    (fam, 13),
        'body_b':  (fam, 13, 'bold'),
        'small':   (fam, 11),
        'mono':    ('Consolas', 12),
    }
