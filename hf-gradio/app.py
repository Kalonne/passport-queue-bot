import os
import sys
import time
import asyncio
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime
from collections import deque
import gradio as gr
from playwright.async_api import async_playwright

# Автоматичне встановлення Chromium у середовищі Hugging Face
try:
    print("Installing Playwright Chromium...", flush=True)
    subprocess.run(["playwright", "install", "chromium"], check=True)
    print("Chromium installed successfully.", flush=True)
except Exception as e:
    print(f"Playwright installation note: {e}", flush=True)

URL = "https://munich.pasport.org.ua/solutions/e-queue"
BUSY_TEXT = "Вибачте, на даний момент всі місця зайняті!"
SERVICE_TEXT = "Закордонний паспорт та (або) ID-картка"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8956789904:AAFRFy1WlV3gzs4aj8lpq7gSFnBtQjN7si8")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "838131789")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

state = {
    "status": "Запуск фонового процесу...",
    "last_check_time": "Ще не перевірялось",
    "checks_count": 0,
    "last_result": "Очікування першої перевірки",
    "is_busy": True,
    "available_days": []
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
    state["status"] = "Виконується перевірка черги..."
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
                log("⚠️ select#service not found on page! Possible Cloudflare challenge.")
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
                state["status"] = "Активний (місць немає)"
                state["last_result"] = "Всі місця зайняті"
                log("ℹ️ All slots are currently busy.")
                return False

        except Exception as e:
            log(f"Error during check: {e}")
            state["last_result"] = f"Помилка: {e}"
            state["status"] = "Помилка при перевірці"
            return False
        finally:
            await browser.close()

def worker_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def loop_runner():
        log(f"Worker thread started. Interval: {CHECK_INTERVAL}s")
        last_notified = 0
        await asyncio.sleep(4)
        while True:
            try:
                found = await perform_check()
                if found:
                    now = time.time()
                    if now - last_notified > 600:
                        last_notified = now
            except Exception as e:
                log(f"Worker exception: {e}")
            state["status"] = f"Очікування наступної перевірки ({CHECK_INTERVAL} сек)..."
            await asyncio.sleep(CHECK_INTERVAL)

    loop.run_until_complete(loop_runner())

# Запуск фонового потоку перевірки
t = threading.Thread(target=worker_thread, daemon=True)
t.start()

# Функції для інтерфейсу Gradio
def get_ui_values():
    logs_text = "\n".join(reversed(log_buffer))
    return state["status"], state["last_check_time"], f"{state['checks_count']}", state["last_result"], logs_text

def manual_check():
    # Запускаємо перевірку синхронно з кнопки
    loop = asyncio.new_event_loop()
    loop.run_until_complete(perform_check())
    return get_ui_values()

def test_telegram_btn():
    send_telegram("🔔 <b>Тест:</b> Бот на Hugging Face (Gradio) успішно підключений до вашого Telegram!")
    return get_ui_values()

# Побудова інтерфейсу Gradio
with gr.Blocks(title="Паспортний сервіс Мюнхен") as demo:
    gr.Markdown("# 🛂 Моніторинг черги ДП «Документ» (Мюнхен)")
    gr.Markdown("Цей бот цілодобово перевіряє наявність вільних місць на послугу **«Закордонний паспорт та (або) ID-картка»**.")

    with gr.Row():
        status_box = gr.Textbox(label="Статус бота", value=state["status"], interactive=False)
        last_check_box = gr.Textbox(label="Час останньої перевірки", value=state["last_check_time"], interactive=False)
        count_box = gr.Textbox(label="Всього перевірок", value="0", interactive=False)
        result_box = gr.Textbox(label="Результат", value=state["last_result"], interactive=False)

    with gr.Row():
        btn_refresh = gr.Button("🔄 Оновити статус", variant="secondary")
        btn_check = gr.Button("⚡ Перевірити зараз", variant="primary")
        btn_test = gr.Button("💬 Надіслати тест у Telegram")

    logs_box = gr.TextArea(label="Системні логи (останні звернення)", value="", lines=12, interactive=False)

    btn_refresh.click(fn=get_ui_values, outputs=[status_box, last_check_box, count_box, result_box, logs_box])
    btn_check.click(fn=manual_check, outputs=[status_box, last_check_box, count_box, result_box, logs_box])
    btn_test.click(fn=test_telegram_btn, outputs=[status_box, last_check_box, count_box, result_box, logs_box])

    # Автоматичне оновлення інтерфейсу кожні 10 секунд
    try:
        timer = gr.Timer(10)
        timer.tick(fn=get_ui_values, outputs=[status_box, last_check_box, count_box, result_box, logs_box])
    except Exception:
        pass

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
