# Встроенная копия Screen Retina

Источник: `C:\Projects\screen-retina` (репозиторий github.com/LDRION/screen-retina), коммит `cd5fd8e`.
Скопировано: 2026-10-19 скриптом `tools/vendor_retina.py`.

Это **замороженная копия**: AiVision запускает именно её (`python -m aivision.vendor.retina watch`,
порт 8766, см. `aivision/services.py`) и не зависит от внешнего проекта. Внешнюю Retina можно
менять или удалить — здесь ничего не сломается.

Код здесь не правим руками: правки — в самой Retina, потом обновление копии:

    python tools/vendor_retina.py
    python -m pytest tests/retina -q && python -m tests.test_offline

Что AiVision использует из её API (при обновлении это должно остаться совместимым):
`GET /stats`, `GET /elements?area&exclude&refresh`, `GET /find?q&area&limit&exclude`,
`GET /settled?area&quiet&timeout&expect`, `GET /events?since`, `POST /exclude {"prefixes"}`.
