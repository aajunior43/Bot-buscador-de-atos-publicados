from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import smtplib
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from telegram import Bot

import database
from config import SETTINGS
from detector import DetectionResult
from scraper import Edicao


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telegram — singleton e helpers
# ---------------------------------------------------------------------------

_bot_instance: Bot | None = None
_bot_lock = threading.Lock()

_TELEGRAM_MAX = 4096

# Caracteres que precisam de escape no MarkdownV2
# Nota: a barra invertida deve ser escapada primeiro (antes dos outros chars)
_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _telegram_creds() -> tuple[str, str]:
    """Token e chat_id: settings DB sobrescreve .env."""
    try:
        import database as db

        token = (db.get_setting("telegram_bot_token", "") or "").strip()
        chat = (db.get_setting("telegram_chat_id", "") or "").strip()
    except Exception:
        token, chat = "", ""
    if not token:
        token = SETTINGS.telegram_bot_token
    if not chat:
        chat = SETTINGS.telegram_chat_id
    return token, chat


def _get_bot() -> Bot:
    """Retorna instância singleton do Bot, reutilizando a sessão HTTP."""
    global _bot_instance
    token, _ = _telegram_creds()
    with _bot_lock:
        # Se a chave mudou no Admin, recria o bot
        cur = getattr(_get_bot, "_token_used", None)
        if _bot_instance is None or cur != token:
            _bot_instance = Bot(token=token) if token else None
            _get_bot._token_used = token  # type: ignore[attr-defined]
        if _bot_instance is None:
            raise RuntimeError("TELEGRAM_BOT_TOKEN vazio")
        return _bot_instance


def _escape_mdv2(texto: str) -> str:
    """Escapa caracteres especiais para o MarkdownV2 do Telegram.

    A barra invertida é tratada primeiro pelo regex para evitar duplo-escape.
    """
    return _MDV2_SPECIAL.sub(r"\\\1", str(texto))


def _truncar_mensagem(texto: str, limite: int = _TELEGRAM_MAX) -> str:
    """Trunca a mensagem para o limite do Telegram, quebrando em linha inteira."""
    if len(texto) <= limite:
        return texto
    corte = texto[: limite - 50].rsplit("\n", 1)[0]
    return corte + "\n\n_\\(mensagem truncada\\)_"


