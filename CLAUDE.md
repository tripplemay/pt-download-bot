# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PT Download Bot — a Telegram bot that searches PT sites (NexusPHP-based) for torrents and pushes downloads to NAS clients (Download Station, qBittorrent, Transmission). Multi-user with Owner approval system. Chinese UI. Deployed on Synology NAS via Docker.

## Commands

```bash
# Run bot locally
python -m bot.main

# Tests (284 tests)
python3 -m pytest tests/                          # all tests
python3 -m pytest tests/test_handlers.py          # single file
python3 -m pytest tests/test_core.py::TestDatabase # single class
python3 -m pytest tests/test_core.py::TestDatabase::test_init_owner_creates_owner -v  # single test
python3 -m pytest --cov=bot --cov-report=term-missing  # with coverage

# Docker
docker compose up -d                              # production (pre-built image)
docker compose -f docker-compose.build.yml up -d  # local build
docker compose logs -f
```

## Architecture

**Entry point**: `bot/main.py` → `main()` loads config, initializes components, registers handlers, starts polling.

**Two-tier config**: Only `TELEGRAM_BOT_TOKEN` and `OWNER_TELEGRAM_ID` are required env vars. All other config (PT site, download client, TMDB) is stored in the SQLite `settings` table and managed via Bot commands (`/setsite`, `/setpasskey`, `/setds`, `/setqb`, `/settr`, `/settmdb`, `/setcookie`). On first boot, `_migrate_env_to_db()` syncs any legacy `.env` values into the database (without overwriting existing DB values).

**Graceful degradation**: `pt_client`, `dl_client`, and `tmdb_client` in `context.bot_data` may be `None` if not yet configured. All handlers that use these clients check for `None` and return a user-friendly "not configured" message. Commands in `bot/handlers/settings.py` dynamically reinitialize clients after saving config — no Bot restart needed.

**Setup wizard**: When Owner sends `/start` and `setup_completed` is not `"true"` in settings, the bot displays a step-by-step guide. Each `/set*` command checks if all required config is complete (`_check_setup_complete`) and auto-marks `setup_completed = true` when ready.

**Shared state via `context.bot_data`**: All handlers receive a Telegram `context` object. Shared instances (Database, PT client, download client, TMDB client, owner_id, page_size) are stored in `context.bot_data["key"]` — set in `main()`, updated by `/set*` commands, read everywhere.

**Auth flow via decorators**: `bot/middleware.py` provides `@require_auth` (user/owner role) and `@require_owner` decorators that wrap handler functions. They check `context.bot_data["db"]` and short-circuit with role-appropriate error messages.

**Two abstraction layers with factory pattern**:
- `bot/pt/base.py` → `PTSiteBase` (abstract) → `NexusPHPSite` (RSS search via `feedparser` + `httpx`, web search via HTML parsing as fallback)
- `bot/clients/base.py` → `DownloadClientBase` (abstract) → 3 implementations. `create_download_client(config)` factory in `bot/clients/__init__.py` selects at runtime.

**Download Station DSM 6/7 dual API**: `bot/clients/download_station.py` auto-detects API version at first use via `_detect_api_version()`. DSM 7 uses `SYNO.DownloadStation2.Task` (v2) with `url` field and required `destination` parameter (auto-fetched from DS settings). DSM 6 falls back to `SYNO.DownloadStation.Task` (v1) with `uri` field. All API calls go through `/webapi/entry.cgi`.

**Progressive Chinese search** (`bot/handlers/search.py`): When a Chinese keyword is detected, the bot uses `_search_web_progressive()` which searches in precision order: (1) TMDB-translated English + title search, (2) Chinese + title search, (3) Chinese + description search. Each tier only triggers if previous results < 3. Falls back to RSS if no Cookie or web search fails.

**NexusPHP HTML parser** (`bot/pt/nexusphp.py`): `_TorrentsPageParser` handles nested `<table>` inside title cells (common NexusPHP layout for title + subtitle). Tracks `_table_depth` so nested `</table>` doesn't prematurely end parsing. Only processes direct-child `<tr>` of the torrents table as data rows.

**Search pagination**: `bot/handlers/search.py` uses a module-level `user_cache: dict` keyed by `user_id` to store search results and page state between `/search` and `/more` commands. `bot/handlers/download.py` imports and reads from the same cache for `/dl`.

**Download two-tier fallback**: `/dl` first tries `add_torrent_url` (passing URL to client). If that fails, downloads the `.torrent` file via `pt_client.download_torrent()` then uses `add_torrent_file` (uploading bytes). Non-Owner downloads trigger a notification to Owner.

**Security**: All `/set*` commands that receive sensitive data (passkey, password, cookie, API key) immediately delete the user's message via `_delete_user_message()`. `/settings` only shows "已配置/未配置" status, never actual values.

## Key Constraints

- Python 3.11+, only 3 runtime deps: `python-telegram-bot>=20.0`, `httpx>=0.25.0`, `feedparser>=6.0`
- Fully async (`async/await`). All HTTP via `httpx.AsyncClient` with 30s timeout.
- Telegram API uses `HTTPXRequest` with 30s connect/read/write timeouts (configured in `main.py`) to accommodate proxy/high-latency networks.
- SQLite via stdlib `sqlite3` (no ORM). Single `Database` instance, `check_same_thread=False`.
- Only 2 required env vars: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`. Everything else via Bot commands.
- Docker: `network_mode: host`, non-root `botuser` via `entrypoint.sh` + `gosu`, `entrypoint.sh` fixes volume permissions on startup.
- DB has 3 tables: `users` (role: owner/user/pending/banned), `download_logs`, `settings` (KV store for all runtime config).
- Multi-arch Docker images (amd64 + arm64) built by GitHub Actions, published to `ghcr.io/tripplemay/pt-download-bot`.

## Testing Patterns

- `asyncio_mode = auto` in `pytest.ini` — async tests need no explicit marker.
- `tests/conftest.py` provides: `tmp_db` / `db_with_owner` / `db_with_users` fixtures for SQLite, plus `make_update()` and `make_context()` helpers that create mock Telegram objects.
- PT and download clients are mocked via `unittest.mock.AsyncMock`.
- `user_cache` is cleared between tests via an `autouse` fixture in `test_handlers.py`.
- Settings commands import `init_*` functions from `bot.main` at call time — patch at `bot.main.init_pt_client` etc.
- `main()` tests must mock the full `ApplicationBuilder` chain including `.request()`, `.get_updates_request()` and `HTTPXRequest`.
