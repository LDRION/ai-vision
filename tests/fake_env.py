"""
Виртуальный экран для многошагового агента: синтетические Яндекс, выдача,
Почта, письмо, вход, настройки с ползунком и канбан-доска — реагируют на
клики, наведение, перетаскивание и ввод, как настоящие страницы.
Тот же интерфейс, что aivision.screen.RealEnv, но мышь пользователя не трогается.

Мышь — как в браузере: клик засчитывается на отпускании кнопки; сдвинулись
с зажатой кнопкой больше 8 px — это перетаскивание, а не клик. Меню «Ещё»
открыто, пока курсор над ним. Карточки доски и бегунок громкости двигаются
только перетаскиванием — клик их лишь выделяет / ничего не делает.
"""

import math
from typing import Callable, List, Optional, Tuple

from PIL import Image, ImageDraw

from aivision.detect import find_elements
from tests.fake_page import _f, make_page

W, H = 1920, 1080

LETTERS = [
    ('Анна Смирнова', 'anna@example.ru', 'Документы к встрече в четверг', '10:42',
     ['Привет! Прикладываю договор и смету к встрече.', 'Встречаемся в четверг в 15:00, переговорная 4.',
      'Если будут правки — пиши до среды.']),
    ('Яндекс Маркет', 'market@example.ru', 'Ваш заказ передан в доставку', 'вчера',
     ['Заказ 48213 передан в службу доставки.', 'Ожидаемая дата доставки — 16 октября.']),
    ('Сергей Петров', 'sergey@example.ru', 'Re: отчёт за сентябрь', '12 окт',
     ['Посмотрел отчёт, всё ок.', 'Цифры по выручке сходятся с бухгалтерией.']),
]