def _rodar_async(coro):
    """Executa uma coroutine de forma segura de dentro de um contexto síncrono.

    Funciona tanto quando não há um event loop rodando (usa asyncio.run)
    quanto quando já há um loop rodando (executa em uma thread separada).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _resumir(trecho: str, limite: int = 150) -> str:
    texto = " ".join(trecho.split())
    return texto if len(texto) <= limite else texto[: limite - 3] + "..."


def _tem_publicacao_oficial(resultado: DetectionResult) -> bool:
    """Verifica se há publicação oficial do município (não apenas menção genérica)."""
    return any(
        p.get("orgao") or p.get("tipo") or p.get("categoria") == "publicacao_oficial"
        for p in resultado.publicacoes
    )


def montar_mensagem(resultado: DetectionResult, edicao: Edicao) -> str:
    """Monta mensagem formatada em MarkdownV2 para o Telegram."""
    eh_oficial = _tem_publicacao_oficial(resultado)
    if eh_oficial:
        cabecalho = "🏛️ *Publicação oficial de Inajá detectada\\!*"
    else:
        cabecalho = "📢 *Menção a Inajá detectada* _\\(sem publicação oficial identificada\\)_"

    titulo_esc = _escape_mdv2(edicao.titulo or "")
    data_esc = _escape_mdv2(edicao.data_publicacao or "não informada")
    paginas_esc = _escape_mdv2(", ".join(map(str, resultado.paginas_com_mencao)))
    termos_esc = _escape_mdv2(", ".join(resultado.termos_encontrados))

    linhas = [
        cabecalho,
        "",
        f"📰 Edição: {titulo_esc}",
        f"📅 Data: {data_esc}",
        f"📄 Páginas: {paginas_esc}",
        f"🔍 Termos: {termos_esc}",
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
            titulo_pub = " \\- ".join(
                _escape_mdv2(str(p)) for p in partes if p
            )
            linhas.append(f"— Pág\\. {_escape_mdv2(item['pagina'])}: {titulo_pub}")
            if item.get("data_documento"):
                linhas.append(f"  Data: {_escape_mdv2(item['data_documento'])}")
            if item.get("valor"):
                linhas.append(f"  Valor: {_escape_mdv2(item['valor'])}")
            if item.get("assunto"):
                linhas.append(f"  Assunto: {_escape_mdv2(_resumir(item['assunto'], 180))}")
            if item.get("resumo_ia"):
                linhas.append(f"  IA: {_escape_mdv2(_resumir(item['resumo_ia'], 180))}")
        if len(resultado.publicacoes) > 5:
            resto = len(resultado.publicacoes) - 5
            linhas.append(f"— Mais {resto} publicação\\(ões\\) omitida\\(s\\)\\.")
        linhas.append("")

    linhas.append("📝 *Trechos encontrados:*")
    for item in resultado.trechos[:10]:
        trecho_esc = _escape_mdv2(_resumir(item["trecho"]))
        linhas.append(f'— Pág\\. {_escape_mdv2(item["pagina"])}: "{trecho_esc}"')
    if len(resultado.trechos) > 10:
        resto = len(resultado.trechos) - 10
        linhas.append(f"— Mais {resto} trecho\\(s\\) omitido\\(s\\)\\.")
    url_esc = edicao.url.replace(")", "\\)")
    linhas.extend(["", f"🔗 [Abrir edição]({url_esc})"])
    return "\n".join(linhas)


async def _enviar_telegram(mensagem: str) -> None:
    bot = _get_bot()
    _, chat_id = _telegram_creds()
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID vazio")
    await bot.send_message(
        chat_id=chat_id,
        text=_truncar_mensagem(mensagem),
        parse_mode="MarkdownV2",
        disable_web_page_preview=False,
    )


def _enviar_telegram_com_retry(mensagem: str) -> None:
    """Envia mensagem Telegram com até 2 tentativas (intervalo de 3s). Lança exceção se falhar."""
    for tentativa in range(1, 3):
        try:
            _rodar_async(_enviar_telegram(mensagem))
            return
        except Exception as exc:
            logger.warning("Falha Telegram (tentativa %s/2): %s", tentativa, exc)
            if tentativa < 2:
                time.sleep(3)
    raise RuntimeError("Telegram falhou após 2 tentativas")


def _salvar_alerta(mensagem: str) -> Path:
    SETTINGS.alert_dir.mkdir(parents=True, exist_ok=True)
    path = SETTINGS.alert_dir / f"{datetime.now().date().isoformat()}.log"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(mensagem)
        fp.write("\n\n" + "-" * 80 + "\n\n")
    return path


def _enviar_email(assunto: str, corpo: str) -> bool:
    """Envia e-mail via SMTP. Retorna True se enviou com sucesso."""
    smtp_host = database.get_setting("smtp_host") or SETTINGS.smtp_host
    smtp_port = int(database.get_setting("smtp_port") or SETTINGS.smtp_port)
    smtp_user = database.get_setting("smtp_user") or SETTINGS.smtp_user
    smtp_pass = database.get_setting("smtp_pass") or SETTINGS.smtp_pass
    smtp_to = database.get_setting("smtp_to") or SETTINGS.smtp_to
    smtp_from = database.get_setting("smtp_from") or SETTINGS.smtp_from or smtp_user

    if not all([smtp_host, smtp_user, smtp_pass, smtp_to]):
        logger.info("SMTP não configurado — ignorando envio de e-mail.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = smtp_from
        msg["To"] = smtp_to
        # Versão texto simples
        corpo_limpo = corpo.replace("*", "").replace("_", "").replace("[", "").replace("]", "")
        msg.attach(MIMEText(corpo_limpo, "plain", "utf-8"))
        # Versão HTML básica
        corpo_html = corpo_limpo.replace("\n", "<br>")
        msg.attach(MIMEText(f"<pre style='font-family:sans-serif'>{corpo_html}</pre>", "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, smtp_to.split(","), msg.as_string())
        logger.info("E-mail enviado para %s", smtp_to)
        return True
    except Exception:
        logger.exception("Falha ao enviar e-mail via SMTP")
        return False


def _disparar_webhooks(payload: dict) -> None:
    """Dispara webhooks configurados no banco de dados em thread separada."""
    webhooks = database.get_webhooks()
    if not webhooks:
        return

    def _disparar(url: str) -> None:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Webhook disparado: %s → %s", url, resp.status_code)
        except Exception:
            logger.warning("Falha ao disparar webhook: %s", url)

    for wh in webhooks:
        t = threading.Thread(target=_disparar, args=(wh["url"],), daemon=True)
        t.start()


def _publicacoes_para_alerta(resultado: DetectionResult) -> list[dict]:
    """Filtra pubs que merecem alerta (importância / notificar_ia)."""
    pubs = list(resultado.publicacoes or [])
    if not pubs:
        return []
    if not getattr(SETTINGS, "ai_importancia", True):
        return pubs
    limiar = int(getattr(SETTINGS, "ai_importancia_min_notificar", 3) or 3)
    filtradas = []
    for p in pubs:
        notif = p.get("notificar_ia")
        if notif is False or notif == 0:
            continue
        if notif is True or notif == 1:
            filtradas.append(p)
            continue
        imp = p.get("importancia")
        try:
            imp_i = int(imp) if imp is not None else limiar
        except (TypeError, ValueError):
            imp_i = limiar
        if imp_i >= limiar:
            filtradas.append(p)
    return filtradas


def notificar(resultado: DetectionResult, edicao: Edicao) -> None:
    if not resultado.encontrado:
        return

    # Inteligência: só alerta se houver pub “importante” (ou menção sem pubs)
    pubs_alerta = _publicacoes_para_alerta(resultado)
    if resultado.publicacoes and not pubs_alerta:
        logger.info(
            "Notificação suprimida (importância baixa) edição %s — %s pub(s) sem alerta",
            resultado.edicao_id,
            len(resultado.publicacoes),
        )
        path = _salvar_alerta(
            montar_mensagem(resultado, edicao)
            + "\n\n[suprimido Telegram: importancia < limiar / notificar=false]"
        )
        database.insert_notificacao(
            resultado.edicao_id, "arquivo", path.read_text(encoding="utf-8")[:2000], True
        )
        return

    # Mensagem só com pubs relevantes quando filtro ativo
    if pubs_alerta and len(pubs_alerta) < len(resultado.publicacoes or []):
        from dataclasses import replace

        resultado = replace(resultado, publicacoes=pubs_alerta)

    mensagem = montar_mensagem(resultado, edicao)
    # Destaque de importância no Telegram
    if pubs_alerta:
        tops = [
            p
            for p in pubs_alerta
            if p.get("importancia")
        ]
        if tops:
            melhor = max(int(p.get("importancia") or 0) for p in tops)
            mensagem = (
                f"⭐ *Importância* {melhor}/5\n\n" + mensagem
            )
    # Anomalias (#3)
    anoms = [
        p
        for p in (pubs_alerta or resultado.publicacoes or [])
        if p.get("anomalia") in (1, True, "1")
    ]
    if anoms:
        motivos = []
        for p in anoms[:3]:
            m = (p.get("anomalia_motivo") or "").strip()
            if m:
                motivos.append(m)
        head = "🚨 *ANOMALIA DETECTADA*"
        if motivos:
            head += "\n" + "\n".join(f"• {m}" for m in motivos)
        mensagem = head + "\n\n" + mensagem

    canal_usado = "arquivo"
    sucesso = False
    erro_str = None

    # 1. Telegram com retry (até 2 tentativas, intervalo de 3s)
    tg_token, tg_chat = _telegram_creds()
    if tg_token and tg_chat:
        try:
            _enviar_telegram_com_retry(mensagem)
            database.mark_notified(resultado.edicao_id)
            logger.info("Notificação Telegram enviada para edição %s", resultado.edicao_id)
            canal_usado = "telegram"
            sucesso = True
        except Exception as exc:
            erro_str = str(exc)
            logger.error("Telegram falhou após 2 tentativas; tentando fallback.")
    elif tg_token and not tg_chat:
        logger.warning(
            "TELEGRAM_BOT_TOKEN definido, mas TELEGRAM_CHAT_ID está vazio — "
            "alertas irão para e-mail/arquivo. Configure o chat.id no .env ou Admin."
        )
    elif not tg_token:
        logger.debug("Telegram não configurado (sem TELEGRAM_BOT_TOKEN).")

    # 2. E-mail: como fallback OU como cópia simultânea se notify_email_always=True
    assunto = f"[Monitor Inajá] {edicao.titulo or 'Nova publicação detectada'}"
    if not sucesso:
        # Fallback: Telegram falhou
        if _enviar_email(assunto, mensagem):
            database.mark_notified(resultado.edicao_id)
            canal_usado = "email"
            sucesso = True
            erro_str = None
    elif SETTINGS.notify_email_always:
        # Cópia simultânea: Telegram OK, mas email também deve ser enviado
        threading.Thread(
            target=_enviar_email,
            args=(assunto, mensagem),
            daemon=True,
        ).start()
        logger.info("E-mail de cópia disparado em background (NOTIFY_EMAIL_ALWAYS=true).")

    # 3. Arquivo local como último recurso
    if not sucesso:
        path = _salvar_alerta(mensagem)
        logger.info("Alerta salvo em %s", path)
        canal_usado = "arquivo"
        sucesso = True

    # Salvar histórico
    database.insert_notificacao(
        edicao_id=resultado.edicao_id,
        canal=canal_usado,
        conteudo=mensagem,
        sucesso=sucesso,
        erro=erro_str,
    )

    # Disparar webhooks em background
    payload = {
        "edicao_id": resultado.edicao_id,
        "edicao_titulo": resultado.edicao_titulo,
        "paginas": resultado.paginas_com_mencao,
        "termos": resultado.termos_encontrados,
        "publicacoes": len(resultado.publicacoes),
        "mencoes": len(resultado.mencoes_db),
        "url": edicao.url,
    }
    _disparar_webhooks(payload)


def verificar_ausencia_publicacao() -> None:
    """Envia alerta se não houve publicação de Inajá nos últimos N dias."""
    days = SETTINGS.absence_alert_days
    if not database.get_absence_alert_needed(days):
        return
    mensagem = (
        f"⚠️ *Alerta de ausência* — Nenhuma publicação oficial de Inajá\\-PR "
        f"foi detectada nos últimos {days} dias\\.\n\n"
        f"Verifique o sistema ou se o jornal publicou algum ato oficial recentemente\\."
    )
    logger.warning("Alerta de ausência: nenhuma publicação em %s dias", days)
    if SETTINGS.telegram_bot_token and SETTINGS.telegram_chat_id:
        try:
            _enviar_telegram_com_retry(mensagem)
        except Exception:
            logger.exception("Falha ao enviar alerta de ausência via Telegram")
    database.insert_notificacao(
        edicao_id=None,
        canal="telegram_ausencia",
        conteudo=mensagem,
        sucesso=True,
    )


def enviar_teste() -> dict:
    """Envia notificação de teste. Retorna dict com canal e status."""
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
    token, chat = _telegram_creds()
    notificar(resultado, edicao)
    # Inferir canal pelo estado das credenciais + arquivo
    if token and chat:
        canal = "telegram"
        detalhe = "Tentativa via Telegram (verifique o chat)."
    elif token and not chat:
        canal = "arquivo"
        detalhe = "Token ok, mas CHAT_ID vazio — gravou em alertas/."
    else:
        canal = "arquivo"
        detalhe = "Telegram não configurado — gravou em alertas/."
    return {
        "ok": True,
        "canal": canal,
        "detalhe": detalhe,
        "token_presente": bool(token),
        "chat_id_presente": bool(chat),
    }


def status_telegram() -> dict:
    token, chat = _telegram_creds()
    return {
        "token_presente": bool(token),
        "chat_id_presente": bool(chat),
        "pronto": bool(token and chat),
        "token_masked": ("…" + token[-6:]) if token and len(token) > 6 else ("***" if token else ""),
        "chat_id": chat if chat else "",
    }
