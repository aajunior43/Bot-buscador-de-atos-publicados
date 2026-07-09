# Monitor de Atos — O Regional Jornal (Inajá-PR)

Sistema em Python que monitora as edições do **O Regional Jornal**, baixa PDFs, extrai texto com `pdfplumber`/OCR, detecta menções e atos oficiais de **Inajá (PR)** e notifica via Telegram, e-mail, webhook ou arquivo.

Além dos trechos, o detector classifica publicações:

- `publicacao_oficial`
- `materia_jornalistica`
- `patrocinador_distribuicao`

Para publicações oficiais, extrai órgão, tipo do ato, número, data, assunto e valor. Opcionalmente a IA (OpenCode/compatível OpenAI) refina e filtra publicações de municípios vizinhos.

## Pipeline

```
scraper → downloader → ocr_processor → detector → ai_processor → notifier
                              ↓              ↓
                         database.py    settings (DB / .env)
```

Orquestração unificada em `pipeline.py` (CLI e webapp usam o mesmo fluxo).

## Instalação

```bash
cd Bot-buscador-de-atos-publicados
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
# ou: pip install -e ".[dev]"
```

Playwright (fallback se a listagem do site for JS-only):

```bash
playwright install chromium
```

## Dependências do sistema

### Ubuntu/Debian

```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-por poppler-utils
```

### Windows

1. Tesseract (UB Mannheim) com pacote de idioma **português**.
2. Poppler para Windows (pasta `bin` no PATH ou em `POPPLER_PATH`).
3. No `.env`: `TESSERACT_PATH` e `POPPLER_PATH` se não estiverem no PATH.

### Termux

```bash
pkg update
pkg install -y python tesseract tesseract-lang-por poppler
```

## Configuração

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS
```

Principais variáveis (ver `.env.example` completo):

| Variável | Descrição |
|----------|-----------|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alertas Telegram |
| `SMTP_*` | E-mail de fallback/cópia |
| `OPENCODE_API_KEY` | Refinamento IA |
| `AI_REFINE_PUBLICATIONS` | Liga/desliga IA (`true`/`false`) |
| `WEBAPP_USER` / `WEBAPP_PASSWORD` | HTTP Basic na interface |
| `APP_ENV` | `development` ou `production` |
| `REQUIRE_WEBAPP_AUTH` | Exige credenciais mesmo fora de production |
| `CHECK_INTERVAL_HOURS` | Intervalo do scheduler CLI (padrão 6h) |
| `TESSERACT_PATH` / `POPPLER_PATH` | Obrigatórios no Windows se fora do PATH |
| `INAJA_EXTRA_TERMS` | Termos extras de detecção |
| `INAJA_CEP_PREFIXES` | Prefixo CEP (padrão `87670`) |

### Segurança da interface web

- Sem `WEBAPP_USER`/`WEBAPP_PASSWORD`: interface aberta + aviso no log (**apenas local**).
- Com `APP_ENV=production` ou `REQUIRE_WEBAPP_AUTH=true`: o webapp **não sobe** sem credenciais.
- O `docker-compose.yml` de produção define `APP_ENV=production` e `REQUIRE_WEBAPP_AUTH=true`.

## Bot do Telegram (alertas)

1. Crie o bot com `@BotFather` e copie o token.
2. Envie uma mensagem ao bot.
3. `https://api.telegram.org/botSEU_TOKEN/getUpdates` → use o `chat.id`.

Sem token/chat, alertas vão para `./alertas/YYYY-MM-DD.log`.

Há também `telegram_bot.py`: bot **interativo** separado do notificador.

## Execução

### Um ciclo

```bash
python main.py --once
```

### Scheduler contínuo (CLI)

```bash
python main.py
```

### Flags úteis

```bash
python main.py --once --force-ocr
python main.py --force-rescan
python main.py --process-all
python main.py --notify-test
python main.py --once --full-structured-ocr
```

### Interface web

```bash
# desenvolvimento (porta 8001, hot-reload)
python run_interface.py

# ou
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

Acesse: **http://localhost:8001** (dev) ou a porta mapeada no Docker.

Páginas: dashboard, edições, detecções, status, exportação, **admin** (IA, SMTP, webhooks, termos).

No Windows, `iniciar.bat` sobe **interface web + rastreador** em um único terminal (`iniciar_tudo.py`).

## Docker

```bash
docker compose up -d --build
```

- Container: `bot-buscador-de-atos`
- Porta host: **8001** → 8000 no container
- Labels Traefik para HTTPS em produção
- Exige `.env` com `WEBAPP_USER` e `WEBAPP_PASSWORD`

Dentro do contêiner:

```bash
docker exec -it bot-buscador-de-atos bash
cd /workspace
python main.py --once
```

Imagem publicada (CI em push para `main`): `aajunior43/bot-buscador-de-atos:latest`.

## OCR

| Modo | Quando |
|------|--------|
| Híbrido (pdfplumber + OCR em páginas fracas) | Padrão do CLI |
| OCR rápido + estruturado em páginas candidatas | Webapp / `--force-ocr` |
| OCR estruturado completo | `--full-structured-ocr` |

Cache: `.ocr.json` ao lado do PDF. Para re-OCR, apague o cache ou use `--force-ocr`.

## Testes e qualidade de código

```bash
pytest tests/ -v
ruff check .          # se instalou extras dev
```

Os testes isolam `SETTINGS` e usam SQLite temporário (`tests/conftest.py`).

## Estrutura principal

| Arquivo | Função |
|---------|--------|
| `main.py` | CLI / scheduler |
| `pipeline.py` | Orquestrador download→OCR→detecção→notify |
| `webapp.py` | Dashboard FastAPI |
| `detector.py` | Regras de detecção e classificação |
| `ocr_processor.py` | Extração de texto / Tesseract |
| `ai_processor.py` | Refinamento LLM |
| `database.py` | SQLite, jobs, métricas, migrations |
| `notifier.py` | Telegram / e-mail / webhook / arquivo |
| `scraper.py` / `downloader.py` | Listagem e download de edições |

## Métricas de qualidade

Cada detecção grava em `deteccao_metricas` (retenção pós-filtros, descartes de município vizinho/IA, páginas com OCR fraco). O dashboard exibe o agregado quando houver dados.

## Scripts one-off

A pasta `scripts/` contém utilitários pontuais de reprocessamento/análise. Não fazem parte do fluxo normal — use apenas se souber o contexto.

Mais detalhes de arquitetura: `AGENTS.md`.
