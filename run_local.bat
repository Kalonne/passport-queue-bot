@echo off
chcp 65001 > nul
echo ====================================================
echo Паспортний сервіс (Мюнхен) — Автоматичний моніторинг
echo ====================================================

:: Перевірка чи встановлений Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ПОМИЛКА] Python не знайдено на цьому комп'ютері!
    echo Будь ласка, встановіть Python з сайту python.org (обов'язково поставте галочку "Add Python to PATH").
    pause
    exit /b
)

:: Перевірка та автоматичне встановлення бібліотек
echo [1/2] Перевірка бібліотеки Playwright...
python -c "import playwright" >nul 2>&1
if %errorlevel% neq 0 (
    echo Встановлюю бібліотеки з requirements.txt (це займе близько 30 секунд)...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    echo Бібліотеку Playwright вже встановлено.
)

:: Перевірка та завантаження браузера Chromium (якщо ще не завантажений)
echo [2/2] Перевірка браузера для перевірки сайту...
python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); p.chromium.launch(headless=True).close(); p.stop()" >nul 2>&1
if %errorlevel% neq 0 (
    echo Завантажую браузер Chromium для Playwright (один раз)...
    playwright install chromium
) else (
    echo Браузер готовий до роботи.
)

echo.
echo ====================================================
echo Запуск моніторингу черги (інтервал: кожні 3 хвилини)
echo Щоб зупинити, просто закрийте це вікно.
echo ====================================================
echo.

python check_queue.py --loop --interval 180
pause
