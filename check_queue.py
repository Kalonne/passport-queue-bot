import asyncio
import argparse
import os
import sys
import time
import random
import urllib.request
import urllib.parse
from datetime import datetime

# Автоматичне встановлення playwright, якщо бібліотека ще не встановлена
try:
    from playwright.async_api import async_playwright
except ModuleNotFoundError:
    print("[INFO] Бібліотеку 'playwright' не знайдено. Автоматично встановлюю...", flush=True)
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "playwright>=1.40.0"], check=True)
    from playwright.async_api import async_playwright

# Запобігання засинанню Windows (екран може згаснути, але система та інтернет не заснуть)
if os.name == 'nt':
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass

URL = "https://munich.pasport.org.ua/solutions/e-queue"
BUSY_TEXT = "Вибачте, на даний момент всі місця зайняті!"
SERVICE_TEXT = "Закордонний паспорт та (або) ID-картка"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8956789904:AAFRFy1WlV3gzs4aj8lpq7gSFnBtQjN7si8")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "838131789")

def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def send_telegram(text: str, photo_path: str = None):
    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            body = bytearray()
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{CHAT_ID}\r\n".encode())
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{text}\r\n".encode())
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"slot.png\"\r\nContent-Type: image/png\r\n\r\n".encode())
            with open(photo_path, "rb") as f:
                body.extend(f.read())
            body.extend(f"\r\n--{boundary}--\r\n".encode())

            req = urllib.request.Request(url, data=body)
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            with urllib.request.urlopen(req, timeout=20) as resp:
                log(f"Telegram notification with photo sent successfully (status {resp.status})")
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=20) as resp:
                log(f"Telegram message sent successfully (status {resp.status})")
    except Exception as e:
        log(f"Error sending Telegram notification: {e}")

