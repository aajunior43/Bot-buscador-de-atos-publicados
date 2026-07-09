"""Orquestrador único do pipeline download → OCR → detecção → notificação.

Usado por ``main.py`` (CLI/scheduler) e ``webapp.py`` (análise manual/lote)
para evitar divergência de flags e de etapas entre as duas entradas.
"""
from __future__ import annotations

import logging
from pathlib import Path

import database
from ai_processor import retry_pending_ia
from config import SETTINGS
from detector import DetectionResult, detectar
from downloader import baixar_edicao
from notifier import notificar
from ocr import extrair_texto, extrair_texto_rapido_com_estruturado_candidato
from process_lock import ProcessLockError, process_lock
from scraper import Edicao, listar_edicoes

logger = logging.getLogger(__name__)


def _ocr_mensagem_modo(force_ocr: bool, fast_ocr: bool) -> str:
    if force_ocr and fast_ocr:
        return "OCR rápido + estruturado em páginas candidatas"
    if force_ocr:
        return "OCR forçado estruturado completo"
    return "OCR híbrido"


def processar_edicao(
    edicao: Edicao,
    *,
    force_ocr: bool = False,
    fast_ocr: bool = True,
    edicao_id: int | None = None,
    notificar_se_encontrado: bool = True,
) -> DetectionResult | None:
    """Processa uma edição completa (com lock global de OCR).

    Returns:
        DetectionResult se OCR+detecção rodaram; None se o download falhou.
    """
    try:
        with process_lock(label=f"edicao:{edicao.titulo or edicao.url}"):
            return _processar_edicao_unlocked(
                edicao,
                force_ocr=force_ocr,
                fast_ocr=fast_ocr,
                edicao_id=edicao_id,
                notificar_se_encontrado=notificar_se_encontrado,
            )
    except ProcessLockError as exc:
        logger.warning("%s", exc)
        database.log_job(
            "processando edição",
            "ignorado",
            titulo=edicao.titulo,
            edicao_id=edicao_id,
            mensagem=str(exc),
        )
        return None


