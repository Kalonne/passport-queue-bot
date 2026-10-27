import asyncio
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from playwright.async_api import async_playwright

URL = "https://munich.pasport.org.ua/solutions/e-queue"
BUSY_TEXT = "Вибачте, на даний момент всі місця зайняті!"
SERVICE_TEXT = "Закордонний паспорт та (або) ID-картка"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8956789904:AAFRFy1WlV3gzs4aj8lpq7gSFnBtQjN7si8")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "838131789")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

state = {
    "status": "Ініціалізація...",
    "last_check_time": "Ще не перевірялось",
    "checks_count": 0,
    "last_result": "Очікування першого запуску",
    "is_busy": True,
    "available_days": [],
    "last_error": None
}

log_buffer = deque(maxlen=60)

def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{now}] {msg}"
    print(entry, flush=True)
    log_buffer.append(entry)

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

async def perform_check() -> bool:
    state["status"] = "Виконується перевірка..."
    state["last_check_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["checks_count"] += 1

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
                log("⚠️ select#service not found on page! Possible Cloudflare check.")
                state["last_result"] = "Помилка: форма не знайдена (можливо Cloudflare)"
                state["status"] = "Очікування..."
                return False

            log(f"Selecting service: '{SERVICE_TEXT}'...")
            try:
                await page.select_option("select#service", label=SERVICE_TEXT)
            except Exception:
                await page.select_option("select#service", value="4")

            await asyncio.sleep(5)
            content = await page.content()

            screenshot_path = "slot_alert.png"
            await page.screenshot(path=screenshot_path)

            has_busy_text = BUSY_TEXT in content
            slots_available = not has_busy_text or (days_data is not None and len(days_data) > 0)

            state["is_busy"] = not slots_available
            state["available_days"] = days_data or []

            if slots_available:
                state["status"] = "🎉 ЗНАЙДЕНО ВІЛЬНІ СЛОТИ!"
                state["last_result"] = f"З'ЯВИЛИСЯ МІСЦЯ! Доступні дати: {days_data}"
                log("🚨 FREE SLOTS DETECTED!")
                msg = (
                    "🎉 <b>УВАГА! З'ЯВИЛИСЯ ВІЛЬНІ МІСЦЯ В Е-ЧЕРЗІ!</b>\n\n"
                    "📍 <b>Мюнхен (Heinrich-Wieland-Straße 5)</b>\n"
                    f"Послуга: <b>{SERVICE_TEXT}</b>\n\n"
                    f"🔗 Швидше переходьте для запису: {URL}"
                )
                if days_data and len(days_data) > 0:
                    msg += f"\n📅 <b>Доступні дати:</b> {days_data}"
                send_telegram(msg, screenshot_path)
                return True
            else:
                state["status"] = "Працює (місць немає)"
                state["last_result"] = "Всі місця зайняті"
                log("ℹ️ All slots are currently busy.")
                return False

        except Exception as e:
            log(f"Error during check: {e}")
            state["last_error"] = str(e)
            state["last_result"] = f"Помилка: {e}"
            state["status"] = "Помилка при перевірці"
            return False
        finally:
            await browser.close()

async def background_worker():
    log(f"Starting background queue monitor (Interval: {CHECK_INTERVAL}s)...")
    last_notified = 0
    # Wait 3 seconds on startup before first check
    await asyncio.sleep(3)
    while True:
        try:
            found = await perform_check()
            if found:
                now = time.time()
                if now - last_notified > 600:
                    last_notified = now
        except Exception as e:
            log(f"Worker loop exception: {e}")
        state["status"] = f"Очікування наступної перевірки ({CHECK_INTERVAL} сек)..."
        await asyncio.sleep(CHECK_INTERVAL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(background_worker())
    yield
    worker_task.cancel()

app = FastAPI(title="Passport Queue Monitor", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    status_color = "emerald" if not state["is_busy"] else ("blue" if "Виконується" in state["status"] else "gray")
    badge = "🟢 Вільні місця знайдено!" if not state["is_busy"] else "🟡 Моніторинг активний (місць поки немає)"

    logs_html = "".join([f"<div class='py-1 font-mono text-xs text-gray-300'>{line}</div>" for line in reversed(log_buffer)])

    html = f"""
    <!DOCTYPE html>
    <html lang="uk">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Паспортний сервіс Мюнхен — Монітор черги</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8">
        <div class="max-w-4xl mx-auto space-y-6">
            <header class="flex flex-col md:flex-row md:items-center justify-between pb-6 border-b border-slate-800 gap-4">
                <div>
                    <h1 class="text-2xl font-bold text-white flex items-center gap-2">
                        🛂 Паспортний сервіс (м. Мюнхен)
                    </h1>
                    <p class="text-slate-400 text-sm mt-1">Автоматичний моніторинг черги на ID-картку та закордонний паспорт</p>
                </div>
                <div>
                    <span class="inline-flex items-center px-3 py-1.5 rounded-full text-xs font-semibold bg-{status_color}-500/20 text-{status_color}-400 border border-{status_color}-500/30">
                        {badge}
                    </span>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-slate-800/80 p-5 rounded-xl border border-slate-700/60">
                    <div class="text-xs font-medium text-slate-400 uppercase tracking-wider">Статус бота</div>
                    <div class="text-lg font-bold text-white mt-2">{state['status']}</div>
                </div>
                <div class="bg-slate-800/80 p-5 rounded-xl border border-slate-700/60">
                    <div class="text-xs font-medium text-slate-400 uppercase tracking-wider">Остання перевірка</div>
                    <div class="text-lg font-bold text-white mt-2">{state['last_check_time']}</div>
                    <div class="text-xs text-slate-400 mt-1">Всього перевірок: {state['checks_count']}</div>
                </div>
                <div class="bg-slate-800/80 p-5 rounded-xl border border-slate-700/60">
                    <div class="text-xs font-medium text-slate-400 uppercase tracking-wider">Останній результат</div>
                    <div class="text-sm font-semibold text-slate-200 mt-2 break-words">{state['last_result']}</div>
                </div>
            </div>

            <div class="flex flex-wrap gap-3">
                <form action="/check-now" method="post">
                    <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition">
                        ⚡ Перевірити зараз
                    </button>
                </form>
                <form action="/test-telegram" method="post">
                    <button type="submit" class="bg-slate-700 hover:bg-slate-600 text-white font-medium px-4 py-2 rounded-lg text-sm transition">
                        💬 Надіслати тест у Telegram
                    </button>
                </form>
                <a href="{URL}" target="_blank" class="bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium px-4 py-2 rounded-lg text-sm border border-slate-700 transition flex items-center gap-1">
                    🔗 Відкрити сайт черги ↗
                </a>
            </div>

            <div class="bg-slate-950 p-5 rounded-xl border border-slate-800">
                <div class="flex items-center justify-between pb-3 border-b border-slate-800">
                    <h2 class="text-sm font-semibold text-slate-300 uppercase tracking-wider">Останні системні логи (оновлюються при оновленні сторінки)</h2>
                    <span class="text-xs text-slate-500">Інтервал: {CHECK_INTERVAL} сек</span>
                </div>
                <div class="mt-4 max-h-72 overflow-y-auto space-y-1">
                    {logs_html if logs_html else '<div class="text-sm text-slate-500">Логів поки немає...</div>'}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.post("/check-now")
async def trigger_check(background_tasks: BackgroundTasks):
    background_tasks.add_task(perform_check)
    return RedirectResponse(url="/", status_code=303)

@app.post("/test-telegram")
async def trigger_test():
    send_telegram("🔔 <b>Тестове сповіщення:</b> Бот на Hugging Face Spaces активний та перевіряє чергу!")
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    sys.stdout.reconfigure(encoding='utf-8')
    uvicorn.run(app, host="0.0.0.0", port=7860)