async def check_slots(save_screenshot: bool = True) -> bool:
    """
    Returns True if free slots were detected, False otherwise.
    """
    async with async_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--disable-extensions',
                '--disable-dev-shm-usage',
            ]
        }
        chrome_win = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if os.name == 'nt' and os.path.exists(chrome_win):
            launch_kwargs["executable_path"] = chrome_win

        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
                log("Chromium browser not found, automatically downloading (one-time setup)...")
                import subprocess
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                if "executable_path" in launch_kwargs:
                    del launch_kwargs["executable_path"]
                browser = await p.chromium.launch(**launch_kwargs)
            else:
                raise e

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            locale="uk-UA",
            viewport={"width": 1920, "height": 1080}
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['uk-UA', 'uk', 'en-US', 'en'] });
        """)

        page = await context.new_page()

        days_data = None
        rate_limited = False

        async def on_response(res):
            nonlocal days_data, rate_limited
            if res.status == 429:
                rate_limited = True
                log("⚠️ Отримано HTTP 429 (Too many requests) від сервера.")
            elif res.url.endswith("/solutions/e-queue") and res.request.method == "POST":
                try:
                    text = await res.text()
                    if "Too many requests" in text:
                        rate_limited = True
                        log("⚠️ Сервер відхилив запит: 'Too many requests'.")
                    else:
                        data = await res.json()
                        if isinstance(data, dict) and "days" in data:
                            days_data = data["days"]
                            log(f"AJAX response 'days': {days_data}")
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            log(f"Navigating to {URL}...")
            resp = await page.goto(URL, wait_until="domcontentloaded", timeout=45000)

            # Перевірка на Rate Limiting при відкритті сторінки
            if resp and resp.status == 429:
                log("⚠️ Сервер повернув 429 Too many requests при завантаженні сторінки. Спробуємо в наступному циклі.")
                return False

            content = await page.content()
            if "Too many requests" in content:
                log("⚠️ Сайт тимчасово обмежив запити (Too many requests). Спробуємо в наступному циклі.")
                return False

            # Wait up to 15s for Cloudflare or page render
            for _ in range(15):
                title = await page.title()
                if "Just a moment" in title or "Cloudflare" in title:
                    log(f"Cloudflare challenge detected ('{title}'). Waiting...")
                    try:
                        frames = page.frames
                        for f in frames:
                            if "challenges.cloudflare.com" in f.url:
                                checkbox = await f.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                                if checkbox:
                                    log("Found Cloudflare turnstile checkbox, clicking...")
                                    await checkbox.click()
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                else:
                    break

            service_select = None
            try:
                await page.wait_for_selector("select#service", timeout=12000)
                service_select = await page.query_selector("select#service")
            except Exception:
                pass

            title = await page.title()
            log(f"Current page title: '{title}'")

            if not service_select:
                log("⚠️ select#service not found on page!")
                await page.screenshot(path="debug_screen.png")
                return False

            # Очікуємо, поки Alpine.js прив'яжеться до форми (щоб запит не зависав зі спінером)
            try:
                await page.wait_for_function(
                    '() => { const f = document.querySelector("#services"); return !!(f && f._x_dataStack && f._x_dataStack[0]); }',
                    timeout=8000
                )
            except Exception:
                pass

            log(f"Selecting service: '{SERVICE_TEXT}'...")
            try:
                await page.select_option("select#service", label=SERVICE_TEXT)
            except Exception:
                await page.select_option("select#service", value="4")

            # Переконуємось, що Alpine getDays спрацював
            try:
                await page.evaluate('''() => {
                    const f = document.querySelector("#services");
                    if (f && f._x_dataStack && f._x_dataStack[0]) {
                        const alp = f._x_dataStack[0];
                        if (alp.service !== "4") alp.service = "4";
                        if (typeof alp.getDays === "function") alp.getDays();
                    }
                }''')
            except Exception:
                pass

            log("Waiting for server response and calendar slots...")

            is_busy = False
            slots_available = False
            available_dates = []

            for _ in range(20):
                if rate_limited:
                    log("⚠️ Запит потрапив під обмеження частоти (Rate Limit). Пропускаємо спробу.")
                    return False

                # 1. Перевірка прямої відповіді API сервера
                if days_data is not None:
                    if len(days_data) == 0:
                        is_busy = True
                        break
                    else:
                        slots_available = True
                        available_dates = days_data
                        break

                # 2. Перевірка тексту повідомлення на самій сторінці
                content = await page.content()
                if BUSY_TEXT in content:
                    is_busy = True
                    break

                # 3. Перевірка наявності доступних дат у випадаючому списку дат
                date_options = await page.query_selector_all("select#date option")
                valid_dates = [await opt.get_attribute("value") for opt in date_options if await opt.get_attribute("value")]
                if len(valid_dates) > 0:
                    slots_available = True
                    available_dates = valid_dates
                    break

                await asyncio.sleep(1)

            screenshot_path = "slot_alert.png"
            if save_screenshot:
                await page.screenshot(path=screenshot_path)

            if is_busy:
                log("ℹ️ All slots are currently busy (Вибачте, на даний момент всі місця зайняті).")
                return False
            elif slots_available:
                log("🚨 FREE SLOTS CONFIRMED!")
                msg = (
                    "🎉 <b>УВАГА! З'ЯВИЛИСЯ ВІЛЬНІ МІСЦЯ В Е-ЧЕРЗІ!</b>\n\n"
                    "📍 <b>Мюнхен (Heinrich-Wieland-Straße 5)</b>\n"
                    f"Послуга: <b>{SERVICE_TEXT}</b>\n\n"
                    f"🔗 Швидше переходьте для запису: {URL}"
                )
                if available_dates:
                    msg += f"\n📅 <b>Доступні дати:</b> {available_dates}"
                send_telegram(msg, screenshot_path if save_screenshot else None)
                return True
            else:
                log("⚠️ Сервер не встиг надіслати дати або тимчасово перевантажений. Пропускаємо, щоб не надсилати хибну тривогу.")
                return False

        except Exception as e:
            log(f"Error during check: {e}")
            await page.screenshot(path="debug_screen.png")
            return False
        finally:
            await browser.close()

async def main():
    parser = argparse.ArgumentParser(description="Passport Service Queue Monitor")
    parser.add_argument("--loop", action="store_true", help="Run continuously in a loop")
    parser.add_argument("--interval", type=int, default=210, help="Check interval in seconds when in loop mode (default: 210 = 3.5 min)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding='utf-8')
    log("Starting Passport Queue Bot...")

    if not args.loop:
        await check_slots()
    else:
        log(f"Continuous loop mode active. Base interval: {args.interval}s (~{args.interval // 60} min).")
        last_notified = 0
        while True:
            try:
                slots = await check_slots()
                if slots:
                    now = time.time()
                    if now - last_notified > 600:
                        last_notified = now
            except KeyboardInterrupt:
                log("Bot stopped by user.")
                break
            except Exception as e:
                log(f"Unexpected error in loop: {e}")

            # Додаємо невеликий випадковий джитер (10-30 сек), щоб сайт не блокував через чіткий таймер робота
            jitter = random.randint(10, 30)
            sleep_time = args.interval + jitter
            log(f"Очікування {sleep_time} сек до наступної перевірки...")
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(main())
