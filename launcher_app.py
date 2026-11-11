"""Точка входа для сборки в exe (PyInstaller). Запускает лаунчер AI Vision.

`AiVision.exe --retina watch ...` — тот же exe в роли встроенной Screen Retina:
в собранном виде нет отдельного Python, поэтому службу запускаем самим exe
(см. aivision/services.py).
"""

import sys

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--retina':
        if sys.stdout is None:               # оконный exe без консоли: печать Retina — в её лог
            sys.stdout = sys.stderr = open('retina.log', 'a', encoding='utf-8')
        from aivision.vendor.retina.__main__ import main as retina_main
        sys.argv = ['retina'] + sys.argv[2:]
        retina_main()
    else:
        from aivision.__main__ import launch_ui
        launch_ui(classic='--classic' in sys.argv)
