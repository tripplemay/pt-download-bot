# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PT Download Bot — a Telegram bot that searches PT sites (NexusPHP-based) for torrents and pushes downloads to NAS clients (Download Station, qBittorrent, Transmission). Multi-user with Owner approval system. Chinese UI. Deployed on Synology NAS via Docker.

## Commands

```bash
# Run bot locally
python -m bot.main

# Tests (387 tests, 88% coverage)
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

**Auth flow via decorators**: `bot/middleware.py` provides `@require_auth` (user/owner role) and `@require_owner` decorators that wrap handler functions. They check `context.bot_data["db"]` and short-circuit with role-appropriate error messages. Both decorators are compatible with message and callback_query updates via `_reply()` helper.

**Two abstraction layers with factory pattern**:
- `bot/pt/base.py` → `PTSiteBase` (abstract) → `NexusPHPSite` (RSS search via `feedparser` + `httpx`, web search via HTML parsing as fallback)
- `bot/clients/base.py` → `DownloadClientBase` (abstract) → 3 implementations. `create_download_client(config)` factory in `bot/clients/__init__.py` selects at runtime. All clients implement `add_torrent_url()` → `Optional[str]` (returns task_id), `add_torrent_file()` → `Optional[str]`, `delete_task()` → `bool`, `get_tasks()`, `test_connection()`.

**Download Station DSM 6/7 dual API**: `bot/clients/download_station.py` auto-detects API version at first use via `_run_api_probe()`. DSM 7 uses `SYNO.DownloadStation2.Task` (v2) with `url` field and required `destination` parameter (auto-fetched from DS settings). DSM 6 falls back to `SYNO.DownloadStation.Task` (v1) with `uri` field. All API calls go through `/webapi/entry.cgi`. API profile (endpoints, field names, task list key) is cached in `_APIProfile` dataclass after first probe.

**Progressive Chinese search** (`bot/handlers/search.py`): When a Chinese keyword is detected, the bot uses `_search_web_progressive()` which searches in precision order: (1) TMDB-translated English + title search, (2) Chinese + title search, (3) Chinese + description search. Each tier only triggers if previous results < 3, with 0.5s delay between requests. Falls back to RSS if no Cookie or web search fails.

**NexusPHP HTML parser** (`bot/pt/nexusphp.py`): `_TorrentsPageParser` handles nested `<table>` inside title cells (common NexusPHP layout for title + subtitle). Tracks `_table_depth` so nested `</table>` doesn't prematurely end parsing. Also extracts seeders/leechers (pure integers after size cell) and subtitle (nested table second row). Web search results are sorted by seeders descending.

**Search results with Inline Keyboard** (`bot/handlers/search.py`): Search results show subtitle, size, seeders icon (🟢/🔴). `_build_keyboard()` adds download buttons `[1][2]...[10]` (5 per row) + `[◀ 上一页][下一页 ▶]`. `page_callback` and `dl_callback` handle button presses with auth checks and input validation.

**Search pagination & caching**: Module-level `user_cache` (per user) stores results + page state. `_search_result_cache` (keyed by normalized keyword, TTL 300s, max 200 entries) prevents duplicate PT site requests. Both caches must be cleared in test fixtures.

**Download task tracking**: `download_logs` table has `task_id` column linking to download client's task ID. `add_torrent_url`/`add_torrent_file` return `Optional[str]` (task_id or None). Callers must use `if result is not None:` (not `if result:`) because empty string `""` means success without task_id.

**User task isolation**: `/status` shows only the user's own tasks (matched via `download_logs.task_id`). Owner sees all by default, `/status mine` to filter. `bot/handlers/status.py` groups tasks by state (downloading/paused/seeding) with progress bars and ETA.

**Task deletion**: `/cancel <序号>` and ❌ buttons on `/status` allow users to delete tasks. Two-step confirmation via `cdel:` → `delok:`/`delno:` callback chain. Users can only delete their own tasks; Owner can delete any.

**Download completion notifications** (`bot/handlers/notify.py`): `check_completed_tasks()` runs every 60s via JobQueue. Compares current task statuses against a snapshot in `context.bot_data["_task_snapshot"]`. First run only takes snapshot (no notifications). Notifies task owner + Owner when a Bot-added task completes.

**PT site risk mitigation**: Browser User-Agent, Accept/Accept-Language headers. `asyncio.Semaphore(3)` limits concurrent PT site requests. `download_torrent()` validates URL domain matches `base_url` before fetching.

**Security**: All `/set*` commands that receive sensitive data immediately delete the user's message. `/settings` only shows "已配置/未配置" status. Callback handlers (`dl_callback`, `page_callback`, `delete_*_callback`) check `db.is_authorized()` and validate callback_data format. SSL verification enabled for HTTPS connections, disabled for HTTP (internal NAS). SQLite uses WAL mode + `busy_timeout=5000`. Database file permissions set to 600.

## Key Constraints

- Python 3.11+, runtime deps: `python-telegram-bot[job-queue]>=20.0`, `httpx>=0.25.0`, `feedparser>=6.0`
- Fully async (`async/await`). All HTTP via `httpx.AsyncClient` with 30s timeout.
- Telegram API uses `HTTPXRequest` with 30s connect/read/write timeouts (configured in `main.py`) to accommodate proxy/high-latency networks.
- SQLite via stdlib `sqlite3` (no ORM). Single `Database` instance, `check_same_thread=False`, WAL mode.
- Only 2 required env vars: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`. Everything else via Bot commands.
- Docker: `network_mode: host`, non-root `botuser` via `entrypoint.sh` + `gosu`, `entrypoint.sh` fixes volume permissions and tightens `bot.db` to 600.
- DB has 3 tables: `users` (role: owner/user/pending/banned), `download_logs` (with `task_id`), `settings` (KV store for all runtime config). `_migrate_tables()` adds new columns incrementally.
- Multi-arch Docker images (amd64 + arm64) built by GitHub Actions, published to `ghcr.io/tripplemay/pt-download-bot`.

## Testing Patterns

- `asyncio_mode = auto` in `pytest.ini` — async tests need no explicit marker.
- `tests/conftest.py` provides: `tmp_db` / `db_with_owner` / `db_with_users` fixtures for SQLite, plus `make_update()` and `make_context()` helpers that create mock Telegram objects.
- PT and download clients are mocked via `unittest.mock.AsyncMock`.
- `user_cache` and `_search_result_cache` are cleared between tests via `autouse` fixtures in `test_handlers.py` and `test_settings.py`.
- Settings commands import `init_*` functions from `bot.main` at call time — patch at `bot.main.init_pt_client` etc.
- `main()` tests must mock the full `ApplicationBuilder` chain including `.request()`, `.get_updates_request()` and `HTTPXRequest`.
- DS client tests use `_v1_profile()` helper to create a pre-configured `_APIProfile`, bypassing API probe.
- Callback handler tests create mock `callback_query` with `from_user.id`, `data`, `message.chat_id`, and `answer`/`edit_message_text` as `AsyncMock`.
