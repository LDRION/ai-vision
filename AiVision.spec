# -*- mode: python ; coding: utf-8 -*-
# Сборка лаунчера:  pyinstaller AiVision.spec   (результат — dist/AiVision/AiVision.exe)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# Пакеты с данными/нативными модулями, которые PyInstaller сам не находит целиком:
# customtkinter — темы и ассеты; winocr/winrt — встроенный OCR; uiautomation/comtypes —
# дерево доступности; cv2 — запись видео; mss — захват экрана.
for pkg in ('customtkinter', 'winocr', 'winrt', 'uiautomation', 'comtypes', 'cv2', 'mss', 'webview'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules('winrt')
# Встроенная Screen Retina: exe запускает сам себя с --retina (см. launcher_app.py, aivision/services.py)
hiddenimports += collect_submodules('aivision.vendor.retina')
# Окно лаунчера — веб-вид: разметка, стили, шрифты (aivision/webui) едут рядом с кодом.
datas += [('aivision/webui', 'aivision/webui')]
hiddenimports += ['pynput.keyboard._win32', 'pynput.mouse._win32', 'macrorec.replayer']

a = Analysis(
    ['launcher_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['tkinter.test', 'pytest', 'matplotlib'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AiVision',
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AiVision')
