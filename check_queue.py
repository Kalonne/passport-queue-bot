import asyncio
import argparse
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
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
        async def on_response(res):
            nonlocal days_data
            if res.url.endswith("/solutions/e-queue") and res.request.method == "POST":
                try:
                    data = await res.json()
                    if isinstance(data, dict) and "days" in data:
                        days_data = data["days"]
                        log(f"AJAX response 'days': {days_data}")
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            log(f"Navigating to {URL}...")
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)

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
                content = await page.content()
                if "challenges.cloudflare.com" in content or "Just a moment" in content:
                    log("❌ Blocked by Cloudflare interactive challenge on GitHub runner IP.")
                return False

            log(f"Selecting service: '{SERVICE_TEXT}'...")
            try:
                await page.select_option("select#service", label=SERVICE_TEXT)
            except Exception:
                await page.select_option("select#service", value="4")

            log("Waiting for calendar slots response...")
            await asyncio.sleep(5)

            content = await page.content()
            screenshot_path = "slot_alert.png"
            if save_screenshot:
                await page.screenshot(path=screenshot_path)

            has_busy_text = BUSY_TEXT in content
            slots_available = not has_busy_text or (days_data is not None and len(days_data) > 0)

            if slots_available:
                log("🚨 FREE SLOTS DETECTED!")
                msg = (
                    "🎉 <b>УВАГА! З'ЯВИЛИСЯ ВІЛЬНІ МІСЦЯ В Е-ЧЕРЗІ!</b>\n\n"
                    "📍 <b>Мюнхен (Heinrich-Wieland-Straße 5)</b>\n"
                    f"Послуга: <b>{SERVICE_TEXT}</b>\n\n"
                    f"🔗 Швидше переходьте для запису: {URL}"
                )
                if days_data and len(days_data) > 0:
                    msg += f"\n📅 <b>Доступні дати:</b> {days_data}"
                send_telegram(msg, screenshot_path if save_screenshot else None)
                return True
            else:
                log("ℹ️ All slots are currently busy (Вибачте, на даний момент всі місця зайняті).")
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
    parser.add_argument("--interval", type=int, default=180, help="Check interval in seconds when in loop mode (default: 180)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding='utf-8')
    log("Starting Passport Queue Bot...")

    if not args.loop:
        await check_slots()
    else:
        log(f"Continuous loop mode active. Checking every {args.interval} seconds ({args.interval // 60} min).")
        last_notified = 0
        while True:
            try:
                slots = await check_slots()
                if slots:
                    now = time.time()
                    if now - last_notified > 600:
                        last_notified = now
                await asyncio.sleep(args.interval)
            except KeyboardInterrupt:
                log("Bot stopped by user.")
                break
            except Exception as e:
                log(f"Unexpected error in loop: {e}")
                await asyncio.sleep(args.interval)

if __name__ == "__main__":
    asyncio.run(main())
