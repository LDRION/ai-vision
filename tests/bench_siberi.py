"""
Живой замер точности на синтетической странице (tests/fake_page.py):
  raw  — голый скриншот, Siberi сама называет координаты (одним запросом на все цели);
  full — боевой пайплайн aivision.agent.locate: метки + сетка + zoom, по запросу на цель.

    python tests/bench_siberi.py [--only full|raw]
"""

import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aivision import siberi                      # noqa: E402
from aivision.agent import locate, parse_json    # noqa: E402
from aivision.detect import ocr_elements         # noqa: E402
from tests.fake_page import make_page            # noqa: E402

TARGETS = {
    'btn_login':     'войти в аккаунт',
    'btn_search':    'нажать кнопку «Найти»',
    'link_Маркет':   'перейти в Маркет',
    'tile_Такси':    'открыть сервис Такси',
    'icon_settings': 'открыть настройки (иконка шестерёнки рядом с «Войти»)',
    'icon_mic':      'включить голосовой поиск (иконка микрофона в строке поиска)',
    'icon_close_ad': 'закрыть рекламный баннер',
}

RAW_SYSTEM = (
    'Ты — зрение агента, управляющего мышью. Дан скриншот экрана {w}×{h} пикселей. '
    'Для каждой задачи дай координаты центра элемента, по которому надо кликнуть, '
    'в пикселях этого скриншота (0,0 — левый верхний угол). '
    'Ответ — строго один JSON-объект: {{"<id задачи>": {{"x": <целое>, "y": <целое>}}, ...}}'
)


def score(name, x, y, truth):
    x1, y1, x2, y2 = truth[name]
    hit = x1 <= x <= x2 and y1 <= y <= y2
    dist = math.hypot(x - (x1 + x2) / 2, y - (y1 + y2) / 2)
    return hit, dist


def run_raw(img, truth, out):
    tasks = '\n'.join(f'- {k}: {v}' for k, v in TARGETS.items())
    t0 = time.time()
    raw = siberi.ask(RAW_SYSTEM.format(w=img.width, h=img.height), f'Задачи:\n{tasks}',
                     images=[siberi.image_to_data_url(img)])
    (out / 'raw_answer.txt').write_text(raw, encoding='utf-8')
    ans = parse_json(raw)
    rows = []
    for k in TARGETS:
        p = ans.get(k) or {}
        if 'x' in p and 'y' in p:
            hit, dist = score(k, float(p['x']), float(p['y']), truth)
            rows.append((k, 'raw', p['x'], p['y'], hit, dist))
        else:
            rows.append((k, 'raw', None, None, False, None))
    return rows, time.time() - t0


def run_full(img, truth, out, elements):
    def one(k):
        d = out / k
        d.mkdir(exist_ok=True)
        try:
            r = locate(img, TARGETS[k], run_dir=d, log=lambda m: None, elements=elements)
        except Exception as e:
            return (k, f'error: {e}', None, None, False, None)
        if r['x'] is None:
            return (k, r['action'], None, None, False, None)
        hit, dist = score(k, r['x'], r['y'], truth)
        return (k, r['source'], r['x'], r['y'], hit, dist)

    t0 = time.time()
    # Не больше 2 параллельно: Siberi — браузерный движок, залп запросов кладёт его
    with ThreadPoolExecutor(2) as ex:
        rows = list(ex.map(one, TARGETS))
    return rows, time.time() - t0


def main():
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    out = ROOT / 'runs' / f'bench-{datetime.now():%Y%m%d-%H%M%S}'
    out.mkdir(parents=True)
    img, truth = make_page()
    img.save(out / 'page.png')
    elements = ocr_elements(img)

    jobs = {}
    with ThreadPoolExecutor(2) as ex:
        if only in (None, 'raw'):
            jobs['raw'] = ex.submit(run_raw, img, truth, out)
        if only in (None, 'full'):
            jobs['full'] = ex.submit(run_full, img, truth, out, elements)
        results = {k: f.result() for k, f in jobs.items()}

    report = {}
    for variant, (rows, secs) in results.items():
        hits = sum(r[4] for r in rows)
        print(f'\n=== {variant}: {hits}/{len(rows)} попаданий, {secs:.0f} с')
        for k, src, x, y, hit, dist in rows:
            d = f'{dist:6.1f}px' if dist is not None else '   —  '
            print(f"  {'✅' if hit else '❌'} {k:<15} {str(src):<6} ({x}, {y})  от центра {d}")
        report[variant] = {'seconds': round(secs), 'rows': rows}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\nПодробности: {out}')


if __name__ == '__main__':
    main()
