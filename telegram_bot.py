"""Bot Telegram interativo para o Monitor de Atos de Inajá.

Execução:
    python telegram_bot.py

Comandos disponíveis:
    /start      — boas-vindas e lista de comandos
    /status     — situação atual do sistema
    /ultima     — última publicação detectada
    /edicoes    — últimas 5 edições processadas
    /alertas    — últimas 5 notificações enviadas
    /teste      — envia mensagem de teste
    /ajuda      — lista todos os comandos
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Garante que o diretório do projeto está no path
sys.path.insert(0, str(Path(__file__).parent))

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import database
from config import SETTINGS
from detector import DetectionResult
from notifier import montar_mensagem, _escape_mdv2
from scraper import Edicao

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(texto: str | None, fallback: str = "—") -> str:
    return _escape_mdv2(texto or fallback)


def _agora() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------------
# Handlers de comandos
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "🤖 *Monitor de Atos de Inajá\\-PR*\n\n"
        "Olá\\! Sou o bot de monitoramento de publicações oficiais de Inajá no Diário Oficial\\.\n\n"
        "📋 *Comandos disponíveis:*\n"
        "/status — situação atual do sistema\n"
        "/ultima — última publicação detectada\n"
        "/edicoes — últimas edições processadas\n"
        "/alertas — últimas notificações enviadas\n"
        "/teste — envia mensagem de teste\n"
        "/ajuda — lista completa de comandos"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra o status geral do sistema."""
    try:
        with database.connect() as conn:
            cur = conn.cursor()

            # Total de edições
            cur.execute("SELECT COUNT(*) FROM edicoes")
            total_edicoes = cur.fetchone()[0]

            # Edições com OCR processado
            cur.execute("SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 1")
            ocr_ok = cur.fetchone()[0]

            # Edições com Inajá detectada
            cur.execute("SELECT COUNT(*) FROM edicoes WHERE tem_inaja = 1")
            com_inaja = cur.fetchone()[0]

            # Última edição processada
            cur.execute(
                "SELECT titulo, data_publicacao, processado_em FROM edicoes "
                "WHERE ocr_processado = 1 ORDER BY processado_em DESC LIMIT 1"
            )
            ultima = cur.fetchone()

            # Total de publicações classificadas
            cur.execute("SELECT COUNT(*) FROM publicacoes")
            total_pub = cur.fetchone()[0]

            # Última notificação
            cur.execute(
                "SELECT canal, enviado_em FROM notificacoes ORDER BY enviado_em DESC LIMIT 1"
            )
            ultima_notif = cur.fetchone()

        linhas = [
            "📊 *Status do Sistema*",
            f"🕐 Atualizado em: {_fmt(_agora())}",
            "",
            f"📰 Edições catalogadas: *{total_edicoes}*",
            f"✅ Com OCR processado: *{ocr_ok}*",
            f"🏛️ Com publicações de Inajá: *{com_inaja}*",
            f"📌 Publicações classificadas: *{total_pub}*",
            "",
        ]
        if ultima:
            linhas += [
                "📄 *Última edição processada:*",
                f"  Título: {_fmt(ultima[0])}",
                f"  Data: {_fmt(ultima[1])}",
                f"  Processada em: {_fmt(str(ultima[2])[:16] if ultima[2] else None)}",
                "",
            ]
        if ultima_notif:
            linhas += [
                f"🔔 Última notificação: canal *{_fmt(ultima_notif[0])}*",
                f"  Enviada em: {_fmt(str(ultima_notif[1])[:16] if ultima_notif[1] else None)}",
            ]
        else:
            linhas.append("🔔 Nenhuma notificação enviada ainda\\.")

        await update.message.reply_text("\n".join(linhas), parse_mode="MarkdownV2")

    except Exception as exc:
        logger.exception("Erro no /status")
        await update.message.reply_text(f"❌ Erro ao buscar status: {_escape_mdv2(str(exc))}", parse_mode="MarkdownV2")


async def cmd_ultima(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra a última publicação oficial de Inajá detectada."""
    try:
        with database.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT e.titulo, e.data_publicacao, e.url,
                       p.categoria, p.orgao, p.tipo, p.numero,
                       p.data_documento, p.valor, p.assunto, p.resumo_ia,
                       p.pagina
                FROM publicacoes p
                JOIN edicoes e ON e.id = p.edicao_id
                ORDER BY p.id DESC LIMIT 1
            """)
            row = cur.fetchone()

        if not row:
            await update.message.reply_text("ℹ️ Nenhuma publicação de Inajá encontrada ainda\\.", parse_mode="MarkdownV2")
            return

        titulo, data_pub, url, categoria, orgao, tipo, numero, data_doc, valor, assunto, resumo_ia, pagina = row

        partes = [categoria or "publicação", orgao, tipo, numero]
        titulo_pub = " \\- ".join(_escape_mdv2(str(p)) for p in partes if p)

        linhas = [
            "🏛️ *Última publicação de Inajá*",
            "",
            f"📰 Edição: {_fmt(titulo)}",
            f"📅 Data: {_fmt(data_pub)}",
            f"📄 Página: {_fmt(str(pagina))}",
            f"🏷️ Publicação: {titulo_pub}",
        ]
        if data_doc:
            linhas.append(f"📆 Data do documento: {_fmt(data_doc)}")
        if valor:
            linhas.append(f"💰 Valor: {_fmt(valor)}")
        if assunto:
            linhas.append(f"📝 Assunto: {_fmt(assunto[:300])}")
        if resumo_ia:
            linhas.append(f"🤖 Resumo IA: {_fmt(resumo_ia[:300])}")
        if url:
            url_esc = url.replace(")", "\\)")
            linhas.extend(["", f"🔗 [Abrir edição]({url_esc})"])

        await update.message.reply_text("\n".join(linhas), parse_mode="MarkdownV2")

    except Exception as exc:
        logger.exception("Erro no /ultima")
        await update.message.reply_text(f"❌ Erro: {_escape_mdv2(str(exc))}", parse_mode="MarkdownV2")


