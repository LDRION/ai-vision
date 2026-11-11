@echo off
rem Сборка exe-файлов AI Vision.
echo Сборка AI Vision...
pip install pyinstaller --quiet

echo.
echo [1/2] AiVisionDev.exe — запуск из живого кода + автоперезапуск при сохранении правок
python -m PyInstaller --noconfirm --clean AiVisionDev.spec

echo.
echo [2/2] AiVision.exe — автономный снимок (переносится на машину без Python)
python -m PyInstaller --noconfirm --clean AiVision.spec

echo.
echo Готово:
echo   dist\AiVisionDev.exe  — для разработки: всегда берёт текущий код, сам перезапускается
echo   dist\AiVision\AiVision.exe — автономный, обновляется только пересборкой
echo.
echo Рядом с exe нужен .env с SIBERI_API_KEY. Папки runs, scenarios, experience,
echo recipes создаются там же при первом запуске.
pause
