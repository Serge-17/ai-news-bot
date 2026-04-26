import os
import time
import logging
import sqlite3
import threading
import socket
import requests
import feedparser
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from html import escape
from difflib import SequenceMatcher
import urllib3.util.connection as urllib3_cn

# ─── ЛОГИРОВАНИЕ ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL_ID      = os.environ.get("CHANNEL_ID", "")
GEMINI_TOKEN    = os.environ.get("GEMINI_TOKEN", "")
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "3600"))   # 1 час
MAX_PER_FEED    = int(os.environ.get("MAX_PER_FEED", "2"))
PORT            = int(os.environ.get("PORT", "7860"))
DB_FILE         = "/tmp/news.db"

# Принудительно IPv4 — HF Spaces зависает на IPv6
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

# ─── HEALTH-CHECK СЕРВЕР ────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

def start_health_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

# ─── RSS ФИДЫ (только самые надёжные) ──────────────────────────
FEEDS = [
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tag": "#AI"},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",  "tag": "#AI"},
    {"url": "https://venturebeat.com/category/ai/feed/",                          "tag": "#AI"},
    {"url": "https://openai.com/news/rss.xml",                                    "tag": "#OpenAI"},
    {"url": "https://www.anthropic.com/rss.xml",                                  "tag": "#Anthropic"},
    {"url": "https://huggingface.co/blog/feed.xml",                               "tag": "#OpenSource"},
    {"url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/",           "tag": "#AIРу"},
]

# ─── БАЗА ДАННЫХ ────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS news (
            url TEXT PRIMARY KEY,
            title TEXT,
            ts INTEGER DEFAULT (strftime('%s','now'))
        )
    """)
    con.commit(); con.close()
    log.info("БД готова")

def is_seen(url: str, title: str) -> bool:
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM news WHERE url=?", (url,))
    if cur.fetchone():
        con.close(); return True
    cur.execute("SELECT title FROM news ORDER BY ts DESC LIMIT 50")
    old = [r[0] for r in cur.fetchall()]
    con.close()
    return any(SequenceMatcher(None, title.lower(), o.lower()).ratio() > 0.75 for o in old)

def mark_seen(url: str, title: str):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("INSERT OR IGNORE INTO news (url, title) VALUES (?,?)", (url, title))
        con.commit(); con.close()
    except Exception as e:
        log.error(f"DB error: {e}")

# ─── GEMINI (с жёстким rate-limit) ─────────────────────────────
_gemini_blocked_until = 0.0
_gemini_calls = 0
_gemini_window_start = time.time()
GEMINI_MAX_PER_HOUR = 10   # консервативно

def ask_gemini(title: str, desc: str, tag: str) -> str | None:
    global _gemini_blocked_until, _gemini_calls, _gemini_window_start
    if not GEMINI_TOKEN:
        return None
    if time.time() < _gemini_blocked_until:
        return None

    # Сброс счётчика раз в час
    if time.time() - _gemini_window_start > 3600:
        _gemini_calls = 0
        _gemini_window_start = time.time()
    if _gemini_calls >= GEMINI_MAX_PER_HOUR:
        log.warning("Gemini: лимит на час исчерпан, fallback")
        return None

    clean = re.sub(r"<[^>]+>", " ", desc or "")[:1500]
    prompt = (
        "Ты редактор Telegram-канала об AI. Перескажи новость на русском кратко:\n"
        "— Строка 1: заголовок с эмодзи\n"
        "— Строки 2-4: три тезиса с эмодзи\n"
        "— Строка 5: почему важно\n"
        "Не используй **жирный** markdown.\n\n"
        f"Заголовок: {title}\nТекст: {clean}"
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_TOKEN}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30
        )
        _gemini_calls += 1
        if r.status_code == 429:
            _gemini_blocked_until = time.time() + 3600
            log.warning("Gemini 429 — пауза 1 час")
            return None
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return f"{tag}\n\n{text.strip()}"
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return None

# ─── FALLBACK ПОСТ (без AI) ─────────────────────────────────────
def make_fallback(title: str, desc: str, tag: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", desc or "")
    clean = re.sub(r"\s+", " ", clean).strip()[:300]
    return f"{tag}\n\n<b>{escape(title)}</b>\n\n{escape(clean)}"

# ─── TELEGRAM ───────────────────────────────────────────────────
def send_telegram(text: str, url: str) -> bool:
    TG_API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org")
    api = f"{TG_API_BASE}/bot{TELEGRAM_TOKEN}/sendMessage"
    body = {
        "chat_id": CHANNEL_ID,
        "text": f"{text[:3800]}\n\n🔗 <a href='{escape(url)}'>Источник</a>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(3):
        try:
            r = requests.post(api, json=body, timeout=(15, 60))
            if r.status_code == 200:
                return True
            log.error(f"Telegram {r.status_code}: {r.text[:200]}")
            # Если parse_mode не прошёл — пробуем plain text
            if r.status_code == 400:
                body.pop("parse_mode", None)
                body["text"] = f"{text[:3800]}\n\nИсточник: {url}"
        except requests.Timeout:
            log.warning(f"Telegram timeout (попытка {attempt+1})")
            time.sleep(5)
        except Exception as e:
            log.error(f"Telegram error: {e}")
    return False

# ─── ОСНОВНОЙ ЦИКЛ ──────────────────────────────────────────────
def check_feeds():
    log.info("=== Запуск проверки фидов ===")
    sent = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    for feed in FEEDS:
        try:
            resp = requests.get(feed["url"], headers=headers, timeout=20)
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:MAX_PER_FEED]:
                url   = entry.get("link", "")
                title = entry.get("title", "").strip()
                desc  = entry.get("summary", "")

                if not url or not title:
                    continue
                if is_seen(url, title):
                    continue

                text = ask_gemini(title, desc, feed["tag"]) \
                       or make_fallback(title, desc, feed["tag"])

                if send_telegram(text, url):
                    mark_seen(url, title)
                    sent += 1
                    log.info(f"✅ Отправлено: {title[:60]}")
                    time.sleep(8)   # пауза между постами
                else:
                    log.error(f"❌ Не отправлено: {title[:60]}")

        except Exception as e:
            log.error(f"Фид {feed['url']}: {e}")

    log.info(f"=== Готово. Отправлено: {sent} новостей ===")

def main():
    if not TELEGRAM_TOKEN or not CHANNEL_ID:
        raise ValueError("Нужны TELEGRAM_TOKEN и CHANNEL_ID")

    init_db()
    threading.Thread(target=start_health_server, daemon=True).start()
    log.info(f"🚀 Бот запущен! Health-check на порту {PORT}")

    while True:
        try:
            check_feeds()
        except Exception as e:
            log.error(f"Ошибка цикла: {e}")
        log.info(f"⏳ Следующая проверка через {CHECK_INTERVAL//60} мин")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
