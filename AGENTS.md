# AGENTS.md — Bot Buscador de Atos Publicados

## What it does

Monitors "O Regional Jornal" PDF editions, runs OCR + text analysis to detect official publications from Inajá-PR, and notifies via Telegram/Email/Webhook/File.

## Pipeline (in order)

```
scraper.py → downloader.py → ocr_processor.py → detector.py → ai_processor.py → notifier.py
                                                      ↑                ↑
                                                 database.py      database.py (reads SETTINGS from DB)
```

## Entrypoints

| File | Purpose |
|------|---------|
| `main.py` | CLI scheduler / one-shot runner. Run with `--once`, `--force-rescan`, `--process-all`, `--notify-test`, `--force-ocr`, `--full-structured-ocr` |
| `webapp.py` | FastAPI dashboard (Jinja2 templates). Port 8000 internal, 8001 external via docker-compose |
| `run_interface.py` | Dev launcher for webapp on port 8001 with hot-reload. Uses a filtered logging formatter ignoring noise like `/api/atividade` |
| `telegram_bot.py` | Standalone interactive Telegram bot (not the notifier — this is a separate interactive session) |

## Dev commands

```bash
# Run full test suite
pytest tests/ -v

# Run single test class
pytest tests/test_detector.py::TestExtrairOrgao -v

# Run one-shot processing
python main.py --once

# Run web UI for development
python run_interface.py

# Run interactive Telegram bot
python telegram_bot.py
```

No linter or formatter config found (no `ruff`, `black`, `isort` config). No `pyproject.toml`. No pre-commit hooks.

## Architecture notes

### OCR strategy (ocr_processor.py)

Three extraction modes:
1. **pdfplumber** — extracts embedded text. Used by default. Pages with < 100 chars (`MIN_TEXT_CHARS_PER_PAGE`) get OCR fallback.
2. **OCR híbrido** — pdfplumber first, then Tesseract only on low-text pages.
3. **OCR forçado** — Tesseract on every page (via `--force-ocr` or `FORCE_OCR=true`).
4. **OCR rápido + estruturado** — Fast low-DPI Tesseract on all pages, then full structured OCR only on pages that mention Inajá-like terms. This is the default in `extrair_texto_rapido_com_estruturado_candidato()`.

OCR results are cached as `.ocr.json` next to the PDF. To force re-OCR, delete the `.ocr.json` file or use `--force-ocr`.

Tesseract column detection is **automatic** (image projection analysis via `_detectar_faixas_colunas`). The `OCR_LAYOUT_COLUMNS` env var is informational only.

### Windows setup requirements

- **Tesseract**: Needs `TESSERACT_PATH` in `.env` pointing to `tesseract.exe` if not in PATH.
- **Poppler** (for pdf2image): Needs `POPPLER_PATH` in `.env` pointing to the `bin` folder.

### AI refinement (ai_processor.py)

Calls OpenCode Go API (OpenAI-compatible) to extract structured fields (`orgao`, `tipo`, `numero`, `data_documento`, `valor`, `assunto`, `resumo`). Disabled when:
- `OPENCODE_API_KEY` is empty, or
- `AI_REFINE_PUBLICATIONS=false`

The API URL, model, timeout, and max_tokens are configurable via env vars. Uses `ThreadPoolExecutor` with 4 workers.

Key filtering logic: the AI can discard publications that belong to neighboring cities (not Inajá). The detector also has a hard-coded list of `MUNICIPIOS_VIZINHOS` (config.py:130-150) for pre-filtering.

### Notifications (notifier.py)

Fallback chain: **Telegram → Email → File** (writes to `./alertas/YYYY-MM-DD.log`).

Telegram uses **MarkdownV2** escaping (special chars in `_MDV2_SPECIAL`). Messages are truncated at 4096 chars. Retries up to 2 times with 3s delay.

Webhooks are stored in the `webhooks` DB table (configurable via `/admin` UI). Dispatched asynchronously in daemon threads.

### Database (database.py)

- SQLite with **WAL mode** (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`).
- Tables: `edicoes`, `mencoes`, `publicacoes`, `jobs`, `notificacoes`, `webhooks`, `settings`, `schema_migrations`.
- Versioned migrations in `_MIGRATIONS` list (5 migrations so far).
- The `settings` table doubles as runtime config storage (overrides `.env` values for AI key, SMTP, webhooks, terms).

### Web interface (webapp.py)

- FastAPI with Jinja2 templates (`templates/`) and static files (`static/`).
- HTTP Basic Auth (optional) — enabled when `WEBAPP_USER` and `WEBAPP_PASSWORD` are set. Skips auth for `/static/` paths.
- Server-Sent Events at `/api/eventos` for real-time dashboard refresh.
- Admin page at `/admin` for managing AI settings, SMTP, webhooks, detection terms.
- On startup, reprocesses any editions with stuck "rodando" jobs (from previous crash).

### Scheduler

Uses the `schedule` library (not cron). Default: runs every 6 hours (`CHECK_INTERVAL_HOURS`). The webapp has its own 4x/day detection loop in a daemon thread.

## Testing

- `pytest` with the **autouse `mock_settings` fixture** in `tests/conftest.py` that replaces `config.SETTINGS` with temp directories and an in-memory-like SQLite DB. All tests are isolated.
- The fixture re-imports `database` with a reload to pick up the mocked SETTINGS.
- Tests that use the DB need the `db` fixture (which calls `database.init_db()`).
- Tests for `detector.detectar()` need the `db` fixture too.
- No integration tests requiring external services (site, Telegram, etc.).

## Configuration

Everything comes from `.env` via `config.py` → `Settings` frozen dataclass. Key env vars beyond the README:

| Var | Default | Note |
|-----|---------|------|
| `OPENCODE_API_KEY` | `""` | AI refinement |
| `OPENCODE_API_URL` | OpenCode Go endpoint | Configurable API base |
| `OPENCODE_MODEL` | `deepseek-v4-flash` | |
| `AI_REFINE_PUBLICATIONS` | `true` | Toggle AI post-processing |
| `WEBAPP_USER` / `WEBAPP_PASSWORD` | `""` | HTTP Basic Auth for web |
| `POPPLER_PATH` / `TESSERACT_PATH` | `""` | Required on Windows |
| `MAX_EDICOES_POR_CICLO` | `10` | Limit first-run batch size |
| `NOTIFY_EMAIL_ALWAYS` | `false` | Send email even if Telegram succeeds |

## Docker

- `Dockerfile`: Python 3.11-bookworm with poppler-utils and tesseract-ocr-por.
- `docker-compose.yml`: Mounts the project at `/workspace`, maps 8001:8000, includes Traefik labels for production.
- GitHub Actions: push to `main` triggers a Docker Hub build/push to `aajunior43/bot-buscador-de-atos:latest`.

## Utility scripts

`scripts/` directory contains one-off analysis/reprocessing scripts (not needed for normal operation). `analise_db.py` clears PDFs/TXTs from processed editions to free disk space.

## Git conventions

- Single `main` branch. No feature branches in remote history.
- Commit messages are in Brazilian Portuguese.
- No conventional commit format — messages are descriptive but inconsistent.

## What NOT to do

- Do NOT commit `.env` files (`.gitignore` already blocks it).
- Do NOT modify `analise_db.py` or scripts in `scripts/` unless specifically asked — they are one-off tools.
- Do NOT edit `.ocr.json` cache files directly — delete them to force re-OCR.
- Do NOT change the `__pycache__/` or `edicoes/` generated content — they are in `.gitignore`.
