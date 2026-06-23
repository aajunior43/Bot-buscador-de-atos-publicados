# Monitor Automático de Edições - O Regional Jornal

Sistema em Python para monitorar edições do jornal, baixar PDFs, extrair texto com `pdfplumber`/OCR e detectar menções a Inajá (PR).

Além dos trechos, o detector classifica publicações por bloco OCR:

- `publicacao_oficial`
- `materia_jornalistica`
- `patrocinador_distribuicao`

Para publicações oficiais, tenta extrair órgão, tipo do ato, número, data, assunto e valor.

## Instalação

```bash
cd /root/novo-projeto
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Se usar Playwright como fallback para páginas renderizadas por JavaScript:

```bash
playwright install chromium
```

## Dependências do Sistema

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-por poppler-utils
```

Windows:

1. Instale o Tesseract pelo instalador UB Mannheim.
2. Marque o pacote de idioma português na instalação.
3. Instale o Poppler para Windows e adicione a pasta `bin` ao `PATH`.
4. Reinicie o terminal antes de executar o projeto.

Termux:

```bash
pkg update
pkg install -y python tesseract tesseract-lang-por poppler
```

## Configuração

Edite o arquivo `.env`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
CHECK_INTERVAL_HOURS=6
OCR_LANGUAGE=por
INAJA_EXTRA_TERMS=João Eder Aguilar,Luana Aiara,Amarildo Peres
INAJA_CEP_PREFIXES=87670
INAJA_IGNORE_CONTEXT_TERMS=distribuição avulsa,auto posto,panificadora,farmácia,loterias,patrocinadores,anunciante,anunciantes
DOWNLOAD_DIR=./edicoes
LOG_DIR=./logs
DB_PATH=./jornal_monitor.db
```

## Bot do Telegram

1. Abra o Telegram e converse com `@BotFather`.
2. Use `/newbot`, escolha nome e usuário do bot.
3. Copie o token para `TELEGRAM_BOT_TOKEN`.
4. Envie uma mensagem qualquer para o bot criado.
5. Acesse `https://api.telegram.org/botSEU_TOKEN/getUpdates`.
6. Copie o `chat.id` retornado para `TELEGRAM_CHAT_ID`.

Sem token ou chat configurado, os alertas são gravados em `./alertas/YYYY-MM-DD.log`.

## Execução

Rodar com scheduler:

```bash
python main.py
```

Rodar uma única vez:

```bash
python main.py --once
```

Forçar OCR visual em todas as páginas:

```bash
python main.py --once --force-ocr
```

Parâmetros úteis no `.env`:

```env
OCR_DPI=200
OCR_TIMEOUT_SECONDS=120
OCR_LAYOUT_COLUMNS=3
```

Forçar reprocessamento:

```bash
python main.py --force-rescan
```

Processar todos os PDFs locais registrados:

```bash
python main.py --process-all
```

Testar notificação:

```bash
python main.py --notify-test
```

Rodar interface web:

```bash
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

Acesse no host:

```text
http://localhost:8001
```

## Docker

```bash
docker compose up -d
docker exec -it novo-projeto-dev bash
```

Dentro do contêiner:

```bash
cd /workspace
pip install -r requirements.txt
python main.py --once
```

## Estrutura

```text
jornal-monitor/
├── main.py
├── scraper.py
├── downloader.py
├── ocr_processor.py
├── detector.py
├── notifier.py
├── database.py
├── config.py
├── .env
├── requirements.txt
└── README.md
```
