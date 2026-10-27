import asyncio
import argparse
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright

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
                '--window-size=1920,1080'
            ]
        }
        # In case running on local Windows machine without standalone playwright browser installed
        chrome_win = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if os.name == 'nt' and os.path.exists(chrome_win):
            launch_kwargs["executable_path"] = chrome_win

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            locale="uk-UA",
            viewport={"width": 1920, "height": 1080}
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
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
            await asyncio.sleep(4)

            service_select = await page.query_selector("select#service")
            if not service_select:
                log("⚠️ select#service not found on page! Possible Cloudflare block or site layout update.")
                content = await page.content()
                if "challenges.cloudflare.com" in content or "Just a moment..." in content:
                    log("Cloudflare challenge screen encountered.")
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

            # Slots exist if either the busy text is ABSENT or days_data contains available items
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
            return False
        finally:
            await browser.close()

async def main():
    parser = argparse.ArgumentParser(description="Passport Service Queue Monitor")
    parser.add_argument("--loop", action="store_true", help="Run continuously in a loop")
    parser.add_argument("--interval", type=int, default=60, help="Check interval in seconds when in loop mode (default: 60)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding='utf-8')
    log("Starting Passport Queue Bot...")

    if not args.loop:
        # Single run (perfect for GitHub Actions cron)
        await check_slots()
    else:
        # Continuous daemon mode (for local machine or VPS)
        log(f"Continuous loop mode active. Checking every {args.interval} seconds.")
        last_notified = 0
        while True:
            try:
                slots = await check_slots()
                if slots:
                    now = time.time()
                    # Throttle repeat alerts (once every 10 minutes if still open)
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
