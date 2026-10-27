@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ====================================================
echo Паспортний сервіс (Мюнхен) — Автоматичний моніторинг
echo ====================================================

:: Перевірка наявності Python
python --version >nul 2>&1
if errorlevel 1 goto :no_python

echo [1/3] Перевірка та встановлення бібліотеки playwright...
python -m pip install -r requirements.txt

echo [2/3] Перевірка браузера Chromium...
python -m playwright install chromium

echo.
echo ====================================================
echo [3/3] Запуск моніторингу черги (~3.5 хвилини)
echo ====================================================
echo.

python check_queue.py --loop --interval 210
goto :end

:no_python
echo.
echo [ПОМИЛКА] Python не знайдено на цьому комп'ютері!
echo.
echo Що потрібно зробити:
echo 1. Зайдіть на сайт: https://www.python.org/downloads/
echo 2. Завантажте Python.
echo 3. При запуску встановлення ОБОВ'ЯЗКОВО внизу поставте галочку:
echo    [X] "Add python.exe to PATH"
echo.

:end
pause
