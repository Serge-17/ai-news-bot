# ⚽ xG Master Bot

> **AI-powered Telegram-бот для анализа футбольной статистики xG, поиска value-ставок и управления банкроллом**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-21.5-blue)](https://python-telegram-bot.org)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?logo=railway)](https://railway.app)
[![AI](https://img.shields.io/badge/AI-Gemini%202.5%20Flash-4285F4?logo=google&logoColor=white)](https://ai.google.dev)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 📋 Оглавление

- [О проекте](#-о-проекте)
- [Возможности](#-возможности)
- [Архитектура](#-архитектура)
- [Стек технологий](#-стек-технологий)
- [Быстрый старт](#-быстрый-старт)
- [Переменные окружения](#-переменные-окружения)
- [Деплой](#-деплой)
- [Структура проекта](#-структура-проекта)
- [Как это работает](#-как-это-работает)
- [Ограничения](#️-ограничения)
- [Дисклеймер](#-дисклеймер)

---

## 🎯 О проекте

**xG Master Bot** — это Telegram-бот, который автоматически собирает футбольную статистику xG (ожидаемые голы) из открытых источников, вычисляет справедливые вероятности исходов через модель Пуассона, находит **value-ставки** (где букмекер недооценивает событие) и публикует аналитические посты в Telegram-канал. Встроенный AI (Google Gemini 2.5 Flash) генерирует человекочитаемые обоснования и распознаёт фото купонов для автоматического учёта ставок.

Весь стек работает **бесплатно** в рамках free-tier лимитов используемых сервисов.

---

## ✨ Возможности

### 📡 Сбор данных (автоматически каждые 3 часа)
- Матчи и расписание из **football-data.org** (10 топ-лиг)
- xG, удары, статистика атак — парсинг **understat.com**
- Форма команд, H2H — парсинг **fbref.com / sofascore.com**
- Коэффициенты букмекеров (1X2, Total, BTTS) — **the-odds-api.com**
- Кэш на 1–6 часов (SQLite) для экономии API-лимитов

### 🧮 Аналитический движок
- **Модель Пуассона** на среднем xG для расчёта честных вероятностей
- Поиск **value-ставок**: edge > 5% при коэффициентах 1.5–3.5
- **Дробный критерий Келли** (¼ Kelly) для расчёта размера ставки
  - Защита банкролла: min 1% / max 5% от банка за ставку

### 🤖 AI-слой (Gemini 2.5 Flash, 1500 req/день бесплатно)
- Генерация структурированных постов в канал с обоснованием ставки
- **OCR купонов**: пользователь присылает фото → Gemini распознаёт все поля и автоматически записывает ставку
- Backup: DeepSeek Chat через OpenRouter (бесплатно)

### 💰 Управление банкроллом
| Команда | Описание |
|---------|----------|
| `/bank` | Текущий баланс + история транзакций |
| `/deposit 10000` | Пополнить банкролл |
| `/withdraw 5000` | Зафиксировать снятие |

### 📊 Трекер ставок
- Кнопка **«💰 Принять ставку»** в посте канала — мгновенная запись
- Привязка результата через фото купона (OCR) или вручную
- Автоматический пересчёт баланса при win/loss

### 📈 Статистика (`/stats`)
- Периоды: 7 дней / 30 дней / всё время
- WinRate %, ROI %, Profit в рублях
- График кривой банкролла (matplotlib → PNG)
- Разбивка по лигам: лучшая и худшая

---

## 🏗 Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                     Telegram Channel                    │
│         Аналитические посты + inline-кнопки             │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   xG Master Bot                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │ scanner  │  │ analysis │  │  channel │  │  bot   │  │
│  │  .py     │  │  .py     │  │  .py     │  │  .py   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘  │
│       │              │             │             │       │
│  ┌────▼──────────────▼─────────────▼─────────────▼────┐ │
│  │              scheduler.py  +  db.py                │ │
│  └────────────────────────────────────────────────────┘ │
│  ┌─────────────────────┐  ┌─────────────────────────┐   │
│  │   data_sources.py   │  │        ai.py            │   │
│  │  football-data.org  │  │  Gemini 2.5 Flash       │   │
│  │  understat.com      │  │  (посты + OCR купонов)  │   │
│  │  the-odds-api.com   │  └─────────────────────────┘   │
│  └─────────────────────┘                                │
│  ┌──────────────────────────────────────────────────┐   │
│  │         webapp.py (FastAPI, port 7860)           │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
          │                           │
   PostgreSQL (Neon)          SQLite (локально)
```

### Схема базы данных

```sql
matches        — fixture + xG + форма + H2H + коэффициенты
predictions    — value-bet расчёт (edge, Kelly, уверенность)
bets           — ставки пользователей (placed → won/lost)
bankroll_tx    — все транзакции банкролла с балансом
```

---

## 🛠 Стек технологий

| Компонент | Технология |
|-----------|-----------|
| Bot framework | python-telegram-bot 21.5 (async) |
| Web server | FastAPI 0.111 + Uvicorn |
| База данных | PostgreSQL async (SQLAlchemy 2.0 + asyncpg) |
| Fallback БД | SQLite (aiosqlite) |
| HTTP клиент | aiohttp 3.9 |
| Планировщик | APScheduler 3.10 |
| Математика | NumPy 1.26 + SciPy 1.13 (Poisson) |
| AI | Google Gemini 2.5 Flash |
| Деплой | Docker + Railway / HuggingFace Spaces |

---

## 🚀 Быстрый старт

### Локальный запуск

```bash
# 1. Клонировать репозиторий
git clone https://github.com/Serge-17/xG_Master_Bot.git
cd xG_Master_Bot

# 2. Создать виртуальное окружение
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Настроить переменные окружения
cp .env.example .env
# Заполните .env своими ключами (см. раздел ниже)

# 5. Запустить
python bot.py
```

### Docker

```bash
# Собрать образ
docker build -t xg-master-bot .

# Запустить
docker run --env-file .env xg-master-bot
```

---

## 🔑 Переменные окружения

Создайте файл `.env` в корне проекта:

```env
# ── Telegram ──────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHANNEL_ID=-100xxxxxxxxxx   # ID канала для публикаций

# ── Данные о матчах ───────────────────────────────────
FOOTBALL_DATA_API_KEY=your_key       # football-data.org (бесплатно)
ODDS_API_KEY=your_key                # the-odds-api.com (500 req/мес бесплатно)

# ── AI ────────────────────────────────────────────────
GEMINI_API_KEY=your_key              # ai.google.dev/aistudio (бесплатно)
OPENROUTER_API_KEY=your_key          # openrouter.ai (backup, опционально)

# ── База данных ───────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://user:password@host/dbname
# Если не задан — автоматически используется SQLite (бот.db)
```

### Где получить ключи

| Сервис | Ссылка | Лимит (free) |
|--------|--------|-------------|
| Telegram Bot Token | [@BotFather](https://t.me/BotFather) | — |
| football-data.org | [football-data.org](https://www.football-data.org/client/register) | 10 req/min, 10 лиг |
| The Odds API | [the-odds-api.com](https://the-odds-api.com) | 500 req/мес |
| Gemini API | [ai.google.dev](https://ai.google.dev/aistudio) | 1500 req/день |
| OpenRouter | [openrouter.ai](https://openrouter.ai) | deepseek бесплатно |
| PostgreSQL | [Neon](https://neon.tech) | 0.5 GB бесплатно |

---

## 🚢 Деплой

### Railway (рекомендуется)

Подробная инструкция: [RAILWAY_DEPLOY.md](RAILWAY_DEPLOY.md)

```bash
# Быстрый деплой через Railway CLI
railway login
railway init
railway up
```

Добавьте все переменные из `.env` в Dashboard → Variables.

### HuggingFace Spaces

Бот запускается через FastAPI на порту `7860` (требование HF Spaces).  
⚠️ Free tier засыпает через 48 ч без активности — добавьте UptimeRobot ping на `/health`.

---

## 📁 Структура проекта

```
xG_Master_Bot/
├── bot.py             # Точка входа, регистрация хендлеров Telegram
├── webapp.py          # FastAPI приложение (health-check, webhook)
├── scheduler.py       # APScheduler: скан матчей каждые 3 часа
├── scanner.py         # Сбор fixtures и запуск анализа
├── data_sources.py    # Парсеры: understat, fbref, football-data, odds
├── analysis.py        # Poisson-модель, value calculator, Kelly
├── ai.py              # Gemini API: генерация постов + OCR купонов
├── channel.py         # Форматирование и публикация в Telegram-канал
├── db.py              # SQLAlchemy модели и CRUD операции
├── config.py          # Настройки из переменных окружения
├── requirements.txt   # Python зависимости
├── Dockerfile         # Docker образ
├── RAILWAY_DEPLOY.md  # Инструкция деплоя на Railway
├── TZ_v2.md           # Техническое задание v2
└── AGENTS.md          # Описание AI-агентов
```

---

## 🔍 Как это работает

### 1. Match Scanner (автоматически, раз в 3 часа)

```
football-data.org  →  список матчей на сегодня/завтра
       ↓
understat.com      →  xG обеих команд (последние 10 матчей)
       ↓
fbref.com          →  форма W/D/L (5 матчей) + H2H (5 встреч)
       ↓
the-odds-api.com   →  коэффициенты 1X2, Total, BTTS
       ↓
БД                 →  сохранение в таблицу matches
```

### 2. Value Calculator

```python
# Пример расчёта
fair_prob_home  = poisson_win_prob(xg_home=2.1, xg_away=0.9)  # → 0.68
bookie_prob     = 1 / odds_home                                 # → 0.57 (odds=1.75)
edge            = fair_prob_home - bookie_prob                  # → 0.11 (11%)

# edge > 5% && odds в диапазоне [1.5, 3.5] → value bet ✅
stake = bank × 0.25 × (edge / (odds - 1))                       # Kelly ¼
```

### 3. Пост в канал (пример)

```
⚽ Манчестер Сити — Арсенал
🏆 Premier League | 19:30 МСК

📈 Ставка: П1 @ 1.85
💵 Рекомендую: 450₽ (3% банка)
🎯 Уверенность: 72%

Почему:
• xG Сити дома за 10 матчей: 2.3 vs 1.1 у Арсенала в гостях
• Форма Сити: ВВВНВ (4 победы из 5)
• H2H дома: 4-1-0 в пользу Сити
⚠️ Риск: травма Родри

[💰 Принять ставку]  [📊 Детали]
```

### 4. Трекинг через OCR

```
Пользователь присылает фото купона
         ↓
Gemini Vision распознаёт купон → JSON
{
  "event": "Man City – Arsenal",
  "bet_type": "П1",
  "odds": 1.85,
  "stake": 450,
  "status": "won",
  "payout": 832.50
}
         ↓
Автоматически: запись в bets + пересчёт банкролла
```

---

## ⚠️ Ограничения

- **The Odds API**: 500 req/мес ≈ 16/день → обязателен кэш с TTL 6 часов
- **Gemini free tier**: 1500 req/день — достаточно для 30 матчей + 10 OCR купонов; при росте нагрузки потребуется платный план
- **Парсинг understat/fbref**: может сломаться при редизайне сайтов → предусмотрен fallback на sofascore
- **HF Spaces free**: засыпает через 48 ч без активности

---

## 📜 Дисклеймер

> ⚠️ **18+** Данный бот предназначен исключительно для **аналитических целей**. Публикуемые материалы — это статистический анализ футбольных матчей, а не советы по ставкам. Авторы не несут ответственности за финансовые решения пользователей. Беттинг может быть незаконен в вашей юрисдикции — ознакомьтесь с местным законодательством.

---

## 🤝 Контрибьютинг

1. Fork репозитория
2. Создайте ветку: `git checkout -b feature/amazing-feature`
3. Сделайте коммит: `git commit -m 'feat: add amazing feature'`
4. Push: `git push origin feature/amazing-feature`
5. Откройте Pull Request

---

## 📄 Лицензия

MIT License — используйте свободно, упоминание автора приветствуется.

---

<div align="center">

Сделано с ⚽ и ☕ | Автор: [Serge-17](https://github.com/Serge-17)

</div>