def _processar_edicao_unlocked(
    edicao: Edicao,
    *,
    force_ocr: bool = False,
    fast_ocr: bool = True,
    edicao_id: int | None = None,
    notificar_se_encontrado: bool = True,
) -> DetectionResult | None:
    download_job = database.start_job(
        "baixando PDF",
        titulo=edicao.titulo,
        edicao_id=edicao_id,
        mensagem=edicao.url,
        progress_step="download",
        progress_current=0,
        progress_total=100,
    )
    try:
        download = baixar_edicao(edicao)
        database.update_job(
            download_job,
            "concluido",
            mensagem=f"PDF salvo em {download.caminho}",
            edicao_id=download.edicao_id,
            progress_step="download",
            progress_current=100,
            progress_total=100,
        )
    except Exception:
        logger.exception("Falha ao baixar edição %s", edicao.url)
        database.update_job(
            download_job,
            "erro",
            mensagem=f"Falha ao baixar {edicao.url}",
            edicao_id=edicao_id,
            progress_step="download",
        )
        return None

    ocr_job = database.start_job(
        "rodando OCR",
        titulo=edicao.titulo,
        edicao_id=download.edicao_id,
        mensagem=_ocr_mensagem_modo(force_ocr, fast_ocr),
        progress_step="ocr",
        progress_current=0,
        progress_total=100,
    )
    try:
        def on_progress(msg: str | dict) -> None:
            if isinstance(msg, dict):
                database.update_job(
                    ocr_job,
                    "rodando",
                    mensagem=str(msg.get("msg") or msg),
                    progress_current=msg.get("current"),
                    progress_total=msg.get("total"),
                    progress_step=msg.get("step") or "ocr",
                )
                return
            # Parse "Página X/Y"
            cur = tot = None
            if isinstance(msg, str):
                import re

                m = re.search(r"Página\s+(\d+)\s*/\s*(\d+)", msg, re.I)
                if m:
                    cur, tot = int(m.group(1)), int(m.group(2))
            database.update_job(
                ocr_job,
                "rodando",
                mensagem=str(msg),
                progress_current=cur,
                progress_total=tot,
                progress_step="ocr",
            )

        if force_ocr and fast_ocr:
            ocr = extrair_texto_rapido_com_estruturado_candidato(
                download.caminho, on_progress=on_progress
            )
        else:
            ocr = extrair_texto(
                download.caminho, force_ocr=force_ocr, on_progress=on_progress
            )

        ocr_status = "aviso" if ocr.avisos else "concluido"
        ocr_mensagem = (
            f"{len(ocr.paginas)} página(s), {len(ocr.texto_completo)} caracteres"
        )
        if ocr.avisos:
            ocr_mensagem += " | " + "; ".join(ocr.avisos)
        database.update_job(
            ocr_job,
            ocr_status,
            mensagem=ocr_mensagem,
            progress_step="ocr",
            progress_current=len(ocr.paginas),
            progress_total=len(ocr.paginas) or 100,
        )

        detectar_job = database.start_job(
            "detectando publicações",
            titulo=edicao.titulo,
            edicao_id=download.edicao_id,
            progress_step="detect",
            progress_current=0,
            progress_total=100,
        )
        database.update_job(
            detectar_job,
            "rodando",
            mensagem="Analisando menções e atos…",
            progress_step="detect",
            progress_current=30,
            progress_total=100,
        )
        resultado = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
        database.update_job(
            detectar_job,
            "rodando",
            mensagem="Gravando publicações…",
            progress_step="detect",
            progress_current=70,
            progress_total=100,
        )
        database.insert_mencoes(download.edicao_id, resultado.mencoes_db)
        database.insert_publicacoes(download.edicao_id, resultado.publicacoes)
        database.salvar_arquivos_atos_locais(ocr.texto_path, resultado.publicacoes)
        database.update_ocr(download.edicao_id, ocr.texto_path, resultado.encontrado)
        if resultado.metricas:
            database.salvar_metricas_deteccao(download.edicao_id, resultado.metricas)
        database.update_job(
            detectar_job,
            "concluido",
            mensagem=(
                f"{len(resultado.publicacoes)} publicação(ões), "
                f"{len(resultado.mencoes_db)} menção(ões)"
            ),
            progress_step="detect",
            progress_current=100,
            progress_total=100,
        )

        if notificar_se_encontrado and resultado.encontrado:
            notify_job = database.start_job(
                "notificando",
                titulo=edicao.titulo,
                edicao_id=download.edicao_id,
                progress_step="notify",
                progress_current=0,
                progress_total=100,
            )
            notificar(resultado, edicao)
            database.update_job(
                notify_job,
                "concluido",
                mensagem="Alerta emitido",
                progress_step="notify",
                progress_current=100,
                progress_total=100,
            )

        logger.info(
            "Edição processada: id=%s tem_inaja=%s pubs=%s",
            download.edicao_id,
            resultado.encontrado,
            len(resultado.publicacoes),
        )
        return resultado
    except Exception:
        logger.exception(
            "Falha ao processar OCR/detecção da edição %s", edicao.url
        )
        database.update_job(
            ocr_job,
            "erro",
            mensagem=f"Falha ao processar OCR/detecção: {edicao.url}",
            edicao_id=download.edicao_id,
            progress_step="ocr",
        )
        raise


