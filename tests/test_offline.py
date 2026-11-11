"""
Конвейер без сети: ответ Siberi подменяется. Проверяет разметку, разбор ответа,
политику «видела / не видела картинку», уточнение zoom и траекторию.

    python -m pytest tests -q      или      python tests/test_offline.py
"""

import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['AV_RETINA'] = '0'     # живая Retina на машине не должна влиять на тесты
os.environ['AV_LOCAL_BRAIN'] = '0'  # и локальная модель Ollama тоже (в своих тестах — подмена)
os.environ['SIBERI_BASE_URL'] = 'http://siberi.test.invalid/api/v1/ai'  # мозг — подмена (FakeSiberi), Siberi Local не поднимаем

from aivision import agent, siberi                      # noqa: E402
from aivision.agent import NotSeen, locate, parse_json  # noqa: E402
from aivision.detect import ocr_elements                # noqa: E402
from aivision.loop import run_task                      # noqa: E402
from aivision.motion import build_events, parse_keys    # noqa: E402
from tests.fake_env import FakeEnv                      # noqa: E402
from tests.fake_page import _f, make_page               # noqa: E402


IMG, TRUTH = make_page()
ELEMENTS = ocr_elements(IMG)


def _el(text):
    return next(e for e in ELEMENTS if e['text'] == text)


def _inside(name, x, y):
    x1, y1, x2, y2 = TRUTH[name]
    return x1 <= x <= x2 and y1 <= y <= y2


class FakeSiberi:
    """Отвечает по сценарию; seen=True — «видит» код на картинке (читает его из рендера)."""

    def __init__(self, answers, seen=True):
        self.answers, self.seen, self.calls = list(answers), seen, 0
        self.images = []           # по вызовам: была ли картинка в запросе
        self.prompts = []          # тексты запросов — проверить, что модели показали
        self.systems = []          # системные промты запросов
        self.image_counts = []     # сколько картинок в каждом запросе
        self.shown = []            # по картинкам с метками: вырез (region) или весь экран (None)
        self._render = agent.compose, agent.zoom_view
        self.code = None

    def _spy(self, fn):
        def wrapped(*a, **kw):
            self.code = kw.get('code')
            if fn is self._render[0]:
                self.shown.append(kw.get('region'))
            return fn(*a, **kw)
        return wrapped

    def __enter__(self):
        agent.compose, agent.zoom_view = self._spy(agent.compose), self._spy(agent.zoom_view)
        self._ask = siberi.ask
        siberi.ask = self.ask
        return self

    def __exit__(self, *_):
        agent.compose, agent.zoom_view = self._render
        siberi.ask = self._ask

    def ask(self, system, prompt, image_b64=None, **_):
        self.calls += 1
        self.images.append(bool(_.get('images') or image_b64))
        self.image_counts.append(len(_.get('images') or ([image_b64] if image_b64 else [])))
        self.prompts.append(prompt)
        self.systems.append(system)
        nxt = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        ans = dict(nxt(prompt) if callable(nxt) else nxt)
        ans['code'] = self.code if self.seen else None
        return '```json\n' + json.dumps(ans, ensure_ascii=False) + '\n```'


def test_parse_json_wrapped():
    assert parse_json('Вот ответ: ```json\n{"a": 1}\n``` готово') == {'a': 1}


def test_ocr_finds_text_targets():
    for text in ('Войти', 'Найти', 'Маркет', 'x'):
        assert _el(text)


def test_mark_seen():
    with FakeSiberi([{'action': 'click', 'mark': _el('Войти')['id']}]) as f:
        r = locate(IMG, 'войти', elements=ELEMENTS, log=lambda m: None)
    assert f.calls == 1 and r['seen'] and r['source'] == 'mark'
    assert _inside('btn_login', r['x'], r['y'])


def test_mark_blind_accepted_from_list():
    with FakeSiberi([{'action': 'click', 'mark': _el('Найти')['id']}], seen=False) as f:
        r = locate(IMG, 'найти', elements=ELEMENTS, log=lambda m: None)
    assert f.calls == 1 and not r['seen']
    assert _inside('btn_search', r['x'], r['y'])


def test_blind_coordinates_rejected():
    with FakeSiberi([{'action': 'click', 'x': 1855, 'y': 40}], seen=False) as f:
        try:
            locate(IMG, 'шестерёнка', elements=ELEMENTS, log=lambda m: None)
            assert False, 'слепые координаты не должны приниматься'
        except NotSeen:
            pass
    assert f.calls == agent.VISION_RETRIES


def test_grid_then_zoom_refine():
    answers = [{'action': 'click', 'x': 1690, 'y': 50},          # грубо по сетке
               {'found': True, 'x': 1700, 'y': 38}]              # уточнение
    with FakeSiberi(answers) as f:
        r = locate(IMG, 'шестерёнка', elements=ELEMENTS, log=lambda m: None)
    assert f.calls == 2 and r['source'] == 'zoom'
    assert _inside('icon_settings', r['x'], r['y'])


def test_zoom_hallucination_ignored():
    answers = [{'action': 'click', 'x': 1700, 'y': 40}, {'found': True, 'x': 300, 'y': 900}]
    with FakeSiberi(answers):
        r = locate(IMG, 'шестерёнка', elements=ELEMENTS, log=lambda m: None)
    assert r['source'] == 'grid' and (r['x'], r['y']) == (1700, 40)


def test_events_land_inside_and_click():
    b = TRUTH['btn_login']
    for _ in range(30):
        ev = build_events((200, 900), {'action': 'click', 'x': 0, 'y': 0, 'box': b})
        moves = [e for e in ev if e['e'] == 'move']
        clicks = [e for e in ev if e['e'] == 'click']
        assert _inside('btn_login', moves[-1]['x'], moves[-1]['y'])
        assert [c['down'] for c in clicks] == [True, False]
        assert all(ev[i]['t'] <= ev[i + 1]['t'] for i in range(len(ev) - 1))
        assert 0.15 < moves[-1]['t'] < 2.5


def test_type_action_emits_text():
    ev = build_events((0, 0), {'action': 'type', 'x': 900, 'y': 410, 'box': None, 'text': 'Привет'})
    typed = ''.join(e['key'] for e in ev if e['e'] == 'key' and e['down'])
    assert typed == 'Привет'


def by_text(label, **kw):
    """Ответ «выбери элемент с этим текстом» — номер берётся из списка в промте."""
    def answer(prompt):
        m = re.search(r'\[(\d+)\] [^«\n]*«' + re.escape(label), prompt)
        assert m, f'в списке элементов нет «{label}»'
        return dict(kw, mark=int(m.group(1)))
    return answer


def test_keys_parse_and_events():
    assert parse_keys('ctrl+a, backspace') == [['ctrl', 'a'], ['backspace']]
    ev = build_events((0, 0), {'action': 'key', 'x': None, 'y': None, 'keys': 'ctrl+l enter'})
    downs = [(e['key'], e['vk']) for e in ev if e['down']]
    assert downs == [('Key.ctrl', None), ('l', 0x4C), ('Key.enter', None)]
    assert not [e for e in ev if e['e'] == 'move']


