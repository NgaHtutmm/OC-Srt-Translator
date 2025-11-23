# Translate Bot (Telegram) — Docker-ready

This project is a Telegram bot that:
- Translates `.str`, subtitle files (`.srt`, `.vtt`, `.ass`) and other supported files.
- Accepts single files or ZIP uploads, auto-extracts, translates all supported files, repacks ZIP.
- Provides a Language selection menu and Mode selection (Normal / Adult-Safe) via inline buttons.
- Docker-ready and easy to deploy to a VPS.

## Quick start (Docker)

1. Copy `.env.example` to `.env` and fill in your keys:
```
TELEGRAM_BOT_TOKEN=your_telegram_token
OPENAI_API_KEY=your_openai_key
```

2. Build and run with Docker:
```
docker build -t translate-bot .
docker run -d --name translate-bot --env-file .env -p 8080:8080 translate-bot
```

Or with docker-compose:
```
docker-compose up -d --build
```

## Usage
- Send a single `.srt` / `.str` or upload a ZIP containing supported files.
- Choose a target language from the inline menu.
- Choose translation mode: Normal or Adult-Safe.
- The bot will return translated files or a translated ZIP.

Supported languages: en, my, ja, th, ko, zh (extendable).

## Project structure
```
translate-bot/
├── bot.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```