def reprocessar_deteccao_de_cache(
    edicao_id: int,
    *,
    notificar_se_encontrado: bool = False,
) -> DetectionResult | None:
    """Reexecuta detecção+IA usando cache OCR (`.ocr.json`), sem re-OCR completo.

    Útil para auditoria de falsos negativos e para popular ``deteccao_metricas``.
    """
    from ocr.cache import _carregar_cache_ocr

    with database.connect() as conn:
        row = conn.execute(
            "SELECT * FROM edicoes WHERE id = ?", (edicao_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Edição id={edicao_id} não encontrada")
        caminho = row["caminho_local"]
        titulo = row["titulo"] or f"Edição {edicao_id}"
        edicao = Edicao(
            url=row["url"],
            titulo=titulo,
            data_publicacao=row["data_publicacao"],
        )

    if not caminho or not Path(caminho).exists():
        logger.warning("Sem PDF local para edicao_id=%s", edicao_id)
        return None

    pdf_path = Path(caminho)
    ocr = _carregar_cache_ocr(pdf_path)
    if ocr is None or not ocr.paginas:
        logger.warning("Sem cache OCR para edicao_id=%s path=%s", edicao_id, pdf_path)
        return None

    job = database.start_job(
        "redetectando (cache OCR)",
        titulo=titulo,
        edicao_id=edicao_id,
        mensagem="Reprocessando detecção a partir do .ocr.json",
    )
    try:
        # Preserva campos de IA já gravados (se a API estiver fora, não apaga resumos).
        pubs_anteriores = _publicacoes_existentes(edicao_id)

        resultado = detectar(edicao_id, titulo, ocr.paginas)
        pubs = _mesclar_ia_anterior(resultado.publicacoes, pubs_anteriores)
        # Reconstroi resultado com pubs mescladas (dataclass frozen)
        from dataclasses import replace

        resultado = replace(resultado, publicacoes=pubs)

        database.insert_mencoes(edicao_id, resultado.mencoes_db)
        database.insert_publicacoes(edicao_id, resultado.publicacoes)
        database.salvar_arquivos_atos_locais(
            ocr.texto_path if ocr.texto_path else pdf_path.with_suffix(".txt"),
            resultado.publicacoes,
        )
        database.update_ocr(
            edicao_id,
            ocr.texto_path or pdf_path.with_suffix(".txt"),
            resultado.encontrado,
        )
        if resultado.metricas:
            database.salvar_metricas_deteccao(edicao_id, resultado.metricas)
        database.update_job(
            job,
            "concluido",
            mensagem=(
                f"{len(resultado.publicacoes)} pub(s), "
                f"{len(resultado.mencoes_db)} menção(ões)"
            ),
        )
        if notificar_se_encontrado and resultado.encontrado:
            notificar(resultado, edicao)
        return resultado
    except Exception:
        logger.exception("Falha ao redetectar edicao_id=%s", edicao_id)
        database.update_job(job, "erro", mensagem=f"Falha redetecção id={edicao_id}")
        raise


def _publicacoes_existentes(edicao_id: int) -> list[dict]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM publicacoes WHERE edicao_id = ?", (edicao_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def _mesclar_ia_anterior(
    novas: list[dict], anteriores: list[dict]
) -> list[dict]:
    """Copia resumo_ia/campos refinados de publicações anteriores parecidas.

    Chave: página + tipo normalizado + número (quando houver).
    """
    if not anteriores:
        return novas

    def chave(p: dict) -> str:
        return "|".join(
            [
                str(p.get("pagina") or ""),
                (p.get("tipo") or "").strip().casefold(),
                (p.get("numero") or "").strip().casefold(),
            ]
        )

    idx = {chave(p): p for p in anteriores}
    # também índice por trecho prefixo
    idx_trecho = {
        (p.get("trecho") or "")[:120].strip().casefold(): p for p in anteriores
    }

    campos_ia = (
        "resumo_ia",
        "categoria_ia",
        "texto_corrigido",
        "orgao",
        "tipo",
        "numero",
        "data_documento",
        "assunto",
        "valor",
    )
    out: list[dict] = []
    for pub in novas:
        merged = dict(pub)
        prev = idx.get(chave(pub))
        if prev is None:
            prev = idx_trecho.get((pub.get("trecho") or "")[:120].strip().casefold())
        if prev and not merged.get("resumo_ia") and prev.get("resumo_ia"):
            for campo in campos_ia:
                if prev.get(campo) and not merged.get(campo):
                    merged[campo] = prev[campo]
            # se a nova não tem órgão mas a antiga tinha (e era Inajá), reaproveita
            if not merged.get("orgao") and prev.get("orgao"):
                merged["orgao"] = prev["orgao"]
        out.append(merged)
    return out


def processar_edicao_por_id(
    edicao_id: int,
    *,
    force_ocr: bool = True,
    fast_ocr: bool = True,
    notificar_se_encontrado: bool = True,
) -> DetectionResult | None:
    """Carrega edição do banco e processa (entrada preferida da webapp)."""
    with database.connect() as conn:
        row = conn.execute(
            "SELECT * FROM edicoes WHERE id = ?", (edicao_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Edição id={edicao_id} não encontrada")
        edicao = Edicao(
            url=row["url"],
            titulo=row["titulo"] or f"Edição {row['id']}",
            data_publicacao=row["data_publicacao"],
        )
    return processar_edicao(
        edicao,
        force_ocr=force_ocr,
        fast_ocr=fast_ocr,
        edicao_id=edicao_id,
        notificar_se_encontrado=notificar_se_encontrado,
    )


def _backup_diario_se_preciso() -> None:
    """No máximo um backup por dia civil (logs/backups)."""
    try:
        dest_dir = SETTINGS.log_dir / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        hoje = __import__("datetime").datetime.now().strftime("%Y%m%d")
        if any(dest_dir.glob(f"jornal_monitor_{hoje}_*.db")):
            return
        path = database.backup_database(dest_dir)
        logger.info("Backup diário automático: %s", path)
    except Exception:
        logger.warning("Backup diário falhou (seguindo o ciclo).", exc_info=True)


def processar_pendentes_automatico(
    *,
    limit: int | None = None,
    recent_days: int | None = None,
    force_ocr: bool = True,
    fast_ocr: bool = True,
) -> int:
    """Processa edições com ocr_processado=0 (mais recentes primeiro).

    Returns:
        Quantidade de edições em que o pipeline foi invocado.
    """
    lim = limit if limit is not None else SETTINGS.auto_process_limit
    dias = recent_days if recent_days is not None else SETTINGS.auto_process_dias
    rows = database.get_pending_edicoes(
        process_all=False,
        limit=max(0, int(lim)),
        recent_days=int(dias) if dias else None,
    )
    if not rows:
        logger.info("Automação: nenhuma edição pendente de OCR.")
        return 0

    job = database.start_job(
        "auto-processando pendentes",
        mensagem=f"{len(rows)} edição(ões) (limite={lim}, dias={dias or 'todos'})",
    )
    ok = 0
    for row in rows:
        edicao = Edicao(
            url=row["url"],
            titulo=row["titulo"] or f"Edição {row['id']}",
            data_publicacao=row["data_publicacao"],
        )
        try:
            resultado = processar_edicao(
                edicao,
                force_ocr=force_ocr,
                fast_ocr=fast_ocr,
                edicao_id=int(row["id"]),
                notificar_se_encontrado=True,
            )
            if resultado is not None:
                ok += 1
        except Exception:
            logger.exception(
                "Automação: falha na edição id=%s", row["id"]
            )
            continue
    database.update_job(
        job,
        "concluido",
        mensagem=f"Processadas {ok}/{len(rows)} edição(ões) pendentes",
    )
    logger.info("Automação: %s/%s edições pendentes processadas", ok, len(rows))
    return ok


def executar_ciclo(
    force_rescan: bool = False,
    process_all: bool = False,
    force_ocr: bool = False,
    fast_ocr: bool = True,
) -> None:
    """Ciclo completo automatizado (BOT): listar → processar novas → pendentes → IA.

    A interface WEB só cadastra edições; o OCR e as notificações ficam neste ciclo.
    """
    database.init_db()
    _backup_diario_se_preciso()
    processadas = 0
    pendentes_ok = 0

    try:
        retry_pending_ia()
    except Exception:
        logger.warning("Falha no retry de IA pendente — continuando ciclo normal.")

    if force_rescan:
        logger.warning("Reprocessamento forçado ativado; limpando status anterior.")
        database.reset_processing()

    try:
        listar_job = database.start_job(
            "verificando novas edições", mensagem=SETTINGS.site_url
        )
        novas = listar_edicoes(force_rescan=force_rescan)
        database.update_job(
            listar_job,
            "concluido",
            mensagem=f"{len(novas)} edição(ões) nova(s)",
        )
    except Exception:
        logger.exception("Falha ao listar edições.")
        database.log_job(
            "verificando novas edições",
            "erro",
            mensagem="Falha ao listar edições",
        )
        novas = []

    for edicao in novas:
        try:
            # Novas: OCR rápido+estruturado por padrão (force_ocr or auto)
            resultado = processar_edicao(
                edicao,
                force_ocr=force_ocr or SETTINGS.auto_process,
                fast_ocr=fast_ocr,
            )
            if resultado is not None:
                processadas += 1
        except Exception:
            continue

    # Fila residual (registradas pela web sem OCR, ou falhas anteriores)
    if process_all:
        for row in database.get_pending_edicoes(process_all=False, limit=None):
            caminho = row["caminho_local"]
            if not caminho or not Path(caminho).exists():
                # Sem PDF local: processar_edicao tenta baixar de novo
                pass
            edicao = Edicao(
                url=row["url"],
                titulo=row["titulo"] or f"Edição {row['id']}",
                data_publicacao=row["data_publicacao"],
            )
            try:
                resultado = processar_edicao(
                    edicao,
                    force_ocr=force_ocr or True,
                    fast_ocr=fast_ocr,
                    edicao_id=int(row["id"]),
                )
                if resultado is not None:
                    pendentes_ok += 1
            except Exception:
                continue
    elif SETTINGS.auto_process:
        try:
            pendentes_ok = processar_pendentes_automatico(
                force_ocr=True,
                fast_ocr=fast_ocr,
            )
        except Exception:
            logger.exception("Falha no auto-processamento de pendentes.")

    resumo = (
        f"novas={len(novas)} processadas={processadas} "
        f"fila_pendentes={pendentes_ok} auto={SETTINGS.auto_process}"
    )
    database.registrar_evento_ciclo("bot_ciclo", resumo)
    logger.info("Ciclo BOT concluído: %s", resumo)
