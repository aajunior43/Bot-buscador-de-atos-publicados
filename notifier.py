from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from telegram import Bot

import database
from config import SETTINGS
from detector import DetectionResult
from scraper import Edicao


logger = logging.getLogger(__name__)


def _resumir(trecho: str, limite: int = 150) -> str:
    texto = " ".join(trecho.split())
    return texto if len(texto) <= limite else texto[: limite - 3] + "..."


def montar_mensagem(resultado: DetectionResult, edicao: Edicao) -> str:
    linhas = [
        "🗞️ *Nova publicação sobre Inajá detectada!*",
        "",
        f"📰 Edição: {edicao.titulo}",
        f"📅 Data: {edicao.data_publicacao or 'não informada'}",
        f"📄 Páginas: {', '.join(map(str, resultado.paginas_com_mencao))}",
        f"🔍 Termos: {', '.join(resultado.termos_encontrados)}",
        "",
    ]
    if resultado.publicacoes:
        linhas.append("📌 *Publicações classificadas:*")
        for item in resultado.publicacoes[:5]:
            partes = [
                item.get("categoria") or "publicação",
                item.get("orgao"),
                item.get("tipo"),
                item.get("numero"),
            ]
            titulo = " - ".join(str(parte) for parte in partes if parte)
            linhas.append(f"— Pág. {item['pagina']}: {titulo}")
            if item.get("data_documento"):
                linhas.append(f"  Data: {item['data_documento']}")
            if item.get("valor"):
                linhas.append(f"  Valor: {item['valor']}")
            if item.get("assunto"):
                linhas.append(f"  Assunto: {_resumir(item['assunto'], 180)}")
        if len(resultado.publicacoes) > 5:
            linhas.append(
                f"— Mais {len(resultado.publicacoes) - 5} publicação(ões) omitida(s)."
            )
        linhas.append("")

    linhas.append("📝 *Trechos encontrados:*")
    for item in resultado.trechos[:10]:
        linhas.append(f"— Pág. {item['pagina']}: \"{_resumir(item['trecho'])}\"")
    if len(resultado.trechos) > 10:
        linhas.append(f"— Mais {len(resultado.trechos) - 10} trecho(s) omitido(s).")
    linhas.extend(["", f"🔗 [Abrir edição]({edicao.url})"])
    return "\n".join(linhas)


async def _enviar_telegram(mensagem: str) -> None:
    bot = Bot(token=SETTINGS.telegram_bot_token)
    await bot.send_message(
        chat_id=SETTINGS.telegram_chat_id,
        text=mensagem,
        parse_mode="Markdown",
        disable_web_page_preview=False,
    )


def _salvar_alerta(mensagem: str) -> Path:
    SETTINGS.alert_dir.mkdir(parents=True, exist_ok=True)
    path = SETTINGS.alert_dir / f"{datetime.now().date().isoformat()}.log"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(mensagem)
        fp.write("\n\n" + "-" * 80 + "\n\n")
    return path


def notificar(resultado: DetectionResult, edicao: Edicao) -> None:
    if not resultado.encontrado:
        return

    mensagem = montar_mensagem(resultado, edicao)
    if SETTINGS.telegram_bot_token and SETTINGS.telegram_chat_id:
        try:
            asyncio.run(_enviar_telegram(mensagem))
            database.mark_notified(resultado.edicao_id)
            logger.info("Notificação Telegram enviada para edição %s", resultado.edicao_id)
            return
        except Exception:
            logger.exception("Falha ao enviar Telegram; salvando alerta local.")

    path = _salvar_alerta(mensagem)
    logger.info("Alerta salvo em %s", path)


def enviar_teste() -> None:
    resultado = DetectionResult(
        encontrado=True,
        edicao_id=0,
        edicao_titulo="Teste",
        paginas_com_mencao=[1],
        trechos=[{"pagina": 1, "trecho": "Teste de alerta do monitor de Inajá."}],
        termos_encontrados=["Inajá"],
        mencoes_db=[],
        publicacoes=[],
    )
    edicao = Edicao(
        url=SETTINGS.site_url,
        titulo="Mensagem de teste",
        data_publicacao=datetime.now().date().isoformat(),
    )
    notificar(resultado, edicao)
