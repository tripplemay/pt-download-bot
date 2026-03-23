# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PT Download Bot — a Telegram bot that searches PT sites (NexusPHP-based) for torrents and pushes downloads to NAS clients (Download Station, qBittorrent, Transmission). Multi-user with Owner approval system. Chinese UI.

## Commands

```bash
# Run bot locally
python -m bot.main

# Tests (222 tests, 93% coverage)
python3 -m pytest tests/                          # all tests
python3 -m pytest tests/test_handlers.py          # single file
python3 -m pytest tests/test_core.py::TestDatabase # single class
python3 -m pytest tests/test_core.py::TestDatabase::test_init_owner_creates_owner -v  # single test
python3 -m pytest --cov=bot --cov-report=term-missing  # with coverage

# Docker
docker-compose up -d
docker-compose logs -f
```

## Architecture

**Entry point**: `bot/main.py` → `main()` loads config, initializes all components, registers handlers, starts polling.

**Shared state via `context.bot_data`**: All handlers receive a Telegram `context` object. Shared instances (Database, PT client, download client, TMDB client, owner_id, page_size) are stored in `context.bot_data["key"]` — set once in `main()`, read everywhere. This is the primary dependency injection mechanism.

**Auth flow via decorators**: `bot/middleware.py` provides `@require_auth` (user/owner role) and `@require_owner` decorators that wrap handler functions. They check `context.bot_data["db"]` and short-circuit with role-appropriate error messages. Applied directly on handler functions in `bot/handlers/`.

**Two abstraction layers with factory pattern**:
- `bot/pt/base.py` → `PTSiteBase` (abstract) → `NexusPHPSite` (RSS search via `feedparser` + `httpx`, web search via HTML parsing as fallback)
- `bot/clients/base.py` → `DownloadClientBase` (abstract) → 3 implementations. `create_download_client(config)` factory in `bot/clients/__init__.py` selects at runtime.

**Chinese search optimization** (`bot/handlers/search.py`): RSS only matches English titles. When a Chinese keyword is detected (`_contains_chinese`), the bot: (1) translates via TMDB API (`bot/tmdb.py`) to get the English name, (2) searches RSS with the English name, (3) if results < 3, falls back to web scraping `torrents.php` with `search_area=1` (subtitle search). Web search requires `PT_COOKIE` env var; TMDB requires `TMDB_API_KEY`. Both are optional — without them, only RSS search is used.

**Search pagination**: `bot/handlers/search.py` uses a module-level `user_cache: dict` keyed by `user_id` to store search results and page state between `/search` and `/more` commands. `bot/handlers/download.py` imports and reads from the same cache for `/dl`.

**User approval flow**: `/apply` → creates pending DB record → sends Owner an `InlineKeyboardMarkup` with approve/reject buttons → `CallbackQueryHandler` in `bot/handlers/start.py::approval_callback` processes the response.

## Key Constraints

- Python 3.11+, only 3 runtime deps: `python-telegram-bot>=20.0`, `httpx>=0.25.0`, `feedparser>=6.0`
- Fully async (`async/await`). All HTTP via `httpx.AsyncClient` with 30s timeout.
- SQLite via stdlib `sqlite3` (no ORM). Single `Database` instance, `check_same_thread=False`.
- Config exclusively from environment variables (see `.env.example`). Required: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `PT_SITE_URL`, `PT_PASSKEY`. Optional: `TMDB_API_KEY` (Chinese translation), `PT_COOKIE` (web search fallback).
- Docker deployment with `network_mode: host` (direct access to NAS services on LAN).

## Testing Patterns

- `asyncio_mode = auto` in `pytest.ini` — async tests need no explicit marker.
- `tests/conftest.py` provides: `tmp_db` / `db_with_owner` / `db_with_users` fixtures for SQLite, plus `make_update()` and `make_context()` helpers that create mock Telegram objects.
- PT and download clients are mocked via `unittest.mock.AsyncMock`.
- `user_cache` is cleared between tests via an `autouse` fixture in `test_handlers.py`.