class FakeEnv:
    def __init__(self, page: str = 'home'):
        self.page, self.query, self.focus = page, '', None
        self.login, self.select_all, self.ctrl, self.alt = '', False, False, False
        self.letter = None
        self.pos = (960, 900)
        self.back: List[str] = []
        self.visited = [page]
        self.regions: List[Tuple[list, Callable[[], None]]] = []
        self.drags: List[Tuple[list, Callable[[Tuple[int, int]], None]]] = []
        self.hovers: List[Tuple[list, str]] = []
        self.hovered: Optional[str] = None
        self.pressed_at: Optional[Tuple[int, int]] = None
        self.volume = 80
        self.cards = {'todo': ['Подготовить отчёт', 'Созвон с дизайнером'],
                      'doing': ['Вёрстка главной'], 'done': ['Купить домен']}
        self.selected: Optional[str] = None
        self.opened: Optional[str] = None          # карточка, открытая двойным кликом
        self.archived: List[str] = []
        self.last_up = None
        self.dbl: List[Tuple[list, Callable[[], None]]] = []

    # ── интерфейс среды ────────────────────────────────────────────────────
    def grab(self):
        return self._render(), (0, 0)

    def title(self) -> str:
        return {
            'home': 'Яндекс — быстрый поиск в интернете',
            'results': f'{self.query} — Яндекс: нашлось 5 млн результатов',
            'mail': 'Входящие — Яндекс Почта',
            'letter': f'{LETTERS[self.letter][2]} — Яндекс Почта' if self.letter is not None else 'Яндекс Почта',
            'login': 'Авторизация — Яндекс ID',
            'settings': 'Настройки — Яндекс',
            'board': 'Доска «Запуск сайта» — Трекер',
        }[self.page]

    def window(self) -> int:
        return 1

    def cursor(self):
        return self.pos

    def elements(self, img, offset):
        return find_elements(img, None)

    def settle(self, before):
        return self.grab()

    def perform(self, events):
        self._render()                          # актуальные области кликов
        self.last_up = None                     # t событий у каждого действия свой, с нуля
        for ev in events:
            e = ev['e']
            if e == 'move':
                self.pos = (ev['x'], ev['y'])
            elif e == 'click' and ev['btn'] == 'left':
                self.pos = (ev['x'], ev['y'])
                if ev['down']:
                    self.pressed_at = self.pos
                elif self.pressed_at:
                    src, self.pressed_at = self.pressed_at, None
                    if math.dist(src, self.pos) > 8:
                        self._drop(src, self.pos)
                    else:
                        self._click(*self.pos)
                        last, self.last_up = self.last_up, (ev['t'], self.pos)
                        if last and ev['t'] - last[0] < 0.5 and math.dist(last[1], self.pos) < 6:
                            self._render()
                            self._dblclick(*self.pos)
                            self.last_up = None
                    self._render()
            elif e == 'key':
                self._key(ev['key'], ev['down'])
                self._render()
        self._hover()

    # ── поведение ─────────────────────────────────────────────────────────
    def _go(self, page: str):
        self.back.append(self.page)
        self.page, self.focus = page, None
        self.visited.append(page)

    def _click(self, x, y):
        for box, fn in self.regions:
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                fn()
                return
        self.focus = None

    def _dblclick(self, x, y):
        for box, fn in self.dbl:
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                fn()
                return

    def _drop(self, src, dst):
        for box, fn in self.drags:
            if box[0] <= src[0] <= box[2] and box[1] <= src[1] <= box[3]:
                fn(dst)
                return

    def _hover(self):
        self._render()
        x, y = self.pos
        self.hovered = next((name for box, name in self.hovers
                             if box[0] <= x <= box[2] and box[1] <= y <= box[3]), None)

    def _key(self, key: str, down: bool):
        if key == 'Key.ctrl':
            self.ctrl = down
            return
        if key == 'Key.alt':
            self.alt = down
            return
        if not down:
            return
        if self.alt and key == 'Key.left' and self.back:
            self.page = self.back.pop()
            return
        if self.focus == 'slider' and key in ('Key.left', 'Key.down', 'Key.right', 'Key.up'):
            self.volume = max(0, min(100, self.volume + (1 if key in ('Key.right', 'Key.up') else -1)))
            return
        field = {'search': 'query', 'login': 'login'}.get(self.focus)
        if self.ctrl and key == 'a':
            self.select_all = True
            return
        if key == 'Key.enter':
            if self.focus == 'search' and self.query:
                self._go('results')
            return
        if not field:
            return
        if key == 'Key.backspace':
            setattr(self, field, '' if self.select_all else getattr(self, field)[:-1])
            self.select_all = False
        elif len(key) == 1 and not self.ctrl:
            base = '' if self.select_all else getattr(self, field)
            setattr(self, field, base + key)
            self.select_all = False

    def _focus(self, name):
        def fn():
            self.focus = name
        return fn

    def _open_letter(self, i):
        def fn():
            self.letter = i
            self._go('letter')
        return fn

    # ── отрисовка ─────────────────────────────────────────────────────────
    def _render(self) -> Image.Image:
        self.regions, self.drags, self.hovers, self.dbl = [], [], [], []
        return getattr(self, f'_page_{self.page}')()

    def _search_box(self, d, x1, y1, x2, y2, button: bool = True):
        d.rounded_rectangle([x1, y1, x2, y2], 12, outline='#ffcc00', width=3)
        cy = (y1 + y2) // 2
        if self.query:
            d.text((x1 + 25, cy), self.query, font=_f(20), fill='#000', anchor='lm')
        else:
            d.text((x1 + 25, cy), 'Найдётся всё', font=_f(20), fill='#999', anchor='lm')
        self.regions.append(([x1, y1, x2 - 130, y2], self._focus('search')))
        if button:
            d.rounded_rectangle([x2 - 130, y1 + 4, x2 - 4, y2 - 4], 10, fill='#ffdb4d')
            d.text((x2 - 67, cy), 'Найти', font=_f(20, True), fill='#000', anchor='mm')
            self.regions.append(([x2 - 130, y1, x2, y2], lambda: self.query and self._go('results')))

    def _page_home(self):
        img, truth = make_page()
        d = ImageDraw.Draw(img)
        d.rectangle([566, 386, 1180, 434], fill='white')
        self._search_box(d, 560, 380, 1360, 440)
        self.regions += [
            (truth['link_Почта'], lambda: self._go('mail')),
            (truth['btn_login'], lambda: self._go('login')),
        ]
        # «Ещё» — меню по наведению: открыто, пока курсор над пунктом или над самим меню
        d.text((613, 24), 'Ещё', font=_f(17), fill='#333')
        d.polygon([(652, 33), (662, 33), (657, 39)], fill='#333')
        link = [605, 18, 668, 52]
        if self.hovered == 'more':
            menu = [600, 54, 840, 196]
            d.rectangle([menu[0] + 3, menu[1] + 3, menu[2] + 3, menu[3] + 3], fill='#dddddd')
            d.rectangle(menu, fill='white', outline='#cccccc')
            for i, item in enumerate(['Настройки', 'Помощь', 'О компании']):
                y = 66 + i * 42
                d.text((620, y), item, font=_f(18), fill='#222')
                if item == 'Настройки':
                    self.regions.append(([600, y - 8, 840, y + 30], lambda: self._go('settings')))
            self.hovers.append(([600, 18, 840, 196], 'more'))
        else:
            self.hovers.append((link, 'more'))
        return img

    def _page_settings(self):
        img = Image.new('RGB', (W, H), 'white')
        d = ImageDraw.Draw(img)
        self._header(d)
        d.text((700, 160), 'Настройки', font=_f(34, True), fill='#000')
        d.text((700, 300), f'Громкость уведомлений: {self.volume}%', font=_f(22), fill='#111')
        x0, x1, y = 700, 1200, 400
        d.rounded_rectangle([x0, y - 4, x1, y + 4], 4, fill='#dddddd')
        kx = x0 + (x1 - x0) * self.volume // 100
        d.rounded_rectangle([x0, y - 4, kx, y + 4], 4, fill='#ffcc00')
        if self.focus == 'slider':
            d.ellipse([kx - 19, y - 19, kx + 19, y + 19], outline='#4b8bf5', width=3)
        d.ellipse([kx - 14, y - 14, kx + 14, y + 14], fill='white', outline='#888', width=2)
        d.text((x0, y + 30), '0%', font=_f(17), fill='#666', anchor='mt')
        d.text((x1, y + 30), '100%', font=_f(17), fill='#666', anchor='mt')

        def drop(dst):
            self.volume = max(0, min(100, round((dst[0] - x0) / (x1 - x0) * 100)))

        self.drags.append(([kx - 20, y - 20, kx + 20, y + 20], drop))   # клик по шкале — ничего
        self.regions.append(([kx - 20, y - 20, kx + 20, y + 20], self._focus('slider')))  # фокус → стрелки
        return img

    def _page_board(self):
        img = Image.new('RGB', (W, H), '#f4f5f7')
        d = ImageDraw.Draw(img)
        d.text((80, 40), 'Доска «Запуск сайта»', font=_f(30, True), fill='#172b4d')
        cols = [('todo', 'Нужно сделать', 80), ('doing', 'В работе', 700), ('done', 'Готово', 1320)]
        for key, name, x0 in cols:
            d.rounded_rectangle([x0, 110, x0 + 520, 1000], 14, fill='#ebecf0')
            d.text((x0 + 30, 135), name, font=_f(22, True), fill='#172b4d')
            for i, card in enumerate(self.cards[key]):
                y = 190 + i * 100
                sel = card == self.selected
                d.rounded_rectangle([x0 + 30, y, x0 + 490, y + 80], 10, fill='white',
                                    outline='#0c66e4' if sel else '#d0d4db', width=3 if sel else 1)
                d.text((x0 + 55, y + 40), card, font=_f(20), fill='#172b4d', anchor='lm')
                if self.opened:
                    continue                           # под модальным окном доска не кликается
                box = [x0 + 30, y, x0 + 490, y + 80]
                self.regions.append((box, (lambda c: lambda: setattr(self, 'selected', c))(card)))
                self.drags.append((box, (lambda k, c: lambda dst: self._move_card(k, c, dst))(key, card)))
                self.dbl.append((box, (lambda c: lambda: setattr(self, 'opened', c))(card)))
        if self.opened:
            # Окно карточки: архивировать можно только отсюда
            layer = Image.new('RGBA', img.size, (9, 30, 66, 120))
            img = Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([560, 220, 1360, 760], 16, fill='white')
            d.text((610, 270), self.opened, font=_f(30, True), fill='#172b4d')
            d.text((610, 340), 'Описание', font=_f(20, True), fill='#44546f')
            d.text((610, 380), 'Добавьте более подробное описание…', font=_f(19), fill='#8590a2')
            for label, (bx, by), fn in [('Архивировать', (1090, 620), self._archive),
                                        ('Закрыть', (610, 620), lambda: setattr(self, 'opened', None))]:
                d.rounded_rectangle([bx, by, bx + 220, by + 60], 10, fill='#e9f2ff' if label == 'Закрыть' else '#ffece8')
                d.text((bx + 110, by + 30), label, font=_f(20, True), fill='#172b4d', anchor='mm')
                self.regions.append(([bx, by, bx + 220, by + 60], fn))
        return img

    def _archive(self):
        for col in self.cards.values():
            if self.opened in col:
                col.remove(self.opened)
        self.archived.append(self.opened)
        self.opened = self.selected = None

    def _move_card(self, src_col, card, dst):
        cols = [('todo', 80), ('doing', 700), ('done', 1320)]
        target = next((k for k, x0 in cols if x0 <= dst[0] <= x0 + 520 and 110 <= dst[1] <= 1000), None)
        if target and target != src_col:
            self.cards[src_col].remove(card)
            self.cards[target].append(card)
            self.selected = None

    def _header(self, d, logo='Яндекс'):
        d.text((40, 40), logo, font=_f(34, True), fill='#fc3f1d', anchor='lm')
        self.regions.append(([30, 15, 190, 65], lambda: self._go('home')))

    def _page_results(self):
        img = Image.new('RGB', (W, H), 'white')
        d = ImageDraw.Draw(img)
        self._header(d)
        self._search_box(d, 220, 15, 1000, 70)
        if 'погод' in self.query.lower():
            d.rounded_rectangle([220, 110, 1000, 330], 16, fill='#eef4ff')
            d.text((250, 140), 'Погода в Москве сейчас', font=_f(24, True), fill='#000')
            d.text((250, 185), '+12°', font=_f(72, True), fill='#000')
            d.text((450, 200), 'Облачно, небольшой дождь', font=_f(22), fill='#333')
            d.text((450, 240), 'Ощущается как +9°  ·  ветер 4 м/с', font=_f(18), fill='#555')
            links = ['Погода в Москве на 10 дней — Яндекс Погода', 'Гисметео: погода в Москве на неделю']
        else:
            d.text((250, 140), f'По запросу «{self.query}» найдено 5 млн результатов', font=_f(20), fill='#333')
            links = [f'{self.query} — Википедия', f'{self.query}: новости и статьи']
        for i, t in enumerate(links):
            d.text((250, 380 + i * 60), t, font=_f(20), fill='#1a0dab')
        return img

    def _page_mail(self):
        img = Image.new('RGB', (W, H), 'white')
        d = ImageDraw.Draw(img)
        self._header(d, 'Яндекс Почта')
        d.rounded_rectangle([30, 100, 230, 150], 12, fill='#ffdb4d')
        d.text((130, 125), 'Написать', font=_f(20, True), fill='#000', anchor='mm')
        for i, f in enumerate(['Входящие  3', 'Отправленные', 'Спам', 'Корзина']):
            d.text((50, 190 + i * 44), f, font=_f(19, i == 0), fill='#222')
        d.line([(260, 90), (260, H)], fill='#e5e5e5', width=2)
        for i, (name, _addr, subj, when, _body) in enumerate(LETTERS):
            y = 110 + i * 64
            d.text((300, y + 20), name, font=_f(19, True), fill='#000', anchor='lm')
            d.text((560, y + 20), subj, font=_f(19), fill='#222', anchor='lm')
            d.text((1840, y + 20), when, font=_f(17), fill='#888', anchor='rm')
            d.line([(280, y + 52), (1880, y + 52)], fill='#eeeeee', width=1)
            self.regions.append(([280, y, 1880, y + 50], self._open_letter(i)))
        return img

    def _page_letter(self):
        img = Image.new('RGB', (W, H), 'white')
        d = ImageDraw.Draw(img)
        self._header(d, 'Яндекс Почта')
        name, addr, subj, when, body = LETTERS[self.letter]
        d.text((300, 110), '← Назад', font=_f(19), fill='#1a0dab')
        self.regions.append(([290, 100, 400, 140], lambda: self._go('mail')))
        d.text((300, 170), subj, font=_f(30, True), fill='#000')
        d.text((300, 230), f'От: {name} <{addr}>   ·   {when}', font=_f(19), fill='#444')
        for i, line in enumerate(body):
            d.text((300, 300 + i * 40), line, font=_f(20), fill='#111')
        return img

    def _page_login(self):
        img = Image.new('RGB', (W, H), '#f5f5f5')
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([710, 200, 1210, 720], 24, fill='white')
        d.text((960, 270), 'Яндекс ID', font=_f(30, True), fill='#000', anchor='mm')
        d.text((960, 330), 'Войдите, чтобы продолжить', font=_f(20), fill='#555', anchor='mm')
        d.rounded_rectangle([760, 390, 1160, 450], 10, outline='#bbb', width=2)
        d.text((780, 420), self.login or 'Логин или email', font=_f(20),
               fill='#000' if self.login else '#999', anchor='lm')
        self.regions.append(([760, 390, 1160, 450], self._focus('login')))
        d.rounded_rectangle([760, 480, 1160, 540], 10, outline='#bbb', width=2)
        d.text((780, 510), 'Пароль', font=_f(20), fill='#999', anchor='lm')
        d.rounded_rectangle([760, 580, 1160, 640], 12, fill='#ffdb4d')
        d.text((960, 610), 'Войти', font=_f(22, True), fill='#000', anchor='mm')
        return img