def test_loop_weather_search():
    env = FakeEnv()
    answers = [by_text('Найдётся всё', action='type', text='погода москва', keys='enter'),
               {'action': 'done', 'answer': 'В Москве +12°'}]
    with FakeSiberi(answers):
        r = run_task('узнай погоду в Москве', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and '+12' in r['answer']
    assert env.page == 'results' and env.query == 'погода москва'
    assert 'заметно изменился' in r['history'][0] and 'окно' in r['history'][0]


def test_loop_open_latest_letter():
    env = FakeEnv()
    answers = [by_text('Почта', action='click'), by_text('Анна Смирнова', action='click'),
               {'action': 'done', 'answer': 'От Анны Смирновой'}]
    with FakeSiberi(answers):
        r = run_task('открой последнее письмо', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and r['steps'] == 3
    assert env.page == 'letter' and env.letter == 0


def test_loop_stops_when_stuck():
    env = FakeEnv()
    with FakeSiberi([{'action': 'click', 'x': 1500, 'y': 980}]):
        r = run_task('нажми на пустое место', env, refine=False, log=lambda m: None)
    assert r['status'] == 'stuck' and r['steps'] == 5
    assert ['НЕ изменился' in h for h in r['history'][:2]] == [True, True]
    assert all('ОТКЛОНЕНО' in h for h in r['history'][2:])     # третий раз то же самое не исполняем


def test_drag_events_hold_button_while_moving():
    ev = build_events((100, 900), {'action': 'drag', 'x': 300, 'y': 230, 'box': None,
                                   'to': {'x': 1580, 'y': 400, 'box': None}})
    clicks = [e for e in ev if e['e'] == 'click']
    assert [c['down'] for c in clicks] == [True, False]
    down_i, up_i = ev.index(clicks[0]), ev.index(clicks[1])
    held_moves = [e for e in ev[down_i:up_i] if e['e'] == 'move']
    assert len(held_moves) > 20                                  # тащим с зажатой кнопкой
    assert abs(clicks[0]['x'] - 300) <= 1 and abs(clicks[0]['y'] - 230) <= 1      # ±1 px — «рука»
    assert (clicks[1]['x'], clicks[1]['y']) == (1580, 400)
    assert all(ev[i]['t'] <= ev[i + 1]['t'] for i in range(len(ev) - 1))


def test_hover_and_long_press_events():
    hv = build_events((0, 0), {'action': 'hover', 'x': 630, 'y': 34, 'box': None})
    assert not [e for e in hv if e['e'] == 'click'] and hv[-1]['e'] == 'move'
    lp = build_events((0, 0), {'action': 'long_press', 'x': 500, 'y': 500, 'box': None, 'seconds': 1.5})
    down, up = [e for e in lp if e['e'] == 'click']
    assert 1.4 < up['t'] - down['t'] < 1.7


def test_stroke_draws_polyline_with_button_held():
    pts = [[100, 100], [300, 120], [260, 400]]
    ev = build_events((50, 50), {'action': 'stroke', 'x': None, 'y': None, 'points': pts})
    clicks = [e for e in ev if e['e'] == 'click']
    assert [c['down'] for c in clicks] == [True, False]
    assert (clicks[0]['x'], clicks[0]['y']) == (100, 100)
    assert (clicks[1]['x'], clicks[1]['y']) == (260, 400)
    moves = [e for e in ev if e['e'] == 'move']
    held = [e for e in ev[ev.index(clicks[0]):ev.index(clicks[1])] if e['e'] == 'move']
    assert len(held) > 20                                  # сплошная линия, не два прыжка
    for p in pts:                                          # линия проходит через каждую вершину
        assert min((m['x'] - p[0]) ** 2 + (m['y'] - p[1]) ** 2 for m in moves) <= 9
    assert all(ev[i]['t'] <= ev[i + 1]['t'] for i in range(len(ev) - 1))


def test_clean_points_filters_and_clamps():
    from aivision.agent import _clean_points
    pts = _clean_points([[10, 20], {'x': 30, 'y': 40}, [30, 40], [-5, 9999], 'junk'], (800, 600))
    assert pts == [[10, 20], [30, 40], [0, 599]]          # дубль убран, точка зажата в кадр, мусор отброшен
    assert _clean_points([[1, 1]], (800, 600)) == [[1, 1]]
    assert _clean_points('nope', (800, 600)) is None


def test_loop_switches_from_click_to_drag():
    """Клик по пустому месту дважды впустую → третий клик отклонён → drag переносит карточку."""
    env = FakeEnv('board')
    answers = [{'action': 'click', 'x': 1580, 'y': 600}, {'action': 'click', 'x': 1580, 'y': 600},
               {'action': 'click', 'x': 1580, 'y': 600},
               by_text('Подготовить отчёт', action='drag', to_x=1580, to_y=600),
               {'action': 'done', 'answer': 'перенесла'}]
    with FakeSiberi(answers):
        r = run_task('перенеси «Подготовить отчёт» в Готово', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and 'Подготовить отчёт' in env.cards['done']
    assert any('ОТКЛОНЕНО' in h for h in r['history'])


def test_memory_dedupes_and_matches_by_app(tmp=None):
    import tempfile
    from aivision.memory import Memory
    path = Path(tempfile.mkdtemp()) / 'lessons.json'
    m = Memory(path)
    m.add([{'app': 'Трекер', 'text': 'Карточки переносятся только перетаскиванием, клик лишь выделяет'}])
    m.add([{'app': 'Трекер', 'text': 'карточки переносятся только перетаскиванием; клик лишь выделяет!'},
           {'app': '', 'text': 'Выпадающие меню часто открываются наведением без клика'}])
    assert len(m.lessons) == 2 and m.lessons[0]['confirmed'] == 2
    assert len(Memory(path).relevant('Доска «Запуск сайта» — Трекер')) == 2      # урок приложения + общий
    assert Memory(path).relevant('Входящие — Яндекс Почта') == ['Выпадающие меню часто открываются наведением без клика']


def test_loop_asks_hint_and_learns_it():
    import tempfile
    from aivision.memory import Memory
    mem = Memory(Path(tempfile.mkdtemp()) / 'lessons.json')
    env = FakeEnv('board')
    prompts = []
    answers = [{'action': 'fail', 'answer': 'не понимаю, как перенести'},
               lambda p: (prompts.append(p), by_text('Подготовить отчёт', action='drag', to_x=1580, to_y=600)(p))[1],
               {'action': 'done', 'answer': 'готово'},
               {'lessons': [{'app': 'Трекер', 'text': 'Карточку переносят перетаскиванием в нужную колонку'}]}]
    asked = []
    with FakeSiberi(answers):
        r = run_task('перенеси отчёт в Готово', env, refine=False, log=lambda m: None, memory=mem,
                     ask_hint=lambda s, why: asked.append((s, why)) or 'зажми карточку и тащи правее до упора')
    assert asked == [('fail', 'не понимаю, как перенести')]
    assert r['status'] == 'done' and 'Подготовить отчёт' in env.cards['done']
    assert 'ПОДСКАЗКИ ПОЛЬЗОВАТЕЛЯ' in prompts[0] and 'тащи правее' in prompts[0]
    assert 'Курсор сейчас в' in prompts[0]
    assert mem.relevant('Доска — Трекер') == ['Карточку переносят перетаскиванием в нужную колонку']


class GameEnv(FakeEnv):
    """Экран как в игре: каждый снимок — другой (мерцает фон), клики при этом работают."""
    def __init__(self):
        super().__init__('mail')
        self.frame = 0

    def grab(self):
        from PIL import ImageDraw
        img, off = super().grab()
        self.frame += 1
        d = ImageDraw.Draw(img)
        for i in range(0, img.width, 40):                                 # «анимация» по всему экрану
            d.rectangle([i, 0, i + 20, img.height], fill=(self.frame * 37 % 255, 120, 90))
        return img, off


def test_loop_acts_on_a_screen_that_never_stops_moving():
    env = GameEnv()
    logs = []
    answers = [{'action': 'click', 'x': 600, 'y': 130}, {'action': 'click', 'x': 600, 'y': 130},
               {'action': 'done', 'answer': 'открыто'}]
    with FakeSiberi(answers) as fs:
        r = run_task('открой письмо от Анны', env, refine=False, log=logs.append)
    assert r['status'] == 'done' and env.page == 'letter'                 # действие всё-таки выполнено
    assert sum('решаю заново' in m for m in logs) == 1                    # перерешила один раз, не по кругу
    assert any('всё время в движении' in m for m in logs)
    assert any('всё время в движении' in p for p in fs.prompts[2:])        # модель знает, что экран живой


def test_cast_moves_to_the_target_presses_the_skill_and_clicks():
    from aivision.motion import build_events
    ev = build_events((0, 0), {'action': 'cast', 'x': 900, 'y': 500, 'box': None, 'keys': 'q'})
    seq = [(e['e'], e.get('key') or e.get('btn'), e['down']) for e in ev if e['e'] != 'move']
    assert seq == [('key', 'q', True), ('key', 'q', False), ('click', 'left', True), ('click', 'left', False)]
    moves = [e for e in ev if e['e'] == 'move']
    assert moves and abs(moves[-1]['x'] - 900) <= 6 and abs(moves[-1]['y'] - 500) <= 6
    quick = build_events((0, 0), {'action': 'cast', 'x': 900, 'y': 500, 'box': None, 'keys': 'q', 'quick': True})
    assert [e['e'] for e in quick if e['e'] != 'move'] == ['key', 'key']       # быстрый каст — без ЛКМ


class LolEnv(GameEnv):
    """Игра: элементов нет (их и не спрашивают), «тишины» не ждут, окно матча выводится вперёд."""
    def __init__(self):
        super().__init__()
        self.focused, self.events = [], []

    def elements(self, img, offset):
        raise AssertionError('в игре OCR и UIA не нужны')

    def settle(self, before):
        raise AssertionError('в игре тишины не ждём')

    def focus_window(self, name):
        self.focused.append(name)
        return 'Блокнот «Без имени — Блокнот» · монитор 1' if 'Блокнот' in name else None

    def perform(self, events):
        self.events += events
        super().perform(events)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Hands:
    def __init__(self):
        import threading
        self.cmds, self.lock, self.offset, self.quick = [], threading.RLock(), (0, 0), False

    def run(self, cmd):
        self.cmds.append(cmd)


class _MatchEnv:
    def __init__(self, frames):
        self.frames = frames

    def grab(self):
        return self.frames[0], (0, 0)


def test_loop_replans_when_window_switched():
    class SwitchingEnv(FakeEnv):
        calls = 0

        def window(self):
            self.calls += 1
            return 2 if self.calls == 2 else 1      # окно сменилось ровно пока «думали» на шаге 1

    env = SwitchingEnv()
    answers = [by_text('Почта', action='click'), by_text('Почта', action='click'),
               {'action': 'done', 'answer': 'ok'}]
    with FakeSiberi(answers):
        r = run_task('открой почту', env, refine=False, log=lambda m: None)
    assert 'сменилось активное окно' in r['history'][0]
    assert env.page == 'mail' and r['status'] == 'done'


def test_play_stops_typing_when_focus_lost():
    from aivision import motion

    class Ctl:
        position = (0, 0)
        typed = []

        def press(self, k):
            Ctl.typed.append(k)

        def release(self, k):
            pass

    class Mouse(Ctl):
        def press(self, k):
            raise AssertionError('type без цели не должен кликать')

    windows = iter([7, 7, 99, 99, 99])
    saved = motion.mouse.Controller, motion.keyboard.Controller, motion.foreground_window
    motion.mouse.Controller, motion.keyboard.Controller = Mouse, Ctl
    motion.foreground_window = lambda: next(windows)
    try:
        ev = build_events((0, 0), {'action': 'type', 'x': None, 'y': None, 'text': 'абвгд'})
        try:
            motion.play(ev, speed=50)
            assert False, 'ввод должен был оборваться'
        except motion.Aborted:
            pass
    finally:
        motion.mouse.Controller, motion.keyboard.Controller, motion.foreground_window = saved
    assert len(Ctl.typed) == 2                     # «а», «б» — в своё окно, дальше стоп


def _record_mail_recipe(tmp):
    """Записать рецепт прогоном «почта → последнее письмо» через подменённую Siberi."""
    import aivision.recipe as R
    R.RECIPES = Path(tmp) / 'recipes'
    env = FakeEnv()
    run_dir = Path(tmp) / 'rec'
    run_dir.mkdir(parents=True)
    answers = [by_text('Почта', action='click'), by_text('Анна Смирнова', action='click'),
               {'action': 'done', 'answer': 'готово'}]
    with FakeSiberi(answers):
        run_task('открой почту и последнее письмо', env, refine=False, run_dir=run_dir, log=lambda m: None)
    return R, R.from_run('sc1', 'открой почту и последнее письмо', run_dir)


def test_recipe_records_steps_by_element():
    import tempfile
    _R, rec = _record_mail_recipe(tempfile.mkdtemp())
    assert [s.action for s in rec.steps] == ['click', 'click']
    assert rec.steps[0].target_text == 'Почта' and rec.steps[1].target_text == 'Анна Смирнова'


def test_replay_reproduces_without_siberi():
    import tempfile
    from aivision.replay import run_recipe
    _R, rec = _record_mail_recipe(tempfile.mkdtemp())
    env = FakeEnv()                                    # свежая среда, без FakeSiberi вообще
    res = run_recipe(rec, rec.goal, env, log=lambda m: None)
    assert res['status'] == 'done' and env.page == 'letter' and env.letter == 0


def test_replay_falls_back_when_target_missing():
    from aivision.replay import run_recipe
    from aivision.recipe import Recipe, Step
    rec = Recipe('x', 'g', steps=[Step(action='click', target_text='Почта', change='экран заметно изменился'),
                                  Step(action='click', target_text='НЕТ ТАКОЙ', change='экран заметно изменился')])
    env = FakeEnv()
    res = run_recipe(rec, rec.goal, env, log=lambda m: None)
    assert res['status'] == 'fallback' and res['from_step'] == 1


def test_adapt_substitutes_variable_parts():
    from aivision.replay import adapt_recipe
    from aivision.recipe import Recipe, Step
    rec = Recipe('x', 'напиши в Избранное: привет',
                 steps=[Step(action='click', target_text='Избранное'),
                        Step(action='type', target_text='поле ввода', text='привет', keys='enter')])
    answers = [{'steps': [{'i': 1, 'target_text': 'Иван'}, {'i': 2, 'text': 'как дела'}]}]
    with FakeSiberi(answers):
        steps = adapt_recipe(rec, 'напиши Ивану: как дела', log=lambda m: None)
    assert steps[0].target_text == 'Иван' and steps[1].text == 'как дела'
    assert steps[1].keys == 'enter'                    # неизменные части на месте
    # Исходный рецепт не испорчен
    assert rec.steps[0].target_text == 'Избранное'


def test_run_scenario_without_recipe_uses_analysis():
    from aivision.replay import run_scenario
    env = FakeEnv()
    with FakeSiberi([by_text('Почта', action='click'), {'action': 'done', 'answer': 'ok'}]):
        r = run_scenario('открой почту', env, None, True, refine=False, log=lambda m: None)
    assert r['via'] == 'analysis' and r['status'] == 'done' and r['fresh_recipe'] is True


def test_agent_manifest_parsing():
    from aivision.agents import parse_agent
    md = ('---\nname: Браузерный агент\ndescription: веб-задачи\n'
          'capabilities: [screen, browser]\ntools: [scenario]\napp: Google Chrome\n---\n'
          'Ты специалист по браузеру.')
    a = parse_agent(md)
    assert a.name == 'Браузерный агент' and a.app == 'Google Chrome'
    assert a.capabilities == ['screen', 'browser'] and a.tools == ['scenario']
    assert a.prompt == 'Ты специалист по браузеру.'
    # заголовка нет — весь текст идёт в роль, имя из source
    bare = parse_agent('просто роль без заголовка')
    assert bare.prompt == 'просто роль без заголовка'


def _demo_agents():
    from aivision.agents import parse_agent
    return [
        parse_agent('---\nname: Браузерный агент\ndescription: веб\ncapabilities: [screen, browser]\n---\nроль'),
        parse_agent('---\nname: Агент-слух\ndescription: звук\ncapabilities: [hearing]\n---\nроль'),
    ]


def test_orchestrator_route_picks_agent():
    from aivision import orchestrator as O
    ags = _demo_agents()
    ask = lambda s, u: '{"agent": "Браузерный агент", "why": "это веб"}'
    r = O.route('открой сайт и напиши сообщение', ags, ask=ask)
    assert r.agent is not None and r.agent.name == 'Браузерный агент'
    # несуществующий агент → None, но не падаем
    r2 = O.route('x', ags, ask=lambda s, u: '{"agent": "Нет такого", "why": "?"}')
    assert r2.agent is None


def test_knowledge_context_and_injection_into_plan():
    import tempfile
    from pathlib import Path
    from aivision import knowledge, orchestrator as O, paths as P
    from aivision.agents import parse_agent
    P.KNOWLEDGE = Path(tempfile.mkdtemp()) / 'knowledge'
    knowledge.remember('Бренд', 'Тон дружелюбный, аудитория — фрилансеры МАРКЕР-ЗНАНИЙ.')
    assert 'МАРКЕР-ЗНАНИЙ' in knowledge.context()
    captured = {}
    ask = lambda s, u: (captured.__setitem__('u', u), '{"steps":[{"agent":"A","task":"x","depends_on":[]}]}')[1]
    O.plan('сделай пост', [parse_agent('---\nname: A\n---\nроль')], ask=ask)
    assert 'МАРКЕР-ЗНАНИЙ' in captured['u']            # знания подмешаны в промт плана


def test_strategize_records_decision():
    import tempfile
    from pathlib import Path
    from aivision import knowledge, orchestrator as O, paths as P
    P.KNOWLEDGE = Path(tempfile.mkdtemp()) / 'knowledge'
    note = O.strategize('запусти контент-воронку', ask=lambda s, u: 'Подход: короткие ролики, тон бодрый.')
    assert note == 'короткие ролики, тон бодрый.'                       # в журнале не «подход: Подход:»
    assert O.strategize('x', ask=lambda s, u: '**ПОДХОД:** отчёт', record=False) == 'отчёт'
    decisions = (P.KNOWLEDGE / 'decisions.md').read_text(encoding='utf-8')
    assert 'короткие ролики' in decisions               # решение записано в журнал


def test_tool_step_picks_hearing_adapter(tmp_path=None):
    import tempfile
    from pathlib import Path
    from aivision import hearing, orchestrator as O
    from aivision.agents import parse_agent
    # настоящий аудиофайл не нужен — подменяем распознавание
    wav = Path(tempfile.mkdtemp()) / 'a.wav'
    wav.write_bytes(b'RIFF....')
    hearing.transcribe = lambda p, **k: 'привет мир'
    listener = parse_agent('---\nname: Слух\ntools: [groq]\n---\nроль')
    res = O.tool_step(listener, f'распознай {wav}', '', log=lambda m: None)
    assert res['status'] == 'done' and res['answer'] == 'привет мир'
    # нет аудио в задаче → понятный fail
    assert O.tool_step(listener, 'просто текст', '', log=lambda m: None)['status'] == 'fail'
    # scenario-агент без env → fail, не падение
    sc = parse_agent('---\nname: Браузер\ntools: [scenario]\n---\nроль')
    assert O.tool_step(sc, 'открой сайт', '', env=None, log=lambda m: None)['status'] == 'fail'
    # video подключён, но фабрика недоступна → понятный fail (не «не подключён»)
    from aivision import video
    video.enabled = lambda: False
    vid = parse_agent('---\nname: Видеограф\ntools: [video]\n---\nроль')
    assert 'dumaem' in O.tool_step(vid, 'сделай ролик', '', log=lambda m: None)['answer']
    # действительно не подключённый инструмент → «не подключён»
    none = parse_agent('---\nname: Икс\ntools: [quantum]\n---\nроль')
    assert 'не подключён' in O.tool_step(none, 'x', '', log=lambda m: None)['answer']


def test_video_step_assembles_from_material():
    import tempfile
    from pathlib import Path
    from aivision import orchestrator as O, video
    from aivision.agents import parse_agent
    out = Path(tempfile.mkdtemp()) / 'edit-1'
    captured = {}

    def fake_edit(material=None, duration=None, **k):
        captured['duration'] = duration
        out.mkdir(parents=True, exist_ok=True)
        return out
    video.enabled = lambda: True
    video.edit = fake_edit
    vid = parse_agent('---\nname: Монтажёр\ntools: [video]\n---\nроль')
    res = O.tool_step(vid, 'смонтируй ролик 45 секунд про запуск', '', log=lambda m: None)
    assert res['status'] == 'done' and 'смонтирован' in res['answer']
    assert captured['duration'] == 45                    # длину выдернули из задачи


def test_pulse_enqueues_task_on_gap_and_drains_inbox():
    import tempfile
    from pathlib import Path
    from aivision import paths as P, pulse, workspace as W
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    # просадка есть → задача во входящих
    ask = lambda s, u: '{"need": true, "area": "Контент", "task": "выпусти пост", "why": "давно нет постов"}'
    iid = pulse.tick(ask=ask, log=lambda m: None)
    assert iid is not None and len(pulse.pending()) == 1
    # всё в норме → ничего не ставим
    assert pulse.tick(ask=lambda s, u: '{"need": false}', log=lambda m: None) is None
    assert len(pulse.pending()) == 1
    # разбор входящих: задача уходит в работу, файл — в done
    handled = []
    out = pulse.drain_inbox(lambda task: (handled.append(task), {'status': 'done'})[1], log=lambda m: None)
    assert handled == ['выпусти пост'] and out[0]['status'] == 'done'
    assert len(pulse.pending()) == 0 and (P.STATE / 'inbox-done').exists()


def test_text_tool_writes_with_agent_role_as_system():
    from aivision import orchestrator as O, writing
    from aivision.agents import parse_agent
    captured = {}

    def fake_write(instruction, material='', system='', **k):
        captured['system'] = system
        captured['material'] = material
        return 'готовый текст'
    saved, writing.write = writing.write, fake_write
    try:
        cw = parse_agent('---\nname: Копирайтер\ntools: [text]\n---\nТы копирайтер РОЛЬ-МАРКЕР.')
        res = O.tool_step(cw, 'напиши пост', 'данные от прошлого шага', log=lambda m: None)
    finally:
        writing.write = saved
    assert res['status'] == 'done' and res['answer'] == 'готовый текст'
    assert 'РОЛЬ-МАРКЕР' in captured['system']            # роль агента — системный промт
    assert captured['material'] == 'данные от прошлого шага'


def test_image_tool_generates_and_reports_path():
    import base64
    import tempfile
    from pathlib import Path
    from aivision import imaging, orchestrator as O, paths as P
    from aivision.agents import parse_agent
    P.OUTBOX = Path(tempfile.mkdtemp()) / 'outbox'
    tiny_png = base64.b64encode(bytes.fromhex(
        '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154'
        '789c6360000002000154a24f3d0000000049454e44ae426082')).decode()
    imaging.enabled = lambda *a, **k: True
    imaging.generate = lambda prompt, out, **k: (Path(out).parent.mkdir(parents=True, exist_ok=True),
                                                 Path(out).write_bytes(base64.b64decode(tiny_png)),
                                                 Path(out))[-1]
    artist = parse_agent('---\nname: Художник\ntools: [image]\n---\nроль')
    res = O.tool_step(artist, 'шапка для видео про ИИ, неон, 3D', '', log=lambda m: None)
    assert res['status'] == 'done' and res['answer'].endswith('.png')
    assert Path(res['answer'].split(': ', 1)[1]).exists()
    # WebUI не поднят → понятный fail
    imaging.enabled = lambda *a, **k: False
    assert O.tool_step(artist, 'картинка', '', log=lambda m: None)['status'] == 'fail'


def test_plan_repairs_unknown_agent_and_gui_routing():
    from aivision import orchestrator as O
    from aivision.agents import parse_agent
    ags = [parse_agent('---\nname: Копирайтер\ntools: [text]\n---\nр'),
           parse_agent('---\nname: Публикатор\ncapabilities: [screen, web]\ntools: [scenario]\n---\nр')]
    # планировщик ошибся: несуществующий агент и загрузка поручена копирайтеру
    ask = lambda s, u: ('{"steps":[{"agent":"Кто-то","task":"напиши пост","depends_on":[]},'
                        '{"agent":"Копирайтер","task":"загрузи видео на YouTube","depends_on":[0]}]}')
    steps = O.plan('сделай и выложи', ags, ask=ask)
    assert steps[0].agent is not None and steps[0].agent.name == 'Копирайтер'   # текст → копирайтер
    assert steps[1].agent.name == 'Публикатор'                                   # загрузка → экранный


def test_plan_uses_registry_addresses_and_links_steps_that_need_results():
    import tempfile
    from aivision import orchestrator as O, paths as P, resources
    from aivision.agents import parse_agent
    saved = P.RESOURCES
    try:
        P.RESOURCES = Path(tempfile.mkdtemp()) / 'resources'
        resources.write('otkliks', '# Откликс\n- Сайт: https://otkliks.ru\n'
                        '- Яндекс Метрика: счётчик 113084036 — https://metrika.yandex.ru/stat/?id=113084036\n'
                        '- Google Analytics: https://analytics.google.com/', {'project': 'Откликс'})
        resources.write('siberi', '# Siberi\n- Яндекс Метрика: https://metrika.yandex.ru/stat/?id=1',
                        {'project': 'Siberi'})
        metrika = 'https://metrika.yandex.ru/stat/?id=113084036'
        assert resources.relevant('Открой Яндекс Метрику Откликса') == [f'Откликс · Яндекс Метрика: счётчик 113084036 — {metrika}']
        assert resources.relevant('Напиши пост про запуск') == []
        assert resources.fix_urls('Открой Метрику Откликса по ссылке https://yandex.ru.') == \
            f'Открой Метрику Откликса по ссылке {metrika}.'
        assert resources.fix_urls('Найди в https://google.com про Метрику Откликса') == \
            'Найди в https://google.com про Метрику Откликса'             # про Google речи нет
        ags = [parse_agent('---\nname: Аналитик\ncapabilities: [screen, web]\ntools: [scenario]\n---\nр'),
               parse_agent('---\nname: Копирайтер\ntools: [text]\n---\nр')]
        prompts = []
        ask = lambda s, u: prompts.append(u) or (
            '{"steps":[{"agent":"Аналитик","task":"Открой Яндекс Метрику проекта Откликс по ссылке '
            'https://yandex.ru. Выгрузи отчёты","depends_on":[]},'
            '{"agent":"Копирайтер","task":"Изучи полученную от Аналитика сводку и напиши отчёт","depends_on":[]},'
            '{"agent":"Копирайтер","task":"Напиши пост про запуск","depends_on":[]}]}')
        steps = O.plan('Открой Яндекс Метрику Откликса и напиши, что изменилось за неделю', ags, ask=ask)
        assert 'АДРЕСА ПО ЗАПРОСУ' in prompts[0] and metrika in prompts[0]
        assert metrika in steps[0].task and 'https://yandex.ru' not in steps[0].task
        assert steps[1].depends_on == [0]                                  # ждёт сводку Аналитика
        assert steps[2].depends_on == []                                   # независимый — параллельно
        from aivision import loop
        goals, run = [], loop.run_task
        loop.run_task = lambda goal, env, **kw: goals.append(goal) or {'status': 'done', 'answer': ''}
        try:
            O.scenario_step(ags[0], 'Открой Яндекс Метрику Откликса', '', env=object(), log=lambda m: None)
        finally:
            loop.run_task = run
        assert 'АДРЕСА ИЗ РЕЕСТРА' in goals[0] and metrika in goals[0]    # экранный агент знает адрес
        loop.run_task = lambda goal, env, **kw: goals.append(goal) or {'status': 'done', 'answer': ''}
        try:                                                               # «заново» по старой задаче
            O.scenario_step(ags[0], 'Открой Яндекс Метрику проекта Откликс по ссылке https://yandex.ru.',
                            '', env=object(), log=lambda m: None)
        finally:
            loop.run_task = run
        assert metrika in goals[1].split('\n\n')[0] and 'https://yandex.ru' not in goals[1]
    finally:
        P.RESOURCES = saved


def test_dashboard_numbers_are_glued_to_their_labels_but_table_columns_are_not():
    from aivision.detect import pair_values
    from aivision.prompt import describe_marks, describe_marks_text
    def t(text, x1, y1, x2, y2):
        return {'role': 'текст', 'text': text, 'box': [x1, y1, x2, y2]}
    els = [t('просмотры', 121, 268, 195, 281), t('О', 340, 265, 352, 277), t('визиты', 361, 268, 408, 278),
           t('посетители', 601, 268, 677, 278), t('время на сайте', 841, 267, 941, 281),
           t('53', 121, 292, 146, 309), t('А +51,43 %', 156, 301, 226, 311), t('20', 361, 292, 386, 309),
           t('А +66,67 %', 396, 301, 466, 312), t('11', 602, 292, 623, 309), t('2 м 49 с', 841, 292, 924, 309),
           t('А +1,46 %', 934, 301, 996, 312), t(', 46 %', 966, 301, 996, 312),          # обрывок поверх
           t('Просмотры', 1150, 805, 1226, 820), t('13', 1210, 838, 1226, 852), t('8', 1215, 870, 1226, 884)]
    out = pair_values(els)
    vals = {e['text']: e.get('value') for e in out}
    assert vals['просмотры'] == '53 А +51,43 %' and vals['визиты'] == '20 А +66,67 %'
    assert vals['посетители'] == '11' and vals['время на сайте'] == '2 м 49 с А +1,46 %'
    assert vals['Просмотры'] is None and {'13', '8'} <= set(vals)          # колонка таблицы — как была
    assert not {'53', '20', '11', ', 46 %'} & set(vals)                    # склеенные числа не дублируются
    assert [e['id'] for e in out] == list(range(1, len(out) + 1))
    assert '«визиты» = «20 А +66,67 %»' in describe_marks_text(out, (1920, 1080))
    assert '«визиты» = «20 А +66,67 %» x 361' in describe_marks(out)


class BrowserEnv(FakeEnv):
    """FakeEnv, где открыт Chrome: focus переключает на него, набранное с клавиатуры копится."""
    def __init__(self):
        super().__init__('home')
        self.focused, self.typed = [], ''

    def focus_window(self, name):
        self.focused.append(name)
        return 'Google Chrome «Новая вкладка» · монитор 1' if name == 'Google Chrome' else None

    def perform(self, events):
        self.typed += ''.join(e['key'] if len(e['key']) == 1 else f'<{e["key"][4:]}>'
                              for e in events if e['e'] == 'key' and e['down'])
        super().perform(events)


def test_loop_opens_the_address_from_the_task_itself_without_the_model():
    import tempfile
    from aivision import paths as P, resources, windows
    saved, lw = P.RESOURCES, windows.list_windows
    try:
        P.RESOURCES = Path(tempfile.mkdtemp()) / 'resources'
        resources.write('otkliks', '# Откликс\n- Яндекс Метрика: https://metrika.yandex.ru/stat/?id=113084036',
                        {'project': 'Откликс'})
        windows.list_windows = lambda include_own=False: [
            {'hwnd': 1, 'title': 'Telegram', 'process': 'Telegram.exe', 'monitor': 2, 'minimized': False, 'active': True},
            {'hwnd': 2, 'title': 'Новая вкладка', 'process': 'chrome.exe', 'monitor': 1, 'minimized': False, 'active': False}]
        env = BrowserEnv()
        with FakeSiberi([{'action': 'done', 'answer': 'открыто'}]) as fs:
            r = run_task('Открой Яндекс Метрику проекта Откликс по ссылке https://yandex.ru. Выгрузи отчёты',
                         env, refine=False, log=lambda m: None)
        assert env.focused == ['Google Chrome']
        assert env.typed == '<ctrl>lhttps://metrika.yandex.ru/stat/?id=113084036<enter>'
        assert 'открыт адрес из задачи' in r['history'][0] and 'открыт адрес из задачи' in fs.prompts[0]
        env2 = BrowserEnv()                                                # без «открой» и ссылки — не трогаем
        with FakeSiberi([{'action': 'done', 'answer': 'ок'}]):
            run_task('Напиши, что изменилось за неделю', env2, refine=False, log=lambda m: None)
        assert env2.focused == [] and env2.typed == ''
    finally:
        P.RESOURCES, windows.list_windows = saved, lw


def test_plan_memory_remembers_and_reuses_plan():
    import tempfile
    from pathlib import Path
    from aivision import orchestrator as O, paths as P, plan_memory, workspace as W
    from aivision.agents import parse_agent
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    ags = [parse_agent('---\nname: Копирайтер\ntools: [text]\n---\nр'),
           parse_agent('---\nname: Монтажёр\ntools: [video]\n---\nр')]
    plan_calls = {'n': 0}

    def ask(s, u):
        plan_calls['n'] += 1
        return '{"steps":[{"agent":"Копирайтер","task":"сценарий","depends_on":[]},{"agent":"Монтажёр","task":"монтаж","depends_on":[0]}]}'
    run = lambda t, c: {'status': 'done', 'answer': 'ok'}
    O.orchestrate('сделай ролик про запуск продукта', run, agents=ags, ask=ask, log=lambda m: None)
    assert plan_calls['n'] == 1                                 # первый раз — планировали через ask
    # очень похожий запрос → план берётся из памяти, ask не зовётся снова
    O.orchestrate('сделай ролик про запуск продукта', run, agents=ags, ask=ask, log=lambda m: None)
    assert plan_calls['n'] == 1                                 # второй раз — без планирования
    # непохожий запрос → снова планируем
    O.orchestrate('переведи деньги контрагенту срочно', run, agents=ags, ask=ask, log=lambda m: None,
                  confirm=lambda a, t: True)
    assert plan_calls['n'] == 2


def test_orchestrate_retries_then_escalates():
    import tempfile
    from pathlib import Path
    from aivision import approval, orchestrator as O, paths as P, pulse, workspace as W
    from aivision.agents import parse_agent
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    approval.APPROVALS = tmp / 'approvals'
    ags = [parse_agent('---\nname: Радар\ncapabilities: [web]\ndepartment: Продажи\nreports_to: Лид продаж\n---\nр'),
           parse_agent('---\nname: Лид продаж\ntools: [orchestrate]\ndepartment: Продажи\n---\nр')]
    ask = lambda s, u: '{"steps":[{"agent":"Радар","task":"найди заказы","depends_on":[]}]}'
    calls = {'n': 0}

    def always_fail(task, ctx):
        calls['n'] += 1
        return {'status': 'fail', 'answer': 'площадка недоступна'}
    out = O.orchestrate('найди заказы', always_fail, agents=ags, ask=ask, log=lambda m: None, retries=2)
    assert calls['n'] == 3                                      # первая + 2 повтора
    assert out['steps'][0]['status'] == 'escalated'
    inbox = [__import__('json').loads(p.read_text(encoding='utf-8')) for p in pulse.pending()]
    assert any('Эскалация' in i['task'] and 'Лид продаж' in i['task'] for i in inbox)  # ушло лиду


def test_orchestrate_holds_risky_step_without_confirmation():
    import tempfile
    from pathlib import Path
    from aivision import approval, orchestrator as O, paths as P, workspace as W
    from aivision.agents import parse_agent
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    approval.APPROVALS = tmp / 'approvals'
    ags = [parse_agent('---\nname: Браузер\ncapabilities: [web]\n---\nроль')]
    ask = lambda s, u: '{"steps":[{"agent":"Браузер","task":"оплати подписку картой","depends_on":[]}]}'
    ran = []
    out = O.orchestrate('оплати подписку', lambda t, c: (ran.append(1), {'status': 'done'})[1],
                        agents=ags, ask=ask, log=lambda m: None)
    assert out['status'] != 'done' and not ran                 # рискованный шаг НЕ исполнен
    assert len(approval.pending()) == 1                        # ждёт подтверждения
    assert (P.STATE / 'journal.md').exists()                   # записано в журнал
    # с подтверждением — исполняется
    out2 = O.orchestrate('оплати подписку', lambda t, c: (ran.append(1), {'status': 'done'})[1],
                         agents=ags, ask=ask, log=lambda m: None, confirm=lambda a, t: True)
    assert out2['status'] == 'done' and ran


def test_is_risky_detects_money_and_deletion():
    from aivision.approval import is_risky
    assert is_risky('оплати картой') and is_risky('удали папку') and is_risky('переведи деньги')
    assert not is_risky('напиши пост') and not is_risky('найди заказы')


def test_orchestrate_end_to_end_creates_tasks_and_reports():
    import tempfile
    from pathlib import Path
    from aivision import orchestrator as O, paths as P, workspace as W
    from aivision.agents import parse_agent
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    ags = [parse_agent('---\nname: Слух\ncapabilities: [hearing]\n---\nроль'),
           parse_agent('---\nname: Браузер\ncapabilities: [web]\n---\nроль')]
    ask = lambda s, u: ('{"steps":[{"agent":"Слух","task":"расшифруй","depends_on":[]},'
                        '{"agent":"Браузер","task":"отправь текст","depends_on":[0]}]}')
    seen = {}

    def run_step(task, context):
        seen[task.task] = context
        return {'status': 'done', 'answer': 'готово:' + task.agent.name}
    out = O.orchestrate('расшифруй и отправь', run_step, agents=ags, ask=ask, log=lambda m: None)
    assert out['status'] == 'done'
    assert 'Результат шага 0: готово:Слух' in seen['отправь текст']       # контекст передан
    # родительская задача + 2 подзадачи + 2 отчёта на диске
    assert W.read_task(out['parent'])['status'] == 'done'
    assert len(list(P.REPORTS.glob('*.md'))) == 2
    assert len([t for t in W.list_tasks() if t.get('parent') == out['parent']]) == 2


def test_dispatch_runs_independent_steps_in_parallel():
    import time
    from aivision import orchestrator as O
    from aivision.agents import parse_agent
    a = parse_agent('---\nname: A\n---\nр')
    steps = [O.Task(agent=a, agent_name='A', task=f's{i}', depends_on=[]) for i in range(3)]
    active = {'now': 0, 'max': 0}
    import threading
    lock = threading.Lock()

    def run_step(task, ctx):
        with lock:
            active['now'] += 1
            active['max'] = max(active['max'], active['now'])
        time.sleep(0.3)
        with lock:
            active['now'] -= 1
        return {'status': 'done', 'answer': task.task}
    t0 = time.time()
    out = O.dispatch(steps, run_step, log=lambda m: None, max_parallel=3)
    assert out['status'] == 'done' and active['max'] >= 2      # шли одновременно
    assert time.time() - t0 < 0.8                              # не последовательно (3×0.3=0.9)


def test_dispatch_respects_dependencies_order():
    from aivision import orchestrator as O
    from aivision.agents import parse_agent
    a = parse_agent('---\nname: A\n---\nр')
    order = []
    s0 = O.Task(agent=a, agent_name='A', task='first', depends_on=[])
    s1 = O.Task(agent=a, agent_name='A', task='second', depends_on=[0])
    seen = {}

    def run_step(task, ctx):
        order.append(task.task)
        seen[task.task] = ctx
        return {'status': 'done', 'answer': 'R:' + task.task}
    O.dispatch([s0, s1], run_step, log=lambda m: None, max_parallel=3)
    assert order == ['first', 'second']                        # зависимый только после предка
    assert 'R:first' in seen['second']                         # и получил его результат


def test_department_hierarchy_and_lead():
    from aivision.agents import parse_agent
    from aivision.team import build_departments
    lead = parse_agent('---\nname: Лид продаж\ntools: [orchestrate]\ndepartment: Продажи\n---\nроль')
    emp = parse_agent('---\nname: Радар\ndepartment: Продажи\nreports_to: Лид продаж\n---\nроль')
    other = parse_agent('---\nname: Браузер\ndepartment: Операции\n---\nроль')
    depts = {d.name: d for d in build_departments([lead, emp, other])}
    assert depts['Продажи'].lead.name == 'Лид продаж'
    assert [m.name for m in depts['Продажи'].members] == ['Радар']
    assert depts['Операции'].lead is None and lead.is_lead


def test_create_agent_from_prompt_roundtrip(tmp_path=None):
    import tempfile
    from pathlib import Path
    from aivision import orchestrator as O
    from aivision.agents import load_agents, write_agent
    spec = {'name': 'SMM-агент', 'description': 'ведёт соцсети', 'department': 'Контент',
            'capabilities': ['text', 'web'], 'tools': ['scenario'], 'prompt': 'Ты ведёшь соцсети.'}
    ask = lambda s, u: __import__('json').dumps(spec, ensure_ascii=False)
    proposed = O.propose_agent('нужен агент для соцсетей', ask=ask)
    assert proposed['name'] == 'SMM-агент' and 'text' in proposed['capabilities']
    folder = Path(tempfile.mkdtemp())
    path = write_agent(proposed, folder=folder)
    reloaded = load_agents(folder)
    assert len(reloaded) == 1 and reloaded[0].name == 'SMM-агент'
    assert reloaded[0].department == 'Контент' and reloaded[0].prompt == 'Ты ведёшь соцсети.'


def test_prompt_override_changes_effective_role():
    import tempfile
    from pathlib import Path
    from aivision import agents as A
    A.set_override_path(Path(tempfile.mkdtemp()) / 'overrides.json')
    ag = A.parse_agent('---\nname: Радар\n---\nБазовая роль.', Path('radar.md'))
    assert ag.effective_prompt() == 'Базовая роль.'
    A.set_override(ag.slug, 'Сегодня ищи только срочные заказы.')
    assert 'Базовая роль.' in ag.effective_prompt() and 'срочные' in ag.effective_prompt()
    A.set_override(ag.slug, 'Полностью новая роль.', replace=True)
    assert ag.effective_prompt() == 'Полностью новая роль.'
    A.clear_override(ag.slug)
    assert ag.effective_prompt() == 'Базовая роль.'


def test_workspace_task_and_report_flow():
    import tempfile
    from pathlib import Path
    from aivision import paths as P, workspace as W
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    tid = W.new_task('найти заказы', assignee='Радар', created_by='Лид')
    W.update_task(tid, status='done', answer='5 лидов')
    W.write_report(tid, 'Радар', 'нашёл 5')
    assert W.read_task(tid)['status'] == 'done'
    assert (P.REPORTS / f'{tid}.md').exists()
    assert [t['id'] for t in W.list_tasks(status='done')] == [tid]


def test_orchestrator_dispatch_passes_results_and_skips_on_failure():
    from aivision import orchestrator as O
    from aivision.agents import parse_agent
    a = parse_agent('---\nname: A\n---\nроль')
    b = parse_agent('---\nname: B\n---\nроль')
    s1 = O.Task(agent=a, agent_name='A', task='дай число', depends_on=[])
    s2 = O.Task(agent=b, agent_name='B', task='удвой', depends_on=[0])
    seen = {}

    def run_step(task, context):
        seen[task.task] = context
        if task.agent.name == 'A':
            return {'status': 'done', 'answer': '21'}
        return {'status': 'done', 'answer': '42'}
    out = O.dispatch([s1, s2], run_step, log=lambda m: None)
    assert out['status'] == 'done' and out['answer'] == '42'
    assert 'Результат шага 0: 21' in seen['удвой']        # результат первого дошёл во второй

    # первый шаг провалился → зависимый пропущен
    def fail_first(task, context):
        return {'status': 'fail' if task.agent.name == 'A' else 'done', 'answer': 'x'}
    out2 = O.dispatch([s1, s2], fail_first, log=lambda m: None)
    assert out2['results'][0]['status'] == 'fail' and out2['results'][1]['status'] == 'skipped'
    assert out2['status'] == 'partial'


def test_run_task_injects_agent_role():
    env = FakeEnv()
    captured = {}

    class Spy(FakeSiberi):
        def ask(self, system, prompt, image_b64=None, **_):
            captured.setdefault('system', system)
            return super().ask(system, prompt, image_b64, **_)
    with Spy([{'action': 'done', 'answer': 'ok'}]):
        run_task('цель', env, role='Ты специалист по браузеру РОЛЬ-МАРКЕР.',
                 refine=False, log=lambda m: None)
    assert 'РОЛЬ-МАРКЕР' in captured['system']            # роль агента попала в системный промт


def test_orchestrator_plan_with_dependencies():
    from aivision import orchestrator as O
    ags = _demo_agents()
    ask = lambda s, u: ('{"steps":[{"agent":"Агент-слух","task":"расшифруй","depends_on":[]},'
                        '{"agent":"Браузерный агент","task":"отправь текст","depends_on":[0]}]}')
    steps = O.plan('расшифруй голосовое и отправь текст', ags, ask=ask)
    assert len(steps) == 2
    assert steps[0].agent.name == 'Агент-слух' and steps[0].depends_on == []
    assert steps[1].agent.name == 'Браузерный агент' and steps[1].depends_on == [0]


def test_policy_daily_limits_allow_record_and_detect():
    import tempfile
    from pathlib import Path
    from aivision import paths as P, policy
    P.STATE = Path(tempfile.mkdtemp()) / 'state'
    # пустой журнал → публиковать можно, лимит 1
    assert policy.allow('publish') and policy.remaining('publish') == 1
    policy.record('publish')
    assert not policy.allow('publish') and policy.remaining('publish') == 0
    # неизвестное действие — без лимита
    assert policy.allow('что-то') and policy.remaining('что-то') > 100
    # классификация задачи по словам
    assert policy.action_of('выложи пост в телеге') == 'publish'
    assert policy.action_of('загрузи на youtube ролик') == 'publish'
    assert policy.action_of('смонтируй шортс из материала') == 'video'
    assert policy.action_of('напиши текст') == ''


def test_remote_detects_write_commands_and_resolves_host():
    import tempfile
    from pathlib import Path
    from aivision import paths as P, remote
    # изменяющие команды распознаются
    for c in ('rm -rf dist', 'git push origin main', 'systemctl restart nginx',
              'docker compose down', 'npm install', 'pm2 restart app'):
        assert remote.is_write(c), c
    # читающие — нет
    for c in ('ls -la', 'cat app.log', 'git status', 'df -h', 'tail -n 50 err.log'):
        assert not remote.is_write(c), c
    # хост из hosts.json: строка, объект с ключом/портом/repo, и алиас
    P.STATE = Path(tempfile.mkdtemp()) / 'state'
    remote.HOSTS = P.STATE / 'hosts.json'
    P.STATE.mkdir(parents=True, exist_ok=True)
    remote.HOSTS.write_text(json.dumps({
        'flat': 'deploy@1.2.3.4',
        'otkliks': {'ssh': 'root@1.2.3.4', 'port': 2222, 'key': 'C:\\k\\id',
                    'repo': '/var/www/hhpilot', 'pm2': ['otkliks-api']},
        'prod': 'otkliks',                               # алиас на otkliks
    }, ensure_ascii=False), encoding='utf-8')
    assert remote.resolve('flat') == 'deploy@1.2.3.4'
    assert remote.resolve('root@host') == 'root@host'    # готовый user@host — как есть
    assert remote.resolve('otkliks') == 'root@1.2.3.4'
    assert remote.repo('otkliks') == '/var/www/hhpilot' and remote.pm2('otkliks') == ['otkliks-api']
    assert remote.resolve('prod') == 'root@1.2.3.4'       # алиас резолвится
    e = remote.entry('otkliks')
    assert e['port'] == 2222 and e['key'] == 'C:\\k\\id'


def test_remote_run_builds_ssh_argv_with_key_and_port(monkeypatch=None):
    import tempfile
    from pathlib import Path
    from aivision import paths as P, remote
    P.STATE = Path(tempfile.mkdtemp()) / 'state'
    remote.HOSTS = P.STATE / 'hosts.json'
    P.STATE.mkdir(parents=True, exist_ok=True)
    remote.HOSTS.write_text(json.dumps({
        'srv': {'ssh': 'root@9.9.9.9', 'port': 2222, 'key': 'C:\\k\\orchestrator'}}), encoding='utf-8')
    seen = {}

    class R:
        returncode, stdout, stderr = 0, 'ok', ''
    saved = remote.shutil.which, remote.subprocess.run       # это общие shutil и subprocess — вернуть!
    remote.shutil.which = lambda x: 'ssh'
    remote.subprocess.run = lambda argv, **kw: (seen.update(argv=argv), R())[1]
    try:
        r = remote.run('srv', 'uptime')
    finally:
        remote.shutil.which, remote.subprocess.run = saved
    assert r['rc'] == 0 and r['out'] == 'ok'
    argv = seen['argv']
    assert argv[0] == 'ssh' and argv[-2:] == ['root@9.9.9.9', 'uptime']
    assert '-i' in argv and 'C:\\k\\orchestrator' == argv[argv.index('-i') + 1]
    assert '-p' in argv and argv[argv.index('-p') + 1] == '2222'
    assert 'BatchMode=yes' in argv


def test_ssh_tool_reads_execute_and_write_holds_for_approval():
    import tempfile
    from pathlib import Path
    from aivision import orchestrator as O, paths as P, remote
    from aivision.agents import parse_agent
    P.STATE = Path(tempfile.mkdtemp()) / 'state'
    P.APPROVALS = P.STATE / 'approvals'
    from aivision import approval
    approval.APPROVALS = P.APPROVALS
    remote.HOSTS = P.STATE / 'hosts.json'                # без реального конфига хостов
    remote.enabled = lambda h='': True
    ran = {}

    def fake_run(host, command, timeout=60.0):
        ran['cmd'] = command
        return {'rc': 0, 'out': 'app.log\nerr.log', 'err': ''}
    remote.run = fake_run
    devops = parse_agent('---\nname: DevOps\ntools: [ssh]\napp: otkliks\n---\nроль')
    # читающая команда в кавычках — выполняется
    res = O.tool_step(devops, 'покажи логи: `ls /var/log`', '', log=lambda m: None)
    assert res['status'] == 'done' and 'app.log' in res['answer'] and ran['cmd'] == 'ls /var/log'
    # изменяющая — удержана на подтверждении, не выполнена
    ran.clear()
    res = O.tool_step(devops, 'деплой: `git push origin main`', '', log=lambda m: None)
    assert res['status'] == 'held' and 'cmd' not in ran
    # нет команды в задаче → понятный fail
    assert O.tool_step(devops, 'просто текст', '', log=lambda m: None)['status'] == 'fail'


def test_projects_registry_defaults_and_add():
    import tempfile
    from pathlib import Path
    from aivision import paths as P, projects
    P.COMPANY = Path(tempfile.mkdtemp()) / 'company'
    projects.STORE = P.COMPANY / 'projects.json'
    names = [p.name for p in projects.projects()]
    assert 'antianti' in names and 'Откликс' in names and 'siberi.online' in names
    projects.add('Откликс', 'ssh', 'deploy@1.2.3.4', 'прод')
    got = {p.name: p for p in projects.projects()}
    assert got['Откликс'].where == 'deploy@1.2.3.4' and got['Откликс'].kind == 'ssh'
    assert 'Откликс' in projects.summary()


def test_resources_registry_and_context():
    import tempfile
    from pathlib import Path
    from aivision import paths as P, resources
    P.RESOURCES = Path(tempfile.mkdtemp()) / 'resources'
    resources.write('otkliks', '# Откликс\n- Telegram: @otkliks_channel',
                    {'project': 'Откликс', 'owners': ['SMM-агент'], 'approval': True})
    slugs = [s for s, _, _ in resources.resources()]
    assert slugs == ['otkliks']
    assert resources.meta('otkliks')['owners'] == ['SMM-агент']
    assert resources.meta('otkliks')['approval'] is True
    assert '@otkliks_channel' in resources.for_project('Откликс')
    ctx = resources.context()
    assert 'Откликс' in ctx and '@otkliks_channel' in ctx


def test_governance_access_and_signoff():
    import tempfile
    from pathlib import Path
    from aivision import governance as G, paths as P, resources
    from aivision.agents import parse_agent
    P.RESOURCES = Path(tempfile.mkdtemp()) / 'resources'
    resources.write('otkliks', '# Откликс', {'owners': ['SMM-агент'], 'approval': True})
    resources.write('аналитика', '# Панели', {'owners': ['Аналитик'], 'approval': False})
    # редактирование: владелец — да, чужой — нет, оркестратор — всегда
    assert G.can_edit('SMM-агент', 'otkliks') and not G.can_edit('Юрист', 'otkliks')
    assert G.can_edit(G.ORCHESTRATOR, 'otkliks')
    assert G.needs_approval('otkliks') and not G.needs_approval('аналитика')
    # серьёзные действия ловятся
    assert G.needs_signoff('создай бота для канала') and G.needs_signoff('выдай права администратора')
    assert G.needs_signoff('подключи аккаунт vk') and not G.needs_signoff('напиши пост')
    # разбор шага: лид действует сам, нижний агент на серьёзном — на визу
    lead = parse_agent('---\nname: Лид\ntools: [orchestrate]\n---\nроль')
    low = parse_agent('---\nname: Низ\ntools: [scenario]\n---\nроль')
    assert G.review(lead, 'создай бота') == 'allow'
    assert G.review(low, 'создай бота') == 'approve'
    assert G.review(low, 'сделай обложку') == 'allow'


def test_orchestrate_holds_signoff_step_until_visa():
    import tempfile
    from pathlib import Path
    from aivision import approval, orchestrator as O, paths as P, workspace as W
    from aivision.agents import parse_agent
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'INBOX', 'TASKS', 'REPORTS', 'OUTBOX', 'KNOWLEDGE', 'STATE'):
        setattr(P, name, tmp / name.lower())
    W.DIRS = (P.INBOX, P.TASKS, P.REPORTS, P.OUTBOX, P.KNOWLEDGE, P.STATE)
    approval.APPROVALS = tmp / 'approvals'
    P.RESOURCES = tmp / 'resources'
    ags = [parse_agent('---\nname: Браузер\ncapabilities: [web]\n---\nроль')]
    ask = lambda s, u: '{"steps":[{"agent":"Браузер","task":"создай бота и выдай права","depends_on":[]}]}'
    ran = []
    out = O.orchestrate('заведи бота', lambda t, c: (ran.append(1), {'status': 'done'})[1],
                        agents=ags, ask=ask, log=lambda m: None)
    assert out['status'] != 'done' and not ran                 # серьёзный шаг НЕ исполнен без визы
    assert len(approval.pending()) == 1
    # с визой — исполняется
    out2 = O.orchestrate('заведи бота', lambda t, c: (ran.append(1), {'status': 'done'})[1],
                         agents=ags, ask=ask, log=lambda m: None, confirm=lambda a, t: True)
    assert out2['status'] == 'done' and ran


def test_decide_parses_also_subactions():
    from aivision.agent import _sub_actions
    elements = [{'id': 5, 'role': 'кнопка', 'text': 'Тумблер A', 'box': [10, 10, 50, 30]},
                {'id': 6, 'role': 'кнопка', 'text': 'Тумблер B', 'box': [10, 40, 50, 60]}]
    raw = [{'action': 'click', 'mark': 6}, {'action': 'type', 'x': 100, 'y': 100, 'text': 'hi'},
           {'action': 'key', 'keys': 'enter'}, {'action': 'done'}]      # 'done' не из набора — отфильтруется
    subs = _sub_actions(raw, elements, (1920, 1080))
    assert len(subs) == 3
    assert subs[0]['action'] == 'click' and subs[0]['x'] == 30 and subs[0]['y'] == 50   # центр рамки mark 6
    assert subs[1]['action'] == 'type' and subs[1]['text'] == 'hi'
    assert subs[2]['action'] == 'key' and subs[2]['keys'] == 'enter'
    assert _sub_actions(None, elements, (1920, 1080)) is None


def test_loop_executes_also_chain_in_one_step():
    env = FakeEnv(page='settings')
    kx = 700 + (1200 - 700) * env.volume // 100          # бегунок при 80% → x=1100
    step1 = {'action': 'click', 'x': kx, 'y': 400,
             'also': [{'action': 'key', 'keys': 'right'}, {'action': 'key', 'keys': 'right'},
                      {'action': 'key', 'keys': 'right'}]}
    with FakeSiberi([step1, {'action': 'done', 'answer': 'готово'}]) as fs:
        r = run_task('подними громкость', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done'
    assert env.volume == 83           # 80 + 3 стрелки из also — за ОДИН шаг
    assert fs.calls == 2              # 4 действия на 2 обращения к Siberi (вот и экономия)


def test_checklist_for_config_task_only():
    from aivision import orchestrator as O
    ask = lambda s, u: '{"items":["Задать описание","Привязать обсуждение","Темы: Отзывы и Лента","Права админов"]}'
    items = O.checklist('Настрой Telegram-канал по уму', ask=ask)
    assert items == ['Задать описание', 'Привязать обсуждение', 'Темы: Отзывы и Лента', 'Права админов']
    called = []
    ask2 = lambda s, u: (called.append(1), '{"items":["x"]}')[1]
    assert O.checklist('отправь сообщение другу', ask=ask2) == [] and not called   # не-настройка → без мозга


def test_scenario_step_injects_checklist_into_goal():
    import tempfile
    from pathlib import Path
    from aivision import orchestrator as O, paths as P
    import aivision.loop as L
    from aivision.agents import parse_agent
    tmp = Path(tempfile.mkdtemp())
    for name in ('COMPANY', 'KNOWLEDGE', 'STATE', 'TASKS', 'REPORTS'):
        setattr(P, name, tmp / name.lower())
    O._default_ask = lambda s, u: '{"items":["Описание","Привязать обсуждение"]}'
    captured = {}
    L.run_task = lambda goal, env, **kw: (captured.update(goal=goal), {'status': 'done', 'answer': 'ok'})[1]
    ag = parse_agent('---\nname: Публикатор\ntools: [scenario]\n---\nроль')
    O.scenario_step(ag, 'Настрой канал по уму', '', env=object(), log=lambda m: None)
    assert 'ЧЕК-ЛИСТ' in captured['goal'] and 'Описание' in captured['goal'] and 'Привязать обсуждение' in captured['goal']


def test_type_clear_wipes_field_before_typing():
    from aivision.motion import build_events
    ev = build_events((0, 0), {'action': 'type', 'x': 900, 'y': 410, 'box': None, 'text': 'Имя', 'clear': True})
    downs = [e['key'] for e in ev if e['e'] == 'key' and e['down']]
    assert 'Key.ctrl' in downs and 'a' in downs and 'Key.delete' in downs   # ctrl+a, delete
    i_del = next(i for i, e in enumerate(ev) if e.get('key') == 'Key.delete')
    i_txt = next(i for i, e in enumerate(ev) if e.get('key') == 'И')
    assert i_del < i_txt                                                     # очистка ДО набора
    # без clear — поле не чистим
    ev2 = build_events((0, 0), {'action': 'type', 'x': 900, 'y': 410, 'box': None, 'text': 'Имя'})
    assert 'Key.delete' not in [e.get('key') for e in ev2]


def test_demo_build_recipe_from_human_events():
    import tempfile
    from pathlib import Path
    from aivision import demo, recipe as R

    class Img:
        size = (1000, 800)
    R.RECIPES = Path(tempfile.mkdtemp()) / 'recipes'
    els = [{'id': 1, 'role': 'поле ввода', 'text': 'Поиск', 'box': [100, 50, 300, 80]},
           {'id': 2, 'role': 'кнопка', 'text': 'Найти', 'box': [320, 50, 400, 80]}]
    detect = lambda im, off: els
    events = [
        {'kind': 'click', 't': 0.0, 'x': 200, 'y': 65},     # клик в поле «Поиск»
        {'kind': 'key', 't': 0.1, 'key': 'п'},
        {'kind': 'key', 't': 0.2, 'key': 'р'},
        {'kind': 'key', 't': 0.3, 'key': 'enter'},          # ввод «пр» + enter
        {'kind': 'click', 't': 0.9, 'x': 360, 'y': 65},     # клик «Найти»
    ]
    shots = {0: (Img(), (0, 0)), 4: (Img(), (0, 0))}
    r = demo.build_recipe(events, shots, 'demo1', 'поиск в сервисе', detect=detect)
    assert r is not None and len(r.steps) == 3
    assert r.steps[0].action == 'click' and r.steps[0].target_text == 'Поиск'
    t = r.steps[1]
    assert t.action == 'type' and t.text == 'пр' and t.keys == 'enter' and t.target_text == 'Поиск'
    assert r.steps[2].action == 'click' and r.steps[2].target_text == 'Найти'
    # клик без элемента → доля экрана (rel), чтобы replay нашёл место
    r2 = demo.build_recipe([{'kind': 'click', 't': 0, 'x': 500, 'y': 400}], {0: (Img(), (0, 0))},
                           'demo2', 'пусто', detect=lambda im, off: [])
    assert r2.steps[0].rel == [0.5, 0.5] and not r2.steps[0].target_text


def test_retina_convert_keeps_focused_window_and_taskbar_on_this_monitor():
    from aivision import retina
    def t(role, name, rect, **kw):
        return {'role': role, 'name': name, 'rect': rect, 'source': kw.get('source', 'uia'),
                'visible': kw.get('visible', True), 'fg': kw.get('fg', True),
                'process': kw.get('process', 'chrome.exe'), 'title': kw.get('title', 'Почта'),
                'value': kw.get('value', ''), 'state': kw.get('state', '')}
    items = [
        t('btn', 'Отправить', [-1800, 100, 80, 30]),                       # левый монитор, в фокусе
        t('btn', 'Пуск', [-1900, 1040, 40, 40], fg=False, process='explorer.exe', title=''),
        t('btn', 'Чужое окно', [-1500, 100, 80, 30], fg=False, title='Другое'),
        t('text', 'OCR-строка', [-1700, 300, 120, 20], source='ocr'),
        t('btn', 'Под окном', [-1600, 100, 80, 30], visible=False),
        t('btn', '', [-1650, 100, 30, 30]),                                # безымянная кнопка
        t('edit', '', [-1700, 200, 300, 30], value='привет'),              # поле без имени — нужно
        t('check', 'Запомнить', [-1700, 260, 20, 20], state='on'),
        t('btn', 'Справа', [100, 100, 80, 30]),                            # другой монитор
        t('item', 'Огромный', [-1920, 0, 1920, 700]),                      # больше четверти экрана
    ]
    out = retina.convert(items, (1920, 1080), (-1920, 0))
    names = [(e['role'], e['text']) for e in out]
    assert names == [('кнопка', 'Отправить'), ('кнопка', 'Пуск'), ('поле ввода', ''), ('флажок', 'Запомнить')]
    assert out[0]['box'] == [120, 100, 200, 130]
    assert out[2]['value'] == 'привет' and out[3]['state'] == 'on'


def test_find_elements_takes_retina_and_falls_back_to_own_uia():
    from aivision import detect, retina
    img = IMG
    saved = retina.elements, detect.uia_elements
    calls = []
    try:
        retina.elements = lambda size, offset: [{'text': 'Найти', 'role': 'кнопка', 'box': [350, 50, 380, 80]}]
        detect.uia_elements = lambda size, offset: calls.append('uia') or []
        els = detect.find_elements(img, (0, 0))
        assert retina.last_source == 'retina' and not calls
        assert any(e['text'] == 'Найти' and e['role'] == 'кнопка' for e in els)
        retina.elements = lambda size, offset: None                        # Retina не запущена
        detect.find_elements(img, (0, 0))
        assert retina.last_source == 'uia' and calls == ['uia']
    finally:
        retina.elements, detect.uia_elements = saved


def test_retina_rereads_a_new_browser_window_once():
    from aivision import retina
    seen = []
    def fake_get(path, params=None, timeout=retina.TIMEOUT):
        seen.append(dict(params or {}))
        return {'foreground': {'process': 'chrome.exe', 'title': 'Почта'},
                'elements': [{'role': 'btn', 'name': f'b{i}', 'rect': [10 + i, 10, 20, 20], 'source': 'uia',
                              'visible': True, 'fg': True, 'process': 'chrome.exe', 'title': 'Почта'}
                             for i in range(60)]}
    saved = retina._get, retina._last_fg
    retina._raw.clear()
    try:
        retina._get, retina._last_fg = fake_get, ('', '')
        assert len(retina.elements((1920, 1080), (0, 0))) == 60
        assert [p.get('refresh') for p in seen] == [None, 1]               # новое окно — перечитали
        seen.clear()
        retina._raw.clear()                                                # кэш шага не мешает проверке
        retina.elements((1920, 1080), (0, 0))
        assert [p.get('refresh') for p in seen] == [None]                  # то же окно, дерево полное
    finally:
        retina._get, retina._last_fg = saved


class ReflexEnv(FakeEnv):
    """FakeEnv + find по подписи — как RealEnv.find через Retina, но по OCR виртуального экрана."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.element_calls = 0

    def elements(self, img, offset):
        self.element_calls += 1
        return super().elements(img, offset)

    def find(self, text, role=''):
        from aivision.replay import find_target
        img, offset = self.grab()
        return find_target(text, role, FakeEnv.elements(self, img, offset))


def test_reflex_parses_commands_and_leaves_free_text_to_the_model():
    from aivision.reflex import parse
    ops = parse('Открой вкладку «Входящие», затем введи «привет, мир» в поле «Сообщение» и нажми Enter')
    assert [(o.kind, o.label, o.role, o.text, o.keys) for o in ops] == [
        ('click', 'Входящие', 'вкладка', '', ''),
        ('type', 'Сообщение', 'поле ввода', 'привет, мир', ''),
        ('key', '', '', '', 'enter')]
    assert [o.label for o in parse('Перейди в раздел «Настройки»; выбери пункт «Т. Иванов»')] == ['Настройки', 'Т. Иванов']
    assert parse('Нажми Ctrl+S')[0].keys == 'ctrl+s'
    assert parse('Дважды кликни по «Отчёт.docx»')[0].kind == 'double_click'
    assert parse('Напиши «hello». Потом нажми клавишу Esc.')[1].keys == 'esc'
    for free in ('зайди в почту и открой последнее письмо', 'нажми на пустое место',
                 'Нажми «Отправить» и «Сохранить»', 'перенеси «Подготовить отчёт» в Готово', ''):
        assert parse(free) is None, free


def test_reflex_runs_simple_commands_without_the_model():
    env = ReflexEnv()
    with FakeSiberi([{'action': 'done', 'answer': 'не должна звать'}]) as fs:
        r = run_task('Нажми «Почта», затем нажми «Анна Смирнова»', env, refine=False, log=lambda m: None)
    assert fs.calls == 0
    assert r['status'] == 'done' and 'без модели' in r['answer'] and r['steps'] == 2
    assert env.page == 'letter' and env.letter == 0 and env.element_calls == 0


def test_reflex_types_into_a_field_by_its_label():
    env = ReflexEnv()
    with FakeSiberi([{'action': 'done', 'answer': 'не должна звать'}]) as fs:
        r = run_task('Введи «погода москва» в поле «Найдётся всё» и нажми Enter', env, refine=False,
                     log=lambda m: None)
    assert fs.calls == 0 and r['status'] == 'done'
    assert env.page == 'results' and env.query == 'погода москва'


def test_reflex_hands_over_to_the_model_with_history():
    env = ReflexEnv()
    with FakeSiberi([{'action': 'done', 'answer': 'дальше сама'}]) as fs:
        r = run_task('Нажми «Почта», затем нажми «Кнопка которой нет»', env, refine=False, log=lambda m: None)
    assert fs.calls == 1 and r['status'] == 'done' and env.page == 'mail'
    assert 'рефлекс: клик «Почта»' in r['history'][0]
    assert 'рефлекс остановился' in r['history'][1] and 'Кнопка которой нет' in r['history'][1]


def test_replay_asks_the_live_index_before_scanning_the_screen():
    from aivision.recipe import Recipe, Step
    from aivision.replay import run_recipe
    env = ReflexEnv()
    rp = Recipe('t', 'открыть почту', [Step(action='click', target_text='Почта', target_role='ссылка')])
    res = run_recipe(rp, rp.goal, env, log=lambda m: None)
    assert res['status'] == 'done' and env.page == 'mail' and env.element_calls == 0
    rp2 = Recipe('t2', 'x', [Step(action='click', target_text='Нет такой', target_role='')])
    assert run_recipe(rp2, rp2.goal, ReflexEnv(), log=lambda m: None)['status'] == 'fallback'


def test_real_env_settles_on_retina_events_instead_of_polling():
    from aivision import retina, screen
    env = screen.RealEnv.__new__(screen.RealEnv)
    env._area = lambda: (0, 0, 1920, 1080)
    env.grab = lambda: ('img', (0, 0))
    calls = []

    def fake_settled(area, quiet_ms=300, timeout_ms=2500, expect=True):
        calls.append((expect, timeout_ms))
        return {'settled': not expect, 'changed': True}         # 1-й вызов: всё ещё меняется

    def no_sleep(s):
        raise AssertionError('при живой Retina опросом не ждём')
    saved = retina.settled, screen.time.sleep
    try:
        retina.settled, screen.time.sleep = fake_settled, no_sleep
        assert env.settle('before') == ('img', (0, 0))
        assert calls == [(True, 2500), (False, 5500)]            # дождались изменения, затем тишины
    finally:
        retina.settled, screen.time.sleep = saved


class FakeOllama:
    """Подмена Ollama: отвечает номером строки, где стоит нужная подпись (как ответила бы модель)."""
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, 0

    def __enter__(self):
        from aivision import local_brain
        self._saved = local_brain._chat, os.environ.get('AV_LOCAL_BRAIN')
        os.environ['AV_LOCAL_BRAIN'] = '1'
        local_brain._down_until = 0.0
        local_brain._chat = self.chat
        return self

    def __exit__(self, *_):
        from aivision import local_brain
        local_brain._chat = self._saved[0]
        os.environ['AV_LOCAL_BRAIN'] = self._saved[1] or '0'

    def chat(self, system, user, timeout=None, **kw):
        from aivision import local_brain
        intent = re.search(r'нажать «([^»]+)»', user).group(1)
        want = self.mapping.get(intent)
        if system == local_brain.VERIFY_SYSTEM:                             # «это то самое?»
            label = re.search(r'Элемент: .*«(.*)»', user).group(1)
            return {'message': {'content': json.dumps({'same': label == want})}}
        self.calls += 1
        idx = 0
        for line in user.splitlines():
            m = re.match(r'(\d+)\. .*«(.*)»$', line)
            if m and want is not None and m.group(2) == want:
                idx = int(m.group(1))
        reply = {'id': idx, 'label': want if idx else ''}
        return {'message': {'content': json.dumps(reply, ensure_ascii=False)}, 'prompt_eval_count': 100}


def test_local_brain_accepts_only_ids_from_the_list():
    from aivision import local_brain
    els = [{'text': 'Войти', 'role': 'кнопка', 'box': [0, 0, 10, 10]},
           {'text': 'Регистрация', 'role': 'ссылка', 'box': [20, 0, 40, 10]}]
    with FakeOllama({'Sign in': 'Войти'}) as fo:
        assert local_brain.choose('Sign in', els)['text'] == 'Войти'
        assert local_brain.choose('Pay', els) is None                       # модель сказала 0
        assert fo.calls == 2

        def reply(pick, same=True):
            def chat(system, user, timeout=None, **kw):
                if system == local_brain.VERIFY_SYSTEM:
                    return {'message': {'content': json.dumps({'same': same})}}
                return {'message': {'content': pick}}
            return chat
        for bad in ('{"id": 7}', '{"id": -1}', 'не json', '{"id": "x"}', '{"id": 1, "label": "Auto"}'):
            local_brain._chat = reply(bad)
            assert local_brain.choose('Sign in', els) is None, bad
        local_brain._chat = reply('{"id": 2, "label": "войти"}')            # номер сдвинут, подпись верна
        assert local_brain.choose('Sign in', els)['text'] == 'Войти'
        local_brain._chat = reply('{"id": 1}', same=False)                  # проверка не подтвердила
        assert local_brain.choose('Sign in', els) is None
    assert local_brain.choose('Sign in', els) is None                       # в тестах выключена


def test_find_target_does_not_match_empty_labels():
    from aivision.replay import find_target
    els = [{'text': '', 'role': 'поле ввода', 'box': [0, 0, 10, 10]},
           {'text': 'Отправить', 'role': 'кнопка', 'box': [20, 0, 40, 10]}]
    assert find_target('Отправить письмо', '', els)['text'] == 'Отправить'
    assert find_target('Удалить', '', els) is None


def test_reflex_uses_local_brain_for_synonyms_and_translations():
    env = ReflexEnv()
    with FakeSiberi([{'action': 'done', 'answer': 'не должна звать'}]) as fs, \
            FakeOllama({'Mail': 'Почта'}) as fo:
        r = run_task('Нажми «Mail»', env, refine=False, log=lambda m: None)
    assert fs.calls == 0 and fo.calls == 1
    assert r['status'] == 'done' and env.page == 'mail'


def test_lookup_leaves_equal_targets_to_the_big_model():
    from aivision.replay import _lookup
    class AmbiguousEnv(ReflexEnv):
        def find(self, text, role=''):
            self.last_find = 'ambiguous'                                    # Retina: три «Удалить»
            return None
    env = AmbiguousEnv()
    img, offset = env.grab()
    with FakeOllama({'Почта': 'Почта'}) as fo:
        assert _lookup(env, img, offset)('Почта', '') is None
    assert fo.calls == 0 and env.element_calls == 0


def _icon_strip():
    """Полоса с тремя иконками без подписи: шестерёнка, крестик, лупа (+ пустое место)."""
    import math as _m
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (420, 100), '#f4f4f6')
    d = ImageDraw.Draw(img)
    cx, cy = 66, 46                                                  # шестерёнка
    d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], outline='#333', width=4)
    for k in range(8):
        a = k * _m.pi / 4
        d.line([cx + 9 * _m.cos(a), cy + 9 * _m.sin(a), cx + 14 * _m.cos(a), cy + 14 * _m.sin(a)], fill='#333', width=4)
    d.line([152, 32, 180, 60], fill='#333', width=4)                 # крестик
    d.line([180, 32, 152, 60], fill='#333', width=4)
    d.ellipse([244, 32, 264, 52], outline='#333', width=4)           # лупа
    d.line([261, 49, 274, 62], fill='#333', width=5)
    boxes = {'gear': [48, 28, 84, 64], 'close': [148, 28, 184, 64], 'search': [240, 28, 278, 66],
             'blank': [330, 28, 366, 64]}
    return img, boxes


class IconEnv:
    """Среда с безымянными иконками: наведение показывает подсказку (как RealEnv.probe)."""
    def __init__(self, img, boxes, tips):
        self.img, self.boxes, self.tips, self.probes = img, boxes, tips, []

    def unnamed(self, img, offset):
        return [{'role': 'кнопка', 'box': b} for b in self.boxes.values()]

    def app(self):
        return 'fake.exe'

    def probe(self, box):
        name = next(k for k, b in self.boxes.items() if b == box)
        self.probes.append(name)
        return self.tips.get(name, '')


def test_icon_book_recognises_learned_icons_and_rejects_others(tmp_dir=None):
    import tempfile
    from aivision import icons
    img, boxes = _icon_strip()
    crop = lambda k: img.crop(tuple(boxes[k]))
    with tempfile.TemporaryDirectory() as root:
        b = icons.IconBook(Path(root))
        assert b.learn(crop('close'), 'Закрыть', 'fake.exe')
        assert b.name(crop('close'), 'fake.exe')[0] == 'Закрыть'
        assert b.name(crop('gear'), 'fake.exe')[0] == '' and b.name(crop('search'))[0] == ''
        assert not b.learn(crop('blank'), 'Пусто')                     # однотонное — не иконка
        b.learn(crop('close'), 'Закрыть окно', 'fake.exe')             # та же иконка — обновили, не дубль
        assert len(b.entries) == 1 and b.entries[0]['label'] == 'Закрыть окно'
        again = icons.IconBook(Path(root))                              # словарь переживает перезапуск
        assert again.name(crop('close'), 'fake.exe')[0] == 'Закрыть окно'


def test_icon_resolve_probes_tooltips_once_then_uses_the_dictionary():
    import tempfile
    from aivision import icons
    img, boxes = _icon_strip()
    with tempfile.TemporaryDirectory() as root:
        icons._books.pop(root, None)
        env = IconEnv(img, boxes, {'gear': 'Настройки', 'close': 'Закрыть', 'search': 'Поиск'})
        el = icons.resolve('Поиск', 'кнопка', env, img, (0, 0), root=Path(root))
        assert el['box'] == boxes['search'] and el['text'] == 'Поиск'
        assert env.probes == ['gear', 'close', 'search']                # навёл по очереди, запомнил все
        env.probes.clear()
        assert icons.resolve('Настройки', '', env, img, (0, 0), root=Path(root))['box'] == boxes['gear']
        assert icons.resolve('закрыть', '', env, img, (0, 0), root=Path(root))['box'] == boxes['close']
        assert env.probes == []                                         # второй раз — без наведения
        assert icons.resolve('Печать', '', env, img, (0, 0), root=Path(root)) is None
        assert env.probes == ['blank']                                  # знакомые не наводит повторно
        els = icons.annotate([{'text': 'Файл', 'role': 'пункт меню', 'box': [0, 0, 40, 20]}],
                             env.unnamed(img, (0, 0)), img, 'fake.exe', root=Path(root))
        assert {(e['text'], e.get('icon', False)) for e in els} == {
            ('Файл', False), ('Настройки', True), ('Закрыть', True), ('Поиск', True)}
        icons._books.pop(root, None)


class TextEnv(FakeEnv):
    """FakeEnv, у которой есть журнал Retina: подписи — как из UIA, изменения — текстом."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.seq = 0

    def elements(self, img, offset):
        return [dict(e, role='ссылка') for e in super().elements(img, offset)]

    def mark(self):
        return self.seq

    def perform(self, events):
        super().perform(events)
        self.seq += 1

    def changes(self, since):
        return [f'window: «{self.title()}»'] if self.seq > since else []


def test_text_marks_carry_zone_value_and_state():
    from aivision.prompt import describe_marks_text
    els = [{'id': 1, 'text': 'Входящие', 'role': 'вкладка', 'box': [10, 10, 90, 40]},
           {'id': 2, 'text': 'Тема', 'role': 'поле ввода', 'value': 'Привет', 'box': [900, 500, 1100, 530]},
           {'id': 3, 'text': 'Запомнить', 'role': 'флажок', 'state': 'on', 'box': [1700, 1000, 1720, 1020]}]
    assert describe_marks_text(els, (1920, 1080)).splitlines() == [
        '[1] вкладка «Входящие» · ВЛ', '[2] поле ввода «Тема» = «Привет» · СЦ', '[3] флажок «Запомнить» [вкл] · НП']


def test_decide_text_needs_a_mark_and_never_takes_blind_coordinates():
    from aivision.agent import decide_text
    els = [{'id': 1, 'text': 'Отправить', 'role': 'кнопка', 'box': [100, 100, 180, 130]}]
    def run(answer):
        with FakeSiberi([answer]):
            return decide_text('sys', 'user', IMG, els, 'цель', log=lambda m: None)
    r = run({'action': 'click', 'mark': 1, 'x': 5, 'y': 5})
    assert r['action'] == 'click' and (r['x'], r['y']) == (140, 115) and r['source'] == 'mark'
    assert run({'action': 'look', 'why': 'иконка без подписи'}) is None
    assert run({'action': 'click', 'x': 400, 'y': 300}) is None          # координаты вслепую — нет
    assert run({'action': 'click', 'mark': 99}) is None                  # метки нет в списке
    assert run({'action': 'stroke', 'points': [[1, 1], [9, 9]]}) is None
    assert run({'action': 'done', 'answer': 'готово'})['action'] == 'done'
    r = run({'action': 'click', 'mark': 1, 'also': [{'action': 'click', 'x': 5, 'y': 5},
                                                    {'action': 'key', 'keys': 'enter'}]})
    assert [a['action'] for a in r['also']] == ['key']


def test_loop_solves_text_screens_without_screenshots_and_records_changes():
    env = TextEnv()
    answers = [by_text('Почта', action='click'), by_text('Анна Смирнова', action='click'),
               {'action': 'done', 'answer': 'От Анны Смирновой'}]
    with FakeSiberi(answers) as fs:
        r = run_task('открой последнее письмо', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and env.page == 'letter'
    assert fs.images == [False, False, False]                            # ни одной картинки
    assert 'на экране: window: «Входящие — Яндекс Почта»' in r['history'][0]


def test_loop_takes_a_screenshot_when_the_model_asks_to_look():
    env = TextEnv()
    answers = [{'action': 'look', 'why': 'нужна иконка'}, by_text('Почта', action='click'),
               by_text('Анна Смирнова', action='click'), {'action': 'done', 'answer': 'ок'}]
    with FakeSiberi(answers) as fs:
        r = run_task('открой последнее письмо', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and env.page == 'letter'
    assert fs.images == [False, True, True, True]                        # look → тот же шаг со скриншотом


def test_retina_changes_keep_the_reaction_and_drop_noise():
    from aivision import retina
    saved = retina._get
    try:
        retina._get = lambda path, params=None, timeout=retina.TIMEOUT, **kw: {'last': 9, 'events': [
            {'seq': 5, 'kind': 'window', 'text': 'window: chrome.exe «Почта»\nbtn «Входящие»\nbtn «Спам»'},
            {'seq': 6, 'kind': 'ui', 'text': '+ btn «Отправить»\n@explorer.exe ~ btn «Часы 5:02» → «Часы 5:03»'},
            {'seq': 7, 'kind': 'motion', 'text': '▶ motion 0,0 640x360'},
            {'seq': 8, 'kind': 'text', 'text': '+ «Сообщение отправлено»'}]}
        assert retina.changes(4) == ['window: chrome.exe «Почта»', '+ btn «Отправить»', '+ «Сообщение отправлено»']
        assert retina.mark() == 9
        retina._get = lambda path, params=None, timeout=retina.TIMEOUT, **kw: {'events': ['[12:00] old']}
        assert retina.mark() is None and retina.changes(1) == []          # старая Retina без журнала
    finally:
        retina._get = saved


class _Services:
    """Изоляция aivision.services: подмена живости Retina, запуска процесса и регистрации окон."""
    def __init__(self, alive=False, child_exits=False, env=None):
        self.alive, self.child_exits, self.env = alive, child_exits, env or {}
        self.spawned, self.registered, self.killed = 0, 0, 0

    def __enter__(self):
        from aivision import retina, services
        self.s, self.r = services, retina
        self.saved = (services._alive, services._spawn, retina.register_own, services._child,
                      services._failed_at, dict(services._status), dict(os.environ), services._idle_thread)
        services._idle_thread = object()                # фоновый сторож простоя в тестах не нужен
        os.environ.update({'AV_RETINA': '1', **self.env})
        if 'AV_RETINA_URL' not in self.env:
            os.environ.pop('AV_RETINA_URL', None)
        services._child, services._failed_at = None, 0.0
        services._alive = lambda url: self.alive
        services._spawn = self.spawn
        retina.register_own = lambda: setattr(self, 'registered', self.registered + 1) or True
        return self

    def __exit__(self, *_):
        s = self.s
        s._alive, s._spawn, self.r.register_own, s._child, s._failed_at, st, env, s._idle_thread = self.saved
        s._status.clear()
        s._status.update(st)
        os.environ.clear()
        os.environ.update(env)

    def spawn(self):
        self.spawned += 1
        exits = self.child_exits
        owner = self

        class Child:
            def poll(self):
                return 1 if exits or owner.killed else None

            def terminate(self):
                owner.killed += 1

            def wait(self, timeout=None):
                return 0
        self.killed = 0
        return Child()


def test_vision_sleeps_until_a_task_needs_the_screen_and_after_idle():
    import time as _t
    from aivision import retina
    with _Services(alive=False) as t:
        s = t.s
        saved = s.start_ollama, s._bg_started
        try:
            s.start_ollama, s._bg_started = (lambda: None), False
            s._status['retina'] = 'idle'
            s.start_background()                                         # старт лаунчера — зрение не трогаем
            _t.sleep(0.3)
            assert t.spawned == 0 and s.status()['retina'] == 'idle'
            assert 'спит' in s.describe()
        finally:
            s.start_ollama, s._bg_started = saved
        assert s.wake(wait=0) == 'starting' and t.spawned == 1            # агенту нужен экран — проснулась
        t.alive = True
        assert s.wake(wait=1) == 'ok' and s.wake() == 'ok' and t.spawned == 1
        s.touch()
        s._idle_check()
        assert t.killed == 0                                             # только что работали — не гасим
        s._last_use = _t.time() - s.IDLE_STOP - 1
        s._idle_check()
        assert t.killed == 1 and s.status()['retina'] == 'idle' and s._child is None   # без дела — уснула
        t.alive = False
        assert s.wake(wait=0) == 'starting' and t.spawned == 2            # снова понадобилась
    calls, saved_get = [], retina._get
    try:                                                                 # вкладка «Зрение» не будит и не держит
        retina._get = lambda path, params=None, timeout=retina.TIMEOUT, **kw: calls.append(kw) or None
        retina.feed(0), retina.recent(), retina.mark()
        assert calls == [{'use': False}, {'use': False}, {}]
    finally:
        retina._get = saved_get


def test_siberi_local_serves_the_siberi_api_on_this_computer():
    """Siberi Local — настоящий Node-сервер, мозг подменён эхом (без Edge и Google)."""
    import shutil
    import urllib.request
    from aivision import services, siberi
    if not shutil.which('node') or not (services.SIBERI_DIR / 'node_modules' / 'playwright-core').exists():
        print('  (нет Node.js или node_modules — пропуск)')
        return
    port = 18770
    import tempfile
    saved = (services.SIBERI_PORT, siberi.BASE_URL, dict(os.environ), services._siberi_child, dict(services._status),
             services.SIBERI_LOG, services.SIBERI_PROFILE, services.SIBERI_CDP_PORT)
    try:
        services.SIBERI_PORT, services.SIBERI_CDP_PORT = port, port + 1     # не мешаем настоящему Siberi Local
        tmp = Path(tempfile.mkdtemp())
        services.SIBERI_LOG, services.SIBERI_PROFILE = tmp / 'siberi-local.log', tmp / 'edge'   # не трогаем настоящие
        siberi.BASE_URL = f'http://127.0.0.1:{port}/api/v1/ai'
        os.environ.update(SIBERI_LOCAL_ENGINE='echo', SIBERI_API_KEY='test-key')
        services._siberi_child = None
        assert services.siberi_local()
        st = services.start_siberi(wait=30)                                 # поднялся сам
        assert st == 'ok', (st, services.SIBERI_LOG, services.SIBERI_LOG.exists() and services.SIBERI_LOG.read_text(encoding='utf-8')[-500:])
        assert 'Siberi Local работает' in services.describe()
        try:                                                                # без ключа — нельзя
            urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/ai/ping', timeout=3)
            assert False, 'без ключа пустил'
        except urllib.error.HTTPError as e:
            assert e.code == 401
        img = siberi.image_to_data_url(__import__('PIL.Image', fromlist=['x']).new('RGB', (40, 30), 'white'))
        assert siberi.ask('система', 'привет') == 'echo[0]: система | привет'
        assert siberi.ask('', 'что на картинках?', images=[img, img]) == 'echo[2]: что на картинках?'
        try:                                                                # картинка без входа в Google
            siberi.ask('', 'REFUSE это', images=[img])
            assert False, 'отказ Google не распознан'
        except RuntimeError as e:
            assert 'Войти в Google' in str(e)
        assert siberi.ask('', 'REFUSE без картинки').startswith('Не удается')   # текст — как ответил Google
        services.stop_siberi()
        assert services._siberi_ping() is None and services.status()['siberi'] == 'off'
        assert siberi.ask('', 'снова') == 'echo[0]: снова'                   # нужен — поднимается сам
    finally:
        services.stop_siberi()
        (services.SIBERI_PORT, siberi.BASE_URL, env, services._siberi_child, st, services.SIBERI_LOG,
         services.SIBERI_PROFILE, services.SIBERI_CDP_PORT) = saved
        os.environ.clear()
        os.environ.update(env)
        services._status.clear()
        services._status.update(st)


def test_services_start_embedded_retina_only_when_needed():
    with _Services(alive=True) as t:                                     # уже отвечает — не запускаем
        assert t.s.start_retina() == 'ok' and t.spawned == 0 and t.registered == 1
    with _Services(alive=False) as t:                                    # нет — запускаем один раз
        assert t.s.start_retina() == 'starting' and t.s.start_retina() == 'starting'
        assert t.spawned == 1
        t.alive = True
        assert t.s.start_retina(wait=1) == 'ok' and t.registered == 1
    with _Services(alive=False, child_exits=True) as t:                  # упала на старте — пауза
        assert t.s.start_retina(wait=1) == 'down' and t.s.start_retina(wait=1) == 'down'
        assert t.spawned == 1
    with _Services(env={'AV_RETINA': '0'}) as t:
        assert t.s.start_retina() == 'off' and t.spawned == 0
    with _Services(env={'AV_RETINA_URL': 'http://127.0.0.1:8765'}) as t:  # внешнюю — не запускаем
        assert t.s.start_retina() == 'down' and t.spawned == 0
        assert t.r._base() == 'http://127.0.0.1:8765'
        t.alive = True
        assert t.s.start_retina() == 'external'
    with _Services() as t:
        assert t.r._base() == 'http://127.0.0.1:8766'                    # по умолчанию — своя, 8766


def test_services_run_the_vendored_copy_from_python_or_from_the_exe():
    from aivision import services
    import aivision.vendor.retina.__main__ as vendored
    assert callable(vendored.main)                                       # копия импортируется из AiVision
    saved = sys.executable, getattr(sys, 'frozen', None)
    try:
        sys.executable = r'C:\Py\pythonw.exe'
        cmd = services.retina_cmd()
        assert cmd[1:4] == ['-m', 'aivision.vendor.retina', 'watch'] and '8766' in cmd
        sys.frozen, sys.executable = True, r'C:\Apps\AiVision\AiVision.exe'
        assert services.retina_cmd()[:3] == [r'C:\Apps\AiVision\AiVision.exe', '--retina', 'watch']
    finally:
        sys.executable = saved[0]
        if saved[1] is None:
            del sys.frozen
        else:
            sys.frozen = saved[1]


def test_services_ollama_status_and_description():
    from aivision import local_brain, services
    saved = services._models, services._ollama_exe, local_brain.warmup, dict(services._status), \
        os.environ.get('AV_LOCAL_BRAIN')
    try:
        os.environ['AV_LOCAL_BRAIN'] = '1'
        local_brain.warmup = lambda: None
        services._models = lambda base: None                             # Ollama не отвечает
        services._ollama_exe = lambda: None                              # и не установлена
        assert services.start_ollama(wait=0) == 'missing'
        services._models = lambda base: ['qwen2.5:7b', 'llama3:8b']
        assert services.start_ollama() == 'ok' and services.status()['model'] == 'ready'
        services._models = lambda base: ['llama3:8b']
        services.start_ollama()
        assert 'ollama pull qwen2.5:7b' in services.describe()
        os.environ['AV_LOCAL_BRAIN'] = '0'
        assert services.start_ollama() == 'off'
    finally:
        services._models, services._ollama_exe, local_brain.warmup, st, flag = saved
        services._status.clear()
        services._status.update(st)
        os.environ['AV_LOCAL_BRAIN'] = flag or '0'


def test_scenario_step_lets_reflex_take_the_bare_command():
    from aivision import loop, orchestrator as O
    from aivision.agents import Agent
    seen = []
    saved = O.checklist, loop.run_task
    try:
        loop.run_task = lambda goal, env, **kw: seen.append((goal, kw.get('command'))) or {'status': 'done'}
        agent = Agent(name='Экранщик', prompt='экранный агент', tools=['scenario'])
        O.checklist = lambda task: []
        O.scenario_step(agent, 'Нажми «Опубликовать»', 'пост готов: /tmp/post.txt', env=object(), log=lambda m: None)
        O.checklist = lambda task: ['Включить уведомления', 'Сохранить']
        O.scenario_step(agent, 'настрой канал по уму', '', env=object(), log=lambda m: None)
    finally:
        O.checklist, loop.run_task = saved
    assert seen[0][1] == 'Нажми «Опубликовать»' and 'КОНТЕКСТ' in seen[0][0]   # контекст — модели, команда — рефлексу
    assert seen[1][1] == ''                                                     # чек-лист — только через модель

def test_screen_secrets_are_masked_before_they_leave():
    from aivision import retina
    from aivision.prompt import describe_marks, describe_marks_text
    from aivision.redact import redact
    assert redact('SIBERI API kEY=sk-sibepi-0a1S2b3c4d5e6f7a8b9c0d1e2f3S4a5b6c7d8e9f0a1b2c3d').endswith('=••••')
    assert redact('GROQ_API_KEY=gsk_abcdefghijklmnopqrstuv1234') == 'GROQ_API_KEY=••••'
    assert redact('пароль = qwerty123') == 'пароль = ••••'
    for safe in ('btn «Отправить письмо»', 'https://otkliks.ru/app/settings', 'commit 9deea0c'):
        assert redact(safe) == safe
    els = [{'id': 1, 'text': 'token: abc123def456', 'role': 'текст', 'box': [0, 0, 9, 9], 'value': 'sk-live_abcdefghijklmnop'}]
    assert 'abc123' not in describe_marks(els) and 'abc123' not in describe_marks_text(els, (100, 100))
    assert 'abcdefghijklmnop' not in describe_marks_text(els, (100, 100))
    saved = retina._get
    try:
        retina._get = lambda path, params=None, timeout=retina.TIMEOUT, **kw: {'last': 2, 'events': [
            {'seq': 1, 'kind': 'text', 'text': '+ «OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuv»'}]}
        assert retina.changes(0) == ['+ «OPENAI_API_KEY=••••']
        assert 'abcdefgh' not in retina.feed(0)[1][0]['text']
    finally:
        retina._get = saved

def test_dev_launcher_skips_a_python_without_the_libraries():
    import tempfile
    import dev_launcher as d
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        py314 = root / 'Py314' / 'python.exe'
        py313 = root / 'Py313' / 'python.exe'
        for p in (py314, py313, py313.with_name('pythonw.exe')):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('')
        saved = d.candidates
        try:
            d.candidates = lambda r: [py314, py313]                    # 3.14 первый в PATH, но пустой
            assert d.find_python(root, check=lambda p: p == py313) == [str(py313.with_name('pythonw.exe'))]
            assert d.find_python(root, check=lambda p: False) == []    # ни одного — честное «нет»
        finally:
            d.candidates = saved

class FocusEnv(FakeEnv):
    """FakeEnv с окнами: focus по названию программы, «Почта» — будто кнопка на панели задач."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.focused, self.clicks = [], 0

    def elements(self, img, offset):
        return [dict(e, taskbar=True) if e['text'] == 'Почта' else e for e in super().elements(img, offset)]

    def open_windows(self):
        return '- Telegram «Гера» · монитор 2 · активно'

    def focus_window(self, name):
        self.focused.append(name)
        return 'Telegram «Гера» · монитор 2' if 'telegram' in name.lower() or name == 'Почта' else None

    def perform(self, events):
        self.clicks += sum(1 for e in events if e['e'] == 'click' and e['down'])
        super().perform(events)


def test_loop_switches_windows_by_focus_not_by_taskbar_clicks():
    env = FocusEnv()
    answers = [{'action': 'focus', 'text': 'Telegram'}, by_text('Почта', action='click'),
               {'action': 'focus', 'text': 'Нет такой'}, {'action': 'done', 'answer': 'ок'}]
    with FakeSiberi(answers) as fs:
        r = run_task('сделай что-нибудь', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and env.focused == ['Telegram', 'Почта', 'Нет такой']
    assert env.clicks == 0 and env.page == 'home'                         # по панели задач не кликали
    assert 'окно выведено вперёд' in r['history'][0] and 'окно выведено вперёд' in r['history'][1]
    assert 'нет среди открытых' in r['history'][2]
    assert 'ОТКРЫТЫЕ ОКНА' in fs.prompts[0] and 'монитор 2' in fs.prompts[0]


def test_loop_brings_the_app_named_in_the_task_forward_first():
    from aivision import loop, windows
    def win(proc, title, mon):
        return {'hwnd': hash(title), 'title': title, 'process': proc, 'monitor': mon, 'minimized': False, 'active': False}
    saved = windows.list_windows
    try:
        windows.list_windows = lambda include_own=False: [win('telegram.exe', 'Гера', 2), win('chrome.exe', 'Почта', 1)]
        env = FocusEnv()
        assert loop._focus_named_app('Открой Telegram. Перейди в настройки группы', env, lambda m: None)
        assert env.focused == ['Telegram']
        env.focused.clear()
        assert loop._focus_named_app('Сравни Telegram и Google Chrome', env, lambda m: None) is None   # две — модель
        assert loop._focus_named_app('Зайди на сайт', env, lambda m: None) is None
        assert loop._focus_named_app('Открой Telegram\n\nКОНТЕКСТ: открой Chrome', env, lambda m: None)
        assert env.focused == ['Telegram']                                # контекст не считается
    finally:
        windows.list_windows = saved


def test_windows_find_matches_app_names_and_taskbar_labels():
    from aivision import windows
    def win(proc, title, mini=False):
        return {'hwnd': hash(title), 'title': title, 'process': proc, 'monitor': 2, 'minimized': mini, 'active': False}
    saved = windows.list_windows
    try:
        windows.list_windows = lambda include_own=False: [
            win('chrome.exe', 'Почта — Google Chrome'), win('telegram.exe', 'Гера @ Тимофей', True)]
        assert windows.find('Telegram')['process'] == 'telegram.exe'
        assert windows.find('Telegram Desktop — 1 запущенное окно')['process'] == 'telegram.exe'
        assert windows.find('Google Chrome')['process'] == 'chrome.exe'
        assert windows.find('почта')['process'] == 'chrome.exe'           # по заголовку
        assert windows.find('Discord') is None and windows.find('') is None
        assert windows.describe(windows.list_windows(), here=2).splitlines()[1] == \
            '- Telegram «Гера @ Тимофей» · монитор 2 (этот) · свёрнуто'
    finally:
        windows.list_windows = saved


def test_real_env_follows_the_work_window_across_monitors():
    from aivision import screen
    env = screen.RealEnv.__new__(screen.RealEnv)
    env.follow, env.monitor = True, 1
    shots = []
    saved = screen.grab, screen.RealEnv._work_monitor
    try:
        screen.grab = lambda m: shots.append(m) or ('img', (0, 0))
        screen.RealEnv._work_monitor = staticmethod(lambda: 2)
        env.grab()
        assert env.monitor == 2 and shots == [2]                          # окно на втором — смотрим туда
        env.follow, env.monitor = False, 1
        env.grab()
        assert shots == [2, 1]                                            # монитор задан явно — не прыгаем
    finally:
        screen.grab, screen.RealEnv._work_monitor = saved

def test_rerun_and_approve_run_the_task_and_record_the_result():
    import tempfile
    import time as _t
    from aivision import approval, orchestrator as O, paths, workspace
    from aivision.agents import Agent
    from aivision.weblauncher import Api
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        saved = (paths.TASKS, paths.REPORTS, paths.STATE, approval.APPROVALS, O.tool_step,
                 __import__('aivision.agents', fromlist=['x']).load_agents)
        import aivision.agents as A
        try:
            paths.TASKS, paths.REPORTS, paths.STATE = root / 'tasks', root / 'reports', root / 'state'
            approval.APPROVALS = root / 'approvals'
            for d in (paths.TASKS, paths.REPORTS, paths.STATE):
                d.mkdir(parents=True)
            agent = Agent(name='Писатель', tools=['text'])
            A.load_agents = lambda: [agent]
            ran = []
            O.tool_step = lambda ag, task, ctx, env=None, log=print, **kw: ran.append(task) or {'status': 'done', 'answer': 'сделано'}
            long_task = 'Напиши пост про запуск ' + 'очень подробно, ' * 10
            tid = workspace.new_task(long_task, assignee='Писатель')
            workspace.update_task(tid, status='aborted')
            workspace.journal('Писатель', long_task, 'aborted', 'остановлено пользователем')
            api = Api.__new__(Api)                                    # без окна и сценариев
            api._lock, api.logs, api.notes, api._seq = __import__('threading').Lock(), [], [], 0
            api.team_busy, api.runs, api.team_request, api.team_plan = False, {}, '', []
            api.quick = False
            j = api.journal()
            assert j[0]['rerun'] and j[0]['full'] == long_task and len(j[0]['task']) == 100
            api.team_busy = True                                      # команда занята — мышь не делим
            assert 'занята' in api.rerun(tid)['error']
            api.team_busy = False
            assert api.rerun(tid) == {'ok': True}
            for _ in range(50):
                if not api.team_busy:
                    break
                _t.sleep(0.02)
            assert ran == [long_task] and workspace.read_task(tid)['status'] == 'done'
            assert api.journal()[0]['status'] == 'done'                 # результат виден в «Что происходит»
            assert any('↻ заново' in x['text'] for x in api.logs) and any(x['text'].startswith('→ done') for x in api.logs)
            # одобрение: задача из «ждёт «да»» выполняется и перестаёт висеть
            tid2 = workspace.new_task('Оплати домен', assignee='Писатель')
            workspace.update_task(tid2, status='held')
            aid = approval.request('Писатель', 'Оплати домен')
            assert api.approve(aid) == {'ok': True}
            for _ in range(50):
                if not api.team_busy:
                    break
                _t.sleep(0.02)
            assert ran[-1] == 'Оплати домен' and workspace.read_task(tid2)['status'] == 'done'
            assert approval.pending() == [] and api.approve(aid)['error']
        finally:
            (paths.TASKS, paths.REPORTS, paths.STATE, approval.APPROVALS, O.tool_step, A.load_agents) = saved

def test_quick_task_runs_the_screen_engine_directly_and_saves_as_a_scenario():
    import tempfile
    import threading
    import time as _t
    from aivision import loop, paths, recipe, screen, workspace
    from aivision.scenarios import Store
    from aivision.weblauncher import QUICK, Api
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        saved = (paths.TASKS, paths.REPORTS, paths.STATE, paths.RUNS, loop.run_task, screen.RealEnv, recipe.from_run)
        try:
            paths.TASKS, paths.REPORTS, paths.STATE, paths.RUNS = root / 't', root / 'r', root / 's', root / 'runs'
            for d in (paths.TASKS, paths.REPORTS, paths.STATE):
                d.mkdir(parents=True)
            calls, recipes = [], []
            outcome = {'status': 'done', 'answer': 'чат открыт'}
            loop.run_task = lambda goal, env, **kw: calls.append((goal, kw)) or dict(outcome, steps=2, history=[])
            screen.RealEnv = lambda monitor=0, speed=1.0: object()
            recipe.from_run = lambda sid, goal, run_dir: recipes.append((sid, goal, run_dir)) or object()
            api = Api.__new__(Api)                                    # без окна
            api._lock, api.logs, api.notes, api._seq = threading.Lock(), [], [], 0
            api.team_busy, api.runs, api.team_request, api.team_plan = False, {}, '', []
            api.quick, api._quick_last, api.scn_rev = False, None, 0
            api._store = Store(root / 'scenarios.json')

            def wait():
                for _ in range(100):
                    if not api.team_busy:
                        return
                    _t.sleep(0.02)
            goal = 'Открой Telegram и найди чат «Гера»'
            assert api.quick_go('  ')['error']
            api.quick = True                                             # идёт быстрая — мышь не делим
            assert 'быстрая' in api._busy() and 'быстрая' in api.quick_go('ещё')['error']
            api.quick = False
            assert api.quick_go(goal) == {'ok': True}
            wait()
            assert calls[0][0] == goal                                   # слова человека — прямо в движок
            assert 'role' not in calls[0][1] and 'command' not in calls[0][1]   # без роли агента и чек-листа
            t = workspace.list_tasks()[0]
            assert t['assignee'] == QUICK and t['status'] == 'done' and t['answer'] == 'чат открыт'
            texts = [x['text'] for x in api.logs]
            assert texts[0] == f'⚡ быстрая задача: {goal}' and '== итог: done ==' in texts
            assert not any('план' in x for x in texts)                   # ни плана, ни подхода
            r = api.quick_save('')                                       # пустое поле — последняя выполненная
            assert r['ok'] and r['recipe'] and r['name'] == goal
            assert api._store.scenarios[-1].goal == goal and recipes[0][1] == goal and api.scn_rev == 1
            assert api.quick_save('Нажми «Опубликовать»')['recipe'] is False  # не выполнялась — без рецепта
            outcome.update(status='stuck', answer='не нашла')            # не вышло — «заново» снова быстрая
            api.quick_go('Закрой окно')
            wait()
            tid = next(x['id'] for x in workspace.list_tasks() if x['goal'] == 'Закрой окно')
            assert api.rerun(tid) == {'ok': True}
            wait()
            assert [c[0] for c in calls] == [goal, 'Закрой окно', 'Закрой окно']
        finally:
            (paths.TASKS, paths.REPORTS, paths.STATE, paths.RUNS, loop.run_task, screen.RealEnv, recipe.from_run) = saved


def test_second_launch_shows_the_open_launcher_instead_of_a_new_one():
    import threading
    import uuid
    from aivision import single
    name, port = 'Local' + chr(92) + f'AIVisionTest-{uuid.uuid4().hex}', 18767
    saved = single._mutex
    try:
        assert single.acquire(name) is True                              # первый запуск — наш
        assert single.acquire(name) is False                             # второй — уже занято
        shown = threading.Event()
        assert single.serve(shown.set, port=port)
        assert single.ask_to_show(port=port, wait=2) and shown.wait(2)    # «покажись» дошло
        assert single.ask_to_show(port=port + 1, wait=0.3) is False      # некому — честное «нет»
    finally:
        single._mutex = saved


def test_a_hung_launcher_is_detected_and_replaced_instead_of_blocking_launches():
    import subprocess
    import uuid
    from aivision import single
    name, port = 'Local' + chr(92) + f'AIVisionTest-{uuid.uuid4().hex}', 18769
    # «Зависший» лаунчер: держит мьютекс, слушает порт, но никому не отвечает
    code = (f'import socket, sys, time; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r}); '
            f'from aivision import single; assert single.acquire({name!r}); '
            f's = socket.socket(); s.bind(("127.0.0.1", {port})); s.listen(4); print("ready", flush=True); '
            f'time.sleep(60)')
    hung = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True)
    saved = single._mutex
    try:
        assert hung.stdout.readline().strip() == 'ready'
        assert single.acquire(name) is False
        assert single.ask_to_show(port=port, wait=1, reply=0.5) is False          # подключились, ответа нет
        assert single.holder_pid(port) == hung.pid
        assert single.take_over(hung.pid, name) is True                          # закрыли и заняли место
        assert hung.wait(5) is not None
    finally:
        hung.kill()
        single._mutex = saved

def test_focus_switch_stops_when_the_user_took_the_mouse():
    from aivision import screen, windows
    from aivision.motion import Aborted
    class Mouse:
        position = (500, 500)
    env = screen.RealEnv.__new__(screen.RealEnv)
    env._mouse, env._left_at, env.monitor = Mouse(), (100, 100), 1     # агент оставил мышь в (100,100)
    saved = windows.find, windows.focus
    try:
        windows.find = lambda name: {'hwnd': 1, 'title': 'Гера', 'process': 'telegram.exe', 'monitor': 2,
                                     'minimized': False, 'active': False}
        focused = []
        windows.focus = lambda h, wait=1.5: focused.append(h) or True
        try:
            env.focus_window('Telegram')
            raise AssertionError('окно отобрали у человека')
        except Aborted:
            pass
        assert focused == []                                            # фокус не трогали
        env._left_at = (500, 500)                                       # мышь там, где оставил агент
        assert 'монитор 2' in env.focus_window('Telegram') and focused == [1]
    finally:
        windows.find, windows.focus = saved

def test_tasks_cut_off_by_a_restart_become_rerunnable_and_busy_mark_follows_work():
    import tempfile
    import threading
    import time as _t
    from aivision import paths, workspace
    from aivision.weblauncher import Api
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        saved = paths.TASKS, paths.REPORTS, paths.STATE
        try:
            paths.TASKS, paths.REPORTS, paths.STATE = root / 'tasks', root / 'reports', root / 'state'
            for d in (paths.TASKS, paths.REPORTS, paths.STATE):
                d.mkdir(parents=True)
            tid = workspace.new_task('Перейди в раздел «Реакции»', assignee='Публикатор')
            workspace.update_task(tid, status='in_progress')                 # процесс умер посреди задачи
            done = workspace.new_task('Готовая', assignee='Публикатор')
            workspace.update_task(done, status='done')
            api = Api.__new__(Api)
            api._lock, api.logs, api.notes, api._seq = threading.Lock(), [], [], 0
            api.team_busy, api.runs, api.recording, api.team_request = False, {}, False, ''
            assert api.recover_orphans() == 1
            assert workspace.read_task(tid)['status'] == 'aborted' and workspace.read_task(done)['status'] == 'done'
            j = api.journal()
            assert j[0]['status'] == 'aborted' and j[0]['rerun'] and j[0]['tid'] == tid
            mark = root / 'state' / 'launcher.busy'
            mark.write_text('остался от упавшего лаунчера', encoding='utf-8')
            api.watch_busy(mark, period=0.02)
            _t.sleep(0.1)
            assert not mark.exists()                                         # старую метку убрали
            api.team_busy = True
            _t.sleep(0.1)
            assert mark.exists()                                             # работаем — метка есть
            api.team_busy = False
            _t.sleep(0.1)
            assert not mark.exists()
        finally:
            paths.TASKS, paths.REPORTS, paths.STATE = saved


def test_writing_and_summaries_go_through_siberi_not_groq():
    from aivision import hearing, siberi, writing
    asked = []
    saved = siberi.ask
    try:
        siberi.ask = lambda system, prompt, **kw: asked.append((system, prompt)) or '  готово  '
        assert writing.write('напиши пост', material='факты', system='ты копирайтер') == 'готово'
        assert asked[-1] == ('ты копирайтер', 'напиши пост\n\nИСХОДНЫЕ ДАННЫЕ:\nфакты')
        assert hearing.summarize('длинная расшифровка') == 'готово' and asked[-1][1] == 'длинная расшифровка'
        siberi.ask = lambda system, prompt, **kw: ''
        try:
            writing.write('пусто')
            raise AssertionError('пустой текст должен быть ошибкой')
        except RuntimeError:
            pass
    finally:
        siberi.ask = saved
    import aivision
    assert not (Path(aivision.__file__).parent / 'groq_vision.py').exists()     # быстрого зрения Groq больше нет


class TrapEnv(FakeEnv):
    """В письме тема — ловушка: клик по ней (будто по кнопке под затемнением) возвращает к списку."""
    def _page_letter(self):
        img = super()._page_letter()
        self.regions.append(([290, 160, 1000, 215], lambda: self._go('mail')))
        return img


def test_loop_blames_the_click_that_threw_it_back_by_accident():
    env = TrapEnv('mail')
    anna, trap = by_text('Анна Смирнова', action='click'), by_text('Документы к встрече', action='click')
    answers = [anna, trap, anna, trap, anna, trap, {'action': 'done', 'answer': 'договор у Анны'}]
    with FakeSiberi(answers) as fs:
        r = run_task('открой последнее письмо и найди в нём номер договора', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and env.page == 'letter'                  # третий заход в тему не пустили
    loops = [h for h in r['history'] if 'ЦИКЛ: экран вернулся' in h]
    assert len(loops) == 2 and all('Документы' in h and 'Анна' not in h for h in loops)
    rejected = [h for h in r['history'] if 'ОТКЛОНЕНО' in h]
    assert len(rejected) == 1 and 'Документы' in rejected[0] and 'возвращало назад' in rejected[0]
    assert not any('Анна Смирнова' in h and 'ОТКЛОНЕНО' in h for h in r['history'])   # путь вперёд не закрыт
    assert any('ЦИКЛ' in p for p in fs.prompts[2:])                        # модель об этом узнала


def test_loop_marks_a_section_it_had_to_leave_as_a_dead_end():
    env = FakeEnv('mail')
    anna, back = by_text('Анна Смирнова', action='click'), by_text('Назад', action='click')
    answers = [anna, back, anna, back, anna, by_text('Яндекс Маркет', action='click'),
               {'action': 'done', 'answer': 'заказ 48213'}]
    logs = []
    with FakeSiberi(answers) as fs:
        r = run_task('найди номер заказа в почте', env, refine=False, log=logs.append)
    assert r['status'] == 'done' and env.letter == 1                       # пошла в другое письмо
    dead = [h for h in r['history'] if 'ТУПИК' in h or 'ЦИКЛ' in h]
    assert len(dead) == 2 and all('Анна' in h and 'Назад' in h for h in dead)   # виноват вход, не «Назад»
    assert 'ТУПИК' in dead[0] and 'ЦИКЛ' in dead[1]
    rejected = [h for h in r['history'] if 'ОТКЛОНЕНО' in h]
    assert len(rejected) == 1 and 'Анна' in rejected[0] and 'тупик' in rejected[0]
    assert not any('Назад' in h and 'ОТКЛОНЕНО' in h for h in r['history'])
    assert 'ТУПИК' in fs.prompts[2] and any('⟲ тупик' in m for m in logs)


def test_deliberate_back_steps_are_told_apart_from_other_clicks():
    from aivision.loop import _deliberate_back
    def click(text):
        return {'action': 'click', 'mark': {'text': text}}
    assert all(_deliberate_back(click(t)) for t in ('Закрыть', '← Назад', 'Отмена', '×', 'Close'))
    assert not any(_deliberate_back(click(t)) for t in ('Сохранить', 'Закрепление сообщений', 'Xbox', ''))
    assert _deliberate_back({'action': 'key', 'keys': 'esc'})
    assert not _deliberate_back({'action': 'key', 'keys': 'enter'})


def test_new_elements_mark_a_panel_on_top_but_not_a_new_page():
    from aivision.loop import _bucket, mark_new, signature
    def el(text, x, y):
        return {'text': text, 'role': 'кнопка', 'box': [x, y, x + 100, y + 30]}
    base = [el('Управление', 1700, 100), el('Чат', 100, 300), el('Поиск', 100, 50), el('Профиль', 1700, 50)]
    before = {_bucket(e) for e in base}
    panel = base + [el('Управление', 900, 500), el('Права', 900, 560)]
    assert mark_new(panel, before) == 2
    assert [e['text'] for e in panel if e.get('new')] == ['Управление', 'Права']
    page = [el('Совсем', 10, 10), el('Другая', 300, 300), el('Страница', 600, 600)]
    assert mark_new(page, before) == 0 and not any(e.get('new') for e in page)    # переход — не панель
    a = {'action': 'click', 'mark': panel[0], 'text': '', 'keys': ''}
    b = {'action': 'click', 'mark': panel[4], 'text': '', 'keys': ''}
    assert signature(a) != signature(b)                     # две «Управление» — разные цели
    from aivision.prompt import describe_marks_text
    for i, e in enumerate(panel, 1):
        e['id'] = i
    assert describe_marks_text(panel, (1920, 1080)).splitlines()[4].endswith('[новое]')


def _dim(img, k=0.45):
    from PIL import Image
    return Image.eval(img, lambda v: int(v * k))


def _dialog(img, box, fill='white'):
    from PIL import ImageDraw
    img = img.copy()
    ImageDraw.Draw(img).rounded_rectangle(box, 18, fill=fill)
    return img


def test_modal_detects_a_dialog_over_a_dimmed_screen_and_its_closing():
    from aivision.modal import ModalTracker, detect
    base, _ = make_page()
    box = [720, 200, 1200, 820]
    opened = _dialog(_dim(base), box)
    found = detect(base, opened)
    assert found and all(abs(a - b) <= 16 for a, b in zip(found, box))
    assert detect(base, _dim(base)) is None                         # просто потемнело — окна нет
    assert detect(base, _dialog(base, box, '#f3f3f3')) is None      # плашка без затемнения
    other, _ = make_page()
    assert detect(base, _dialog(other.transpose(0), [0, 0, 900, 1080])) is None   # другая страница
    t = ModalTracker()
    assert t.update(None, base) is None
    assert t.update(base, opened) == found
    inner = _dialog(_dim(opened), [840, 360, 1080, 640])          # окно поверх окна
    top = t.update(opened, inner)
    assert len(t.stack) == 2 and top[0] >= 820 and top[2] <= 1100
    assert t.update(inner, opened) == found and len(t.stack) == 1   # верхнее закрыли
    assert t.update(opened, base) is None and not t.stack


class ModalEnv(FakeEnv):
    """Главная с кнопкой «Реакции»: она открывает окно посреди экрана, фон затемняется,
    клик мимо окна его закрывает. За затемнением — своя кнопка «Управление»."""
    BOX = [720, 200, 1200, 820]

    def __init__(self):
        super().__init__('home')
        self.modal, self.saved, self.closed = False, False, 0
        self.icons, self.icons_all = 30, False      # поле реакций: стирается, но эмодзи с клавиатуры не берёт

    def _key(self, key, down):
        if down and self.focus == 'icons' and key != 'Key.ctrl':
            if key == 'a' and self.ctrl:
                self.icons_all = True
            elif key in ('Key.delete', 'Key.backspace'):
                self.icons, self.icons_all = 0 if self.icons_all else max(0, self.icons - 1), False
            return
        super()._key(key, down)

    def _button(self, d, text, x, y, fill='#e8f0fe'):
        d.rounded_rectangle([x - 110, y - 24, x + 110, y + 24], 10, fill=fill)
        d.text((x, y), text, font=_f(20, True), fill='#1a4fa0', anchor='mm')
        return [x - 110, y - 24, x + 110, y + 24]

    def _render(self):
        from PIL import ImageDraw
        img = super()._render()
        d = ImageDraw.Draw(img)
        opener = self._button(d, 'Реакции', 1600, 560)
        self._button(d, 'Управление', 1600, 640)
        if not self.modal:
            self.regions.append((opener, self._open))
            return img
        img = _dialog(_dim(img), self.BOX)
        d = ImageDraw.Draw(img)
        d.text((760, 250), 'Настройка реакций', font=_f(26, True), fill='#000', anchor='lm')
        field = [760, 500, 980, 566]
        if self.icons:
            _emoji_grid(d, 760, 500, rows=3, cols=10)
        else:
            d.text((760, 530), 'Добавить реакции...', font=_f(18), fill='#999', anchor='lm')
        inner = self._button(d, 'Управление', 960, 420)
        save = self._button(d, 'Сохранить', 960, 740)
        self.regions = [(field, self._focus('icons')), (inner, lambda: None), (save, self._save),
                        (self.BOX, lambda: None), ([0, 0, img.width, img.height], self._close)]
        self.hovers = []
        return img

    def _open(self):
        self.modal = True

    def _save(self):
        self.modal, self.saved = False, True

    def _close(self):
        self.modal, self.closed = False, self.closed + 1


def test_loop_works_only_inside_an_open_dialog():
    env = ModalEnv()
    answers = [by_text('Реакции', action='click'),
               {'action': 'click', 'x': 1600, 'y': 640},              # «Управление» под затемнением
               by_text('Сохранить', action='click'),
               {'action': 'done', 'answer': 'сохранено'}]
    logs = []
    with FakeSiberi(answers) as fs:
        r = run_task('настрой реакции и сохрани', env, refine=False, log=logs.append)
    assert r['status'] == 'done' and env.saved and env.closed == 0      # окно мимо не закрывали
    assert any('открылось всплывающее окно' in m for m in logs)
    assert any('всплывающее окно закрылось' in m for m in logs)
    assert any('вне всплывающего окна' in h for h in r['history'])
    dialog = fs.prompts[1]
    assert 'ОТКРЫТО ВСПЛЫВАЮЩЕЕ ОКНО' in dialog and 'ВСПЛЫВАЮЩЕЕ' not in fs.prompts[0]
    assert len(re.findall(r'\[\d+\] [^«\n]*«Управление', dialog)) == 1    # фон из списка убран
    assert not re.search(r'\[\d+\] [^«\n]*«Найти', dialog)
    assert re.search(r'\[\d+\] значки «набор из ~\d+ значков', dialog)    # поле из эмодзи — элемент
    assert all(fs.images[1:3])                                         # в окне — по картинке
    assert fs.shown[0] is None and fs.shown[1] == fs.shown[2]            # модели — вырез окна
    assert all(abs(a - b) <= 16 for a, b in zip(fs.shown[1], ModalEnv.BOX))


def test_an_icon_field_that_drops_typed_icons_is_reported_and_is_not_a_loop():
    env = ModalEnv()
    answers = [by_text('Реакции', action='click'),
               by_text('набор из', action='type', text='👍🔥❤️', clear=True),
               {'action': 'done', 'answer': 'ок'}]
    with FakeSiberi(answers) as fs:
        r = run_task('оставь в реакциях только 👍, 🔥 и ❤️', env, refine=False, log=lambda m: None)
    assert env.modal and env.icons == 0 and env.closed == 0
    typed = next(h for h in r['history'] if '← ввод' in h)
    assert 'ОПУСТЕЛО' in typed and 'панель выбора' in typed
    assert not any('ЦИКЛ' in h for h in r['history'])                  # стёрли значки — не «вернулись назад»
    assert 'ОПУСТЕЛО' in fs.prompts[2]


def test_a_click_into_a_field_is_not_a_failure_even_if_nothing_visible_changes():
    env = ModalEnv()
    into = by_text('набор из', action='click')
    answers = [by_text('Реакции', action='click'), into, into, into, {'action': 'done', 'answer': 'ок'}]
    with FakeSiberi(answers):
        r = run_task('поставь курсор в поле реакций', env, refine=False, log=lambda m: None)
    assert r['status'] == 'done' and env.focus == 'icons'
    clicks = [h for h in r['history'] if 'набор из' in h]
    assert len(clicks) == 3 and all('курсор стоит в поле' in h for h in clicks)
    assert not any('ОТКЛОНЕНО' in h or 'НЕ изменился' in h for h in r['history'])   # не «провал»


def test_a_picker_opened_over_a_dialog_widens_the_working_area():
    from PIL import ImageDraw
    from aivision.modal import ModalTracker
    base, _ = make_page()
    box = [720, 200, 1200, 820]
    opened = _dialog(_dim(base), box)
    t = ModalTracker()
    found = t.update(base, opened)
    picker = opened.copy()
    d = ImageDraw.Draw(picker)
    d.rounded_rectangle([760, 120, 1260, 420], 12, fill='#fafafa', outline='#dddddd')
    _emoji_grid(d, 780, 170, rows=5, cols=18)
    wide = t.update(opened, picker)
    assert len(t.stack) == 1 and wide[0] == found[0] and wide[3] == found[3]
    assert wide[1] <= 128 and wide[2] >= 1256                           # панель вылезла вверх и вправо
    assert t.update(picker, opened) == found                           # панель закрыли — рамка как была


def _emoji_grid(d, x, y, rows, cols, size=18, gap=4):
    """Ряды «эмодзи»: разноцветные кружки с глазками вплотную, как в поле реакций."""
    hues = ['#f44336', '#ffc107', '#ff9800', '#4caf50', '#e91e63', '#ffeb3b', '#9c27b0', '#2196f3']
    for r in range(rows):
        for c in range(cols):
            cx, cy = x + c * (size + gap), y + r * (size + gap)
            d.ellipse([cx, cy, cx + size, cy + size], fill=hues[(r * cols + c) % len(hues)])
            d.ellipse([cx + 5, cy + 6, cx + 7, cy + 8], fill='#222')
            d.ellipse([cx + 11, cy + 6, cx + 13, cy + 8], fill='#222')
    return [x, y, x + cols * (size + gap) - gap, y + rows * (size + gap) - gap]


def test_icon_sets_are_found_on_flat_fields_and_not_in_text_avatars_or_photos():
    import random
    from PIL import Image, ImageDraw
    from aivision.glyphs import add_sets, find_sets
    img = Image.new('RGB', (1200, 800), 'white')
    d = ImageDraw.Draw(img)
    truth = _emoji_grid(d, 100, 100, rows=5, cols=12)
    for i in range(6):                                                  # столбик аватарок
        d.ellipse([700, 60 + i * 62, 746, 106 + i * 62], fill=['#e53935', '#43a047', '#1e88e5'][i % 3])
    d.text((100, 300), 'Создавать собственные наборы', font=_f(22, True), fill='#1a73e8')   # ссылка
    for i in range(10):                                                 # ряд одноцветных квадратов
        d.rectangle([100 + i * 22, 400, 118 + i * 22, 418], fill='#2196f3')
    rnd = random.Random(1)
    for _ in range(4000):                                               # пёстрая «фотография»
        x, y = rnd.randrange(800, 1180), rnd.randrange(450, 780)
        d.ellipse([x, y, x + 14, y + 14], fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    for c in range(10):                                                 # одноцветные «смайлы» — ползунок, статусы
        d.ellipse([100 + c * 22, 480, 118 + c * 22, 498], fill='#2196f3')
        d.ellipse([105 + c * 22, 486, 107 + c * 22, 488], fill='#222')
    hues = ['#f44336', '#ffc107', '#4caf50', '#2196f3', '#9c27b0']
    for r in range(3):                                                  # мелкие цветные точки — не значки
        for c in range(12):
            d.ellipse([100 + c * 12, 560 + r * 12, 108 + c * 12, 568 + r * 12], fill=hues[(r + c) % 5])
    for x in range(400, 640, 2):                                        # те же значки на текстуре (обои, фото)
        for y in range(560, 700, 2):
            d.point((x, y), fill=(rnd.randrange(30, 220),) * 3)
    for r in range(4):
        for c in range(9):
            d.ellipse([410 + c * 26, 570 + r * 30, 428 + c * 26, 588 + r * 30], fill=hues[(r * 9 + c) % 5])
    for r in range(2):                                                  # галерея аватарок — крупнее значков
        for c in range(6):
            d.ellipse([700 + c * 44, 500 + r * 44, 736 + c * 44, 536 + r * 44], fill=hues[(r + c) % 5])
    for c in range(10):                                                 # цветные кольца-статусы — не сплошные
        d.ellipse([100 + c * 26, 680, 120 + c * 26, 700], outline=hues[c % 5], width=6)
    sets = find_sets(img)
    assert len(sets) == 1, sets
    assert all(abs(a - b) <= 8 for a, b in zip(sets[0]['box'], truth))
    assert sets[0]['count'] >= 40
    noise = {'id': 1, 'role': 'текст', 'text': '•а• ©', 'box': [140, 120, 200, 140]}   # OCR по значкам
    title = {'id': 2, 'role': 'текст', 'text': 'Доступные реакции', 'box': [100, 70, 300, 90]}
    els = add_sets(img, [noise, title])
    assert [e['text'] for e in els] == ['Доступные реакции', f"набор из ~{sets[0]['count']} значков"]
    assert [e['id'] for e in els] == [1, 2] and els[1]['role'] == 'значки'
    assert find_sets(Image.new('RGB', (800, 600), 'white')) == []


def test_compose_shows_an_open_dialog_cropped_with_screen_rulers():
    from PIL import Image
    from aivision.overlay import REGION_MARGIN, REGION_ZOOM, compose
    img = Image.new('RGB', (1920, 1080), 'white')
    box = [800, 300, 1200, 600]
    out = compose(img, [{'id': 1, 'box': [900, 400, 1000, 430]}], step=50, region=box)
    w = round((box[2] - box[0] + 2 * REGION_MARGIN) * REGION_ZOOM)
    h = round((box[3] - box[1] + 2 * REGION_MARGIN) * REGION_ZOOM)
    assert out.size == (w + 2 * 48, h + 2 * 48)
    assert compose(img, [], region=None).size == (1920 + 96, 1080 + 96)


def test_names_from_the_task_are_matched_exactly_not_by_resemblance():
    from aivision.loop import _goal_names, _name_match, name_warnings
    goal = 'В настройках группы «Откликс» перейди в раздел «Реакции».\n\nКОНТЕКСТ: «Чужое»'
    assert _goal_names(goal) == ['Откликс', 'Реакции']
    assert _name_match('Откликс', '•а• 0tklocal') is False                # похоже, но другое
    assert _name_match('Откликс', 'Бот, Otklocal, Радар запущен') is False
    assert _name_match('Откликс', 'Откликс | otkliks.ru') is True
    assert _name_match('Откликс', 'OTKLIKS') is True                        # транслит — то же
    assert _name_match('Реакции', 'Реакция') is True                        # окончание не в счёт
    assert _name_match('Откликс', 'sippin on wine') is None
    els = [{'id': 5, 'text': '•а• 0tklocal'}, {'id': 6, 'text': 'Реакции'}]
    warn = name_warnings(goal, els)
    assert len(warn) == 1 and '«Откликс» из цели на экране НЕТ' in warn[0] and '[5]' in warn[0]
    assert name_warnings(goal, els + [{'id': 7, 'text': 'Откликс | otkliks.ru'}]) == []


class ChatEnv(FakeEnv):
    """Мессенджер: в списке чатов есть бот «Otklocal», а группа «Откликс» — только через поиск."""
    def __init__(self):
        super().__init__('home')
        self.chat = None

    def title(self):
        return f'{self.chat or "Telegram"} — Telegram'

    def _items(self):
        rows = [('поле ввода', 'Поиск', [20, 60, 400, 100], self._focus('search')),
                ('текст', '•а• 0tklocal', [20, 150, 400, 190], lambda: setattr(self, 'chat', 'Otklocal')),
                ('текст', 'sippin on wine', [20, 200, 400, 240], lambda: setattr(self, 'chat', 'sippin'))]
        if 'откликс' in self.query.lower():
            rows.append(('текст', 'Откликс | otkliks.ru', [20, 250, 400, 290], lambda: setattr(self, 'chat', 'Откликс')))
        return rows

    def _render(self):
        from PIL import Image, ImageDraw
        self.regions, self.drags, self.hovers, self.dbl = [], [], [], []
        img = Image.new('RGB', (1920, 1080), 'white')
        d = ImageDraw.Draw(img)
        for _, text, box, fn in self._items():
            d.rectangle(box, outline='#999')
            d.text((box[0] + 10, box[1] + 10), self.query if text == 'Поиск' and self.query else text, font=_f(18), fill='#000')
            self.regions.append((box, fn))
        return img

    def elements(self, img, offset):
        return [{'id': i, 'role': role, 'text': text, 'box': box}
                for i, (role, text, box, _) in enumerate(self._items(), 1)]


def test_loop_searches_for_the_named_chat_and_does_not_give_up_after_a_wrong_turn():
    env = ChatEnv()
    answers = [by_text('•а• 0tklocal', action='click'),                    # похожее имя — откажем
               {'action': 'fail', 'answer': 'группы «Откликс» нет в списке чатов'},   # рано сдаётся
               by_text('Поиск', action='type', text='Откликс'),
               by_text('Откликс', action='click'),
               {'action': 'done', 'answer': 'открыта группа'}]
    logs = []
    with FakeSiberi(answers) as fs:
        r = run_task('Открой группу «Откликс»', env, refine=False, log=logs.append)
    assert r['status'] == 'done' and env.chat == 'Откликс'
    assert 'ВНИМАНИЕ: «Откликс» из цели на экране НЕТ' in fs.prompts[0]
    assert any('не «Откликс», а похожее' in h for h in r['history'])
    assert any('fail ОТКЛОНЁН' in h for h in r['history'])
    assert any(m.strip().startswith('⤴ рано сдаваться') for m in logs)
    assert 'ВНИМАНИЕ' not in fs.prompts[3]                                 # нашлось — предупреждать незачем


def test_launcher_api_gives_pywebview_no_objects_to_crawl():
    """pywebview на старте обходит публичные поля js_api вглубь; окно (WinForms) и трей там —
    это десятки тысяч свойств из чужого потока, и окно лаунчера зависало («Python не отвечает»)."""
    from aivision.weblauncher import Api
    api = Api()
    crawled = [n for n in dir(api) if not n.startswith('_')
               and not callable(getattr(api, n)) and hasattr(getattr(api, n), '__module__')]
    assert crawled == []
    assert api._window is None and api._tray is None


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if re.match(r'test_', k)]
    for t in tests:
        t()
        print(f'✅ {t.__name__}')
    print(f'{len(tests)} тестов пройдено')
