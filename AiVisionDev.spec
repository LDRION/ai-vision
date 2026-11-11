# -*- mode: python ; coding: utf-8 -*-
# Небольшой dev-exe: запускает приложение из живого кода и перезапускает при
# сохранении правок. Лёгкий (только stdlib) — рядом нужен установленный Python.
#   pyinstaller AiVisionDev.spec   →   dist/AiVisionDev.exe

a = Analysis(['dev_launcher.py'], pathex=[], binaries=[], datas=[],
             hiddenimports=[], excludes=['tkinter', 'numpy', 'cv2', 'PIL'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='AiVisionDev',
          console=True, onefile=True, disable_windowed_traceback=False)
