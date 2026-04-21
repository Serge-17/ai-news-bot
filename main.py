import os
import feedparser
import sqlite3
import time
import logging
import requests
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import google.generativeai as genai
from difflib import SequenceMatcher

# --- ФЕЙКОВЫЙ СЕРВЕР ДЛЯ HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        pass  # Заглушаем логи HTTP-сервера

def run_health_server():
    server = HTTPServer(('0.0.0.0', 7860), HealthCheckHandler)
    server.serve_forever()

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID     = os.environ.get("CHANNEL_ID")
GEMINI_TOKEN   = os.environ.get("GEMINI_TOKEN") # Меняем здесь
MODEL_ID       = "gemini-2.5-flash"             # Gemini 1.5 Flash — быстрая и дешевая (или 'gemini-1.5-pro')
DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800   

# Настройка Gemini
genai.configure(api_key=GEMINI_TOKEN)
model = genai.GenerativeModel(MODEL_ID)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
client = InferenceClient(api_key=HF_TOKEN)

# ---------------------------------------------------------------------------
# ИСТОЧНИКИ НОВОСТЕЙ
# Формат: url, tag (хештег в посте), keywords (хотя бы одно должно быть в тексте)
# ---------------------------------------------------------------------------
RSS_FEEDS = [

    # ── ОБЩИЕ НОВОСТИ ИИ ───────────────────────────────────────────────────
    {
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "model", "startup", "llm", "openai", "anthropic", "gemini"]
    },
    {
        "url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "agent", "llm", "automation", "openai", "google", "meta"]
    },
    {
        "url": "https://venturebeat.com/category/ai/feed/",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "model", "agent", "inference", "enterprise"]
    },
    {
        "url": "https://www.artificialintelligence-news.com/feed/",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "machine learning", "neural", "model"]
    },
    {
        "url": "https://openai.com/news/rss.xml",
        "tag": "#НовостиИИ",
        "keywords": ["gpt", "openai", "sora", "agents", "o1", "o3"]
    },
    {
        "url": "https://www.anthropic.com/rss.xml",
        "tag": "#НовостиИИ",
        "keywords": ["claude", "anthropic", "safety", "agent"]
    },
    {
        "url": "https://ai.googleblog.com/feeds/posts/default",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "gemini", "model", "research", "agent"]
    },
    {
        "url": "https://huggingface.co/blog/feed.xml",
        "tag": "#OpenSourceAI",
        "keywords": ["model", "dataset", "agent", "fine-tuning", "open-source", "lora"]
    },
    {
        "url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
        "tag": "#НовостиИИ",
        "keywords": ["ии", "нейросети", "ml", "llm", "агент", "модель"]
    },
    {
        "url": "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt",
        "tag": "#НовостиИИ",
        "keywords": ["ии", "нейросети", "gpt", "claude", "модель"]
    },

    # ── ГОЛОС / TTS / КЛОНИРОВАНИЕ ГОЛОСА ─────────────────────────────────
    {
        "url": "https://blogs.nvidia.com/feed/",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "tts", "audio", "cloning", "synthesis", "deepfake voice"]
    },
    {
        "url": "https://techcrunch.com/tag/voice/feed/",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "audio", "clone", "synthesis", "eleven labs", "elevenlabs"]
    },
    {
        "url": "https://elevenlabs.io/blog/rss.xml",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "audio", "clone", "tts"]
    },
    {
        "url": "https://www.deepmind.com/blog/rss.xml",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "audio", "language", "model", "gemini"]
    },

    # ── СОЦИАЛЬНЫЕ СЕТИ / CREATOR ECONOMY ─────────────────────────────────
    {
        "url": "https://socialmediatoday.com/rss.xml",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "reels", "shorts", "tiktok", "instagram", "ai content", "ugc", "influencer"]
    },
    {
        "url": "https://www.socialmediaexaminer.com/feed/",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "shorts", "reels", "ai", "automation", "content", "brand"]
    },
    {
        "url": "https://later.com/blog/feed/",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "social media", "reels", "shorts", "tiktok", "instagram", "brand deal"]
    },
    {
        "url": "https://www.tubefilter.com/feed/",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "youtube", "shorts", "monetization", "sponsor", "brand", "influencer"]
    },

    # ── AD TECH / МОНЕТИЗАЦИЯ / CPA ────────────────────────────────────────
    {
        "url": "https://adexchanger.com/feed/",
        "tag": "#AdTech",
        "keywords": ["programmatic", "cpa", "cpc", "brand", "ad", "affiliate", "performance", "ai"]
    },
    {
        "url": "https://martech.org/feed/",
        "tag": "#AdTech",
        "keywords": ["ai", "brand", "marketing", "automation", "cpa", "conversion", "affiliate"]
    },
    {
        "url": "https://digiday.com/feed/",
        "tag": "#AdTech",
        "keywords": ["brand", "ad", "creator", "influencer", "cpa", "ai", "monetization", "social"]
    },
    {
        "url": "https://www.marketingweek.com/feed/",
        "tag": "#AdTech",
        "keywords": ["brand", "ad", "creator", "influencer", "ai", "affiliate", "performance marketing"]
    },

    # ── AI-АГЕНТЫ / АВТОМАТИЗАЦИЯ ──────────────────────────────────────────
    {
        "url": "https://agentsnews.io/rss.xml",
        "tag": "#AIАгенты",
        "keywords": ["agent", "automation", "workflow", "autonomous", "agentic", "multi-agent"]
    },
    {
        "url": "https://www.llmsecurity.net/rss.xml",
        "tag": "#AIАгенты",
        "keywords": ["agent", "llm", "autonomous", "tool use", "multi-agent"]
    },
    {
        "url": "https://techcrunch.com/tag/automation/feed/",
        "tag": "#AIАгенты",
        "keywords": ["automation", "agent", "ai", "workflow", "autonomous"]
    },

    # ── СТАРТАПЫ / ИНВЕСТИЦИИ ──────────────────────────────────────────────
    {
        "url": "https://techcrunch.com/category/startups/feed/",
        "tag": "#AIСтартапы",
        "keywords": ["ai", "funding", "series", "seed", "startup", "venture", "raise"]
    },
    {
        "url": "https://sifted.eu/rss",
        "tag": "#AIСтартапы",
        "keywords": ["ai", "startup", "funding", "series", "venture", "raise", "scaleup"]
    },

    # ── БЕСПЛАТНЫЙ AI / FREEMIUM МОДЕЛИ ───────────────────────────────────
    {
        "url": "https://techcrunch.com/tag/free/feed/",
        "tag": "#БесплатныйAI",
        "keywords": ["free", "freemium", "open-source", "no-code", "affordable", "subscription", "access"]
    },
]