async def cmd_edicoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista as últimas 5 edições processadas."""
    try:
        with database.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT titulo, data_publicacao, tem_inaja, ocr_processado, url
                FROM edicoes
                ORDER BY id DESC LIMIT 5
            """)
            rows = cur.fetchall()

        if not rows:
            await update.message.reply_text("ℹ️ Nenhuma edição encontrada ainda\\.", parse_mode="MarkdownV2")
            return

        linhas = ["📚 *Últimas 5 edições:*", ""]
        for titulo, data_pub, tem_inaja, ocr_ok, url in rows:
            icone = "🏛️" if tem_inaja else ("✅" if ocr_ok else "⏳")
            linhas.append(f"{icone} {_fmt(titulo)} \\({_fmt(data_pub)}\\)")

        await update.message.reply_text("\n".join(linhas), parse_mode="MarkdownV2")

    except Exception as exc:
        logger.exception("Erro no /edicoes")
        await update.message.reply_text(f"❌ Erro: {_escape_mdv2(str(exc))}", parse_mode="MarkdownV2")


async def cmd_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista as últimas 5 notificações enviadas."""
    try:
        rows = database.get_notificacoes(limit=5)

        if not rows:
            await update.message.reply_text("ℹ️ Nenhuma notificação enviada ainda\\.", parse_mode="MarkdownV2")
            return

        linhas = ["🔔 *Últimas 5 notificações:*", ""]
        for row in rows:
            canal = row["canal"] if "canal" in row.keys() else "?"
            sucesso = row["sucesso"] if "sucesso" in row.keys() else False
            enviado_em = row["enviado_em"] if "enviado_em" in row.keys() else ""
            icone = "✅" if sucesso else "❌"
            data_str = str(enviado_em)[:16] if enviado_em else "?"
            linhas.append(f"{icone} Canal: *{_fmt(canal)}* — {_fmt(data_str)}")

        await update.message.reply_text("\n".join(linhas), parse_mode="MarkdownV2")

    except Exception as exc:
        logger.exception("Erro no /alertas")
        await update.message.reply_text(f"❌ Erro: {_escape_mdv2(str(exc))}", parse_mode="MarkdownV2")


async def cmd_teste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia uma mensagem de teste para confirmar funcionamento."""
    resultado = DetectionResult(
        encontrado=True,
        edicao_id=0,
        edicao_titulo="Mensagem de teste",
        paginas_com_mencao=[1],
        trechos=[{"pagina": 1, "trecho": "Teste de alerta do monitor de Inajá-PR."}],
        termos_encontrados=["Inajá"],
        mencoes_db=[],
        publicacoes=[],
    )
    edicao = Edicao(
        url=SETTINGS.site_url,
        titulo="Edição de teste",
        data_publicacao=datetime.now().date().isoformat(),
    )
    msg = montar_mensagem(resultado, edicao)
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_desconhecido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde a mensagens que não são comandos reconhecidos."""
    await update.message.reply_text(
        "🤔 Não entendi\\. Use /ajuda para ver os comandos disponíveis\\.",
        parse_mode="MarkdownV2",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

import asyncio

import requests
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest


def _telegram_api_alcancavel(token: str, tentativas: int = 3) -> bool:
    """Testa GET getMe com requests (mais estável que o bootstrap do PTB em rede lenta)."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    for n in range(1, tentativas + 1):
        try:
            resp = requests.get(url, timeout=20)
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            logger.warning(
                "Telegram getMe HTTP %s (tentativa %s/%s): %s",
                resp.status_code,
                n,
                tentativas,
                resp.text[:200],
            )
        except requests.RequestException as exc:
            logger.warning(
                "Telegram indisponível (tentativa %s/%s): %s",
                n,
                tentativas,
                exc,
            )
        time.sleep(min(3 * n, 10))
    return False


def main() -> None:
    token = SETTINGS.telegram_bot_token
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN não configurado no .env")
        sys.exit(1)

    logger.info("Iniciando bot Telegram com polling...")

    # Se a API não responde, sai limpo (código 0) — não quebra o launcher nem
    # gera RuntimeError de event loop em retries do run_polling.
    if not _telegram_api_alcancavel(token):
        logger.error(
            "Não foi possível conectar em api.telegram.org (timeout/firewall/DNS). "
            "Bot interativo desligado. WEB e rastreador continuam. "
            "Alertas automáticos usam o notifier com TELEGRAM_CHAT_ID, se configurado."
        )
        sys.exit(0)

    # Timeouts maiores — redes com latência alta (comum no Windows + proxy)
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    # Loop limpo (Python 3.12+/3.14)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajuda", cmd_ajuda))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ultima", cmd_ultima))
    app.add_handler(CommandHandler("edicoes", cmd_edicoes))
    app.add_handler(CommandHandler("alertas", cmd_alertas))
    app.add_handler(CommandHandler("teste", cmd_teste))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_desconhecido))

    try:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=True,
        )
    except (TimedOut, NetworkError, OSError) as exc:
        logger.error(
            "Bot Telegram encerrou por rede: %s. "
            "WEB e BOT seguem; reinicie o bat quando a internet com o Telegram estabilizar.",
            exc,
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