# ---------------------------------------------------------------------------
# РАБОТА С БД
# ---------------------------------------------------------------------------
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS published_news (
            url        TEXT PRIMARY KEY,
            title      TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def titles_are_similar(a: str, b: str, threshold: float) -> bool:
    """Проверяет схожесть двух заголовков по порогу threshold."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold


def is_duplicate(url: str, title: str, cycle_titles: list[str]) -> bool:
    """
    Проверяет, является ли статья дубликатом:
    1. По точному URL (в БД).
    2. По схожести заголовка с уже опубликованными в БД (межцикловая).
    3. По схожести заголовка с уже одобренными в ТЕКУЩЕМ цикле (кросс-источниковая).
    """
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    # 1. Точная проверка по URL
    cur.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
    if cur.fetchone():
        con.close()
        return True

    # 2. Нечёткая проверка по БД (последние 80 записей, межцикловая)
    cur.execute("SELECT title FROM published_news ORDER BY created_at DESC LIMIT 80")
    recent_db_titles = [row[0] for row in cur.fetchall()]
    con.close()

    for old_title in recent_db_titles:
        if titles_are_similar(title, old_title, SIMILARITY_DB):
            log.info(f"Дубликат (БД, {SIMILARITY_DB*100:.0f}%+): '{title[:60]}'")
            return True

    # 3. Кросс-источниковая проверка: та же история уже одобрена в этом цикле
    for cycle_title in cycle_titles:
        if titles_are_similar(title, cycle_title, SIMILARITY_CYCLE):
            log.info(f"Дубликат (цикл, {SIMILARITY_CYCLE*100:.0f}%+): '{title[:60]}' ≈ '{cycle_title[:60]}'")
            return True

    return False

def mark_published(url: str, title: str):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute(
            "INSERT OR IGNORE INTO published_news (url, title) VALUES (?, ?)",
            (url, title)
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"Ошибка записи в БД: {e}")

# ---------------------------------------------------------------------------
# AI-ОБРАБОТКА
# ---------------------------------------------------------------------------
def process_with_ai(title: str, description: str, tag: str) -> str | None:
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:2000] # У 2.0 контекст огромный
    
    prompt = (
        "Ты техно-блогер для Telegram-канала об AI и GoClaw — первой в мире "
        "рекламной AI-платформе (пользователи получают бесплатный доступ к Claude/GPT/Midjourney "
        "в обмен на то, что AI-агент публикует Stories/Shorts с их клонированным голосом; "
        "бренды платят по CPA-модели).\n\n"
        "Перескажи новость КРАТКО на русском языке:\n"
        "— Заголовок с одним релевантным эмодзи\n"
        "— 3 коротких тезиса (без нумерации, каждый с эмодзи)\n"
        "— 1 строка вывода: почему это важно для AI-рынка или для создателей контента\n"
        "Не используй жирный шрифт **. Не пиши слово 'Заголовок' или 'Вывод'.\n\n"
        f"Заголовок оригинала: {title}\n"
        f"Текст новости: {clean_desc}"
    )

    try:
        # У Gemini 2.0 Flash очень высокая скорость генерации
        response = model.generate_content(prompt)
        
        if not response or not response.text:
            return None
            
        res_text = response.text
        # Убираем жирный шрифт (Markdown **), так как TG в HTML моде может криво его принять 
        # или просто по твоему ТЗ мы его не хотим
        final_text = res_text.replace('**', '').strip()
        
        return f"{tag}\n\n{final_text}"
        
    except Exception as e:
        log.error(f"Gemini 2.0 Error: {e}")
        return None

# ---------------------------------------------------------------------------
# ОТПРАВКА В TELEGRAM
# ---------------------------------------------------------------------------
def send_telegram(text: str, url: str) -> bool:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": f"{text}\n\n🔗 <a href='{url}'>Источник</a>",
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(api_url, json=payload, timeout=20)
        if r.status_code != 200:
            log.warning(f"TG вернул {r.status_code}: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        log.error(f"TG Error: {e}")
        return False

# ---------------------------------------------------------------------------
# ОСНОВНАЯ ПРОВЕРКА
# ---------------------------------------------------------------------------
def check_news():
    log.info(f"--- НАЧАЛО ПРОВЕРКИ ({len(RSS_FEEDS)} лент) ---")
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; AINewsBot/2.0)'}

    # Заголовки, одобренные в ЭТОМ цикле — для кросс-источниковой дедупликации
    cycle_titles: list[str] = []
    total_published = 0

    for feed_item in RSS_FEEDS:
        url_feed = feed_item["url"]
        tag      = feed_item["tag"]
        keywords = feed_item.get("keywords", [])

        log.info(f"Обработка: {url_feed}")

        try:
            resp = requests.get(url_feed, headers=headers, timeout=25)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            log.error(f"Ошибка загрузки ленты {url_feed}: {e}")
            continue

        for entry in feed.entries[:MAX_PER_FEED]:
            link    = entry.get("link", "")
            title   = entry.get("title", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")

            if not link or not title:
                continue

            # ── Дедупликация: по URL, по БД и по текущему циклу ───────────
            if is_duplicate(link, title, cycle_titles):
                continue

            # ── Проверка релевантности ключевым словам ─────────────────────
            full_text = f"{title} {re.sub(r'<[^>]+>', '', summary)}".lower()
            if keywords and not any(kw.lower() in full_text for kw in keywords):
                log.info(f"Нерелевантно: {title[:60]}")
                continue

            log.info(f"📰 Новая новость: {title[:80]}")

            # ── AI-обработка ───────────────────────────────────────────────
            ai_text = process_with_ai(title, summary, tag)
            if not ai_text:
                continue

            # ── Отправка ───────────────────────────────────────────────────
            if send_telegram(ai_text, link):
                mark_published(link, title)
                cycle_titles.append(title)   # Регистрируем в памяти цикла
                total_published += 1
                log.info(f"✅ Опубликовано: {title[:60]}")
                time.sleep(12)
            else:
                log.warning(f"❌ Сбой отправки: {title[:60]}")

    log.info(f"--- ПРОВЕРКА ЗАВЕРШЕНА | Опубликовано: {total_published} ---")

# ---------------------------------------------------------------------------
# ТОЧКА ВХОДА
# ---------------------------------------------------------------------------
def main():
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    log.info("🚀 GoClaw AI News Bot v2 запущен!")

    while True:
        try:
            check_news()
        except Exception as e:
            log.critical(f"Критическая ошибка в основном цикле: {e}")

        log.info(f"⏳ Следующая проверка через {CHECK_INTERVAL // 60} минут...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
