"""Orquestrador único do pipeline download → OCR → detecção → notificação.

Usado por ``main.py`` (CLI/scheduler) e ``webapp.py`` (análise manual/lote)
para evitar divergência de flags e de etapas entre as duas entradas.
"""
from __future__ import annotations

import logging
from pathlib import Path

import console_ui
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
    ui_indice: int | None = None,
    ui_total_lote: int | None = None,
    ui_pendentes: int | None = None,
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
                ui_indice=ui_indice,
                ui_total_lote=ui_total_lote,
                ui_pendentes=ui_pendentes,
            )
    except ProcessLockError as exc:
        # Lock ocupado não conta como falha da edição
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
    ui_indice: int | None = None,
    ui_total_lote: int | None = None,
    ui_pendentes: int | None = None,
) -> DetectionResult | None:
    t0 = console_ui.edition_start(
        titulo=edicao.titulo or edicao.url,
        data=edicao.data_publicacao,
        edicao_id=edicao_id,
        indice=ui_indice,
        total_lote=ui_total_lote,
        pendentes_restantes=ui_pendentes,
    )
    console_ui.step("Download", "buscando PDF…", phase="DL")

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
        console_ui.step(
            "Download",
            f"ok · {Path(download.caminho).name}",
            ok=True,
            phase="DL",
        )
    except Exception as exc:
        logger.exception("Falha ao baixar edição %s", edicao.url)
        database.update_job(
            download_job,
            "erro",
            mensagem=f"Falha ao baixar {edicao.url}",
            edicao_id=edicao_id,
            progress_step="download",
        )
        if edicao_id:
            database.registrar_falha_processamento(
                int(edicao_id), f"download: {exc}"
            )
        console_ui.step("Download", str(exc)[:80], ok=False, phase="DL")
        console_ui.edition_end(ok=False, t0=t0, erro=f"download: {exc}")
        return None

    modo = _ocr_mensagem_modo(force_ocr, fast_ocr)
    console_ui.step("OCR", modo, phase="OCR")
    ocr_job = database.start_job(
        "rodando OCR",
        titulo=edicao.titulo,
        edicao_id=download.edicao_id,
        mensagem=modo,
        progress_step="ocr",
        progress_current=0,
        progress_total=100,
    )
    try:
        def on_progress(msg: str | dict) -> None:
            cur, tot, label = console_ui.parse_progress_payload(msg)
            if cur is not None and tot is not None:
                console_ui.progress(cur, tot, label=label)
            texto = (
                str(msg.get("msg") or msg)
                if isinstance(msg, dict)
                else str(msg)
            )
            step = "ocr"
            if isinstance(msg, dict):
                step = str(msg.get("step") or "ocr")
                database.update_job(
                    ocr_job,
                    "rodando",
                    mensagem=texto,
                    progress_current=msg.get("current") or cur,
                    progress_total=msg.get("total") or tot,
                    progress_step=step,
                )
                return
            database.update_job(
                ocr_job,
                "rodando",
                mensagem=texto,
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
        console_ui.step(
            "OCR",
            f"{len(ocr.paginas)} págs · {len(ocr.texto_completo):,} chars".replace(
                ",", "."
            ),
            ok=True,
            phase="OCR",
        )

        console_ui.step("Detecção", "menções e atos oficiais…", phase="DET")
        console_ui.step("IA", "refino estruturado (se houver candidatos)…", phase="IA")
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
        try:
            import atos_arquivo

            atos_arquivo.espelhar_edicao(
                download.edicao_id,
                resultado.publicacoes,
                edicao_meta={
                    "id": download.edicao_id,
                    "titulo": edicao.titulo,
                    "data_publicacao": edicao.data_publicacao,
                    "url": edicao.url,
                },
            )
        except Exception:
            logger.exception("Falha ao espelhar atos em pasta organizada")
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
        console_ui.step(
            "Detecção",
            f"{len(resultado.publicacoes)} pub · {len(resultado.mencoes_db)} menções"
            + (" · INAJÁ" if resultado.encontrado else ""),
            ok=True,
            phase="DET",
        )
        console_ui.step(
            "IA",
            (
                f"{len(resultado.publicacoes)} ato(s) refinados"
                if resultado.publicacoes
                else "nada a refinar"
            ),
            ok=True,
            phase="IA",
        )

        if notificar_se_encontrado and resultado.encontrado:
            console_ui.step("Alerta", "Telegram / e-mail / arquivo…", phase="ALR")
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
            console_ui.step("Alerta", "enviado", ok=True, phase="ALR")
        else:
            console_ui.phase_set("ALR", "skip")

        database.limpar_falhas_processamento(download.edicao_id)
        logger.info(
            "Edição processada: id=%s tem_inaja=%s pubs=%s",
            download.edicao_id,
            resultado.encontrado,
            len(resultado.publicacoes),
        )
        # Score pós-OCR (inteligência de fila / métricas)
        try:
            from inteligencia import score_texto_candidatura

            blob = " ".join(
                (p.texto or "")[:2000] for p in (ocr.paginas or [])[:8]
            )
            sr = score_texto_candidatura(blob, titulo=edicao.titulo or "")
            database.atualizar_score_edicao(
                download.edicao_id, sr.score, sr.prioridade
            )
            logger.info(
                "Score candidatura id=%s score=%s prio=%s (%s)",
                download.edicao_id,
                sr.score,
                sr.prioridade,
                ", ".join(sr.motivos[:4]) or "—",
            )
        except Exception:
            logger.debug("score pós-OCR falhou", exc_info=True)

        console_ui.edition_end(
            ok=True,
            tem_inaja=bool(resultado.encontrado),
            n_pubs=len(resultado.publicacoes),
            n_mencoes=len(resultado.mencoes_db),
            t0=t0,
            publicacoes=list(resultado.publicacoes or []),
        )
        return resultado
    except Exception as exc:
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
        database.registrar_falha_processamento(
            download.edicao_id, f"ocr/detecção: {exc}"
        )
        console_ui.step("OCR/Detecção", str(exc)[:80], ok=False, phase="OCR")
        console_ui.edition_end(ok=False, t0=t0, erro=str(exc))
        # Não propaga: falha contada; fila segue para a próxima edição
        return None


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
        data_pub = row["data_publicacao"]
        url_ed = row["url"]
        edicao = Edicao(
            url=url_ed,
            titulo=titulo,
            data_publicacao=data_pub,
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
        try:
            import atos_arquivo

            atos_arquivo.espelhar_edicao(
                edicao_id,
                resultado.publicacoes,
                edicao_meta={
                    "id": edicao_id,
                    "titulo": titulo,
                    "data_publicacao": data_pub,
                    "url": url_ed,
                },
            )
        except Exception:
            logger.exception("Falha ao espelhar atos em pasta organizada")
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
    max_total: int | None = None,
    lotes: bool = True,
    quiet: bool = False,
) -> int:
    """Processa edições com ocr_processado=0 (mais recentes primeiro).

    Args:
        limit: tamanho de cada lote (padrão AUTO_PROCESS_LIMIT).
        max_total: teto de edições nesta chamada. None usa
            AUTO_PROCESS_MAX_POR_CICLO; 0 = só um lote (limit).
        lotes: se True, repete lotes até esgotar a fila (na janela de dias)
            ou atingir max_total.
        quiet: se True, fila vazia só em DEBUG (uso no loop idle).

    Returns:
        Quantidade de edições em que o pipeline foi invocado com sucesso.
    """
    lim = max(1, int(limit if limit is not None else SETTINGS.auto_process_limit))
    dias = recent_days if recent_days is not None else SETTINGS.auto_process_dias
    desde = (SETTINGS.auto_process_desde or "").strip()
    if max_total is None:
        max_total = int(SETTINGS.auto_process_max_por_ciclo or 0)
    teto = int(max_total) if max_total and max_total > 0 else lim

    total_ok = 0
    lote_n = 0
    while total_ok < teto:
        restante = teto - total_ok
        batch = min(lim, restante)
        rows = database.get_pending_edicoes(
            process_all=False,
            limit=batch,
            recent_days=int(dias) if dias else None,
            desde=desde or None,
        )
        if not rows:
            if lote_n == 0:
                msg = (
                    "Automação: nenhuma edição pendente de OCR "
                    f"(janela={dias or 'todas'} dias"
                    f"{', desde=' + desde if desde else ''})."
                )
                if quiet:
                    logger.debug(msg)
                else:
                    logger.info(msg)
            break

        lote_n += 1
        if not quiet:
            console_ui.ciclo_banner(
                f"LOTE {lote_n}",
                f"{len(rows)} edição(ões) · limite={lim} · teto={teto} · "
                f"dias={dias or 'todos'}"
                + (f" · desde={desde}" if desde else ""),
            )
        job = database.start_job(
            "auto-processando pendentes",
            mensagem=(
                f"lote {lote_n}: {len(rows)} edição(ões) "
                f"(limite_lote={lim}, teto={teto}, dias={dias or 'todos'})"
            ),
        )
        ok = 0
        try:
            restam_fila = len(
                database.get_pending_edicoes(
                    process_all=False,
                    limit=500,
                    recent_days=int(dias) if dias else None,
                    desde=desde or None,
                )
            )
        except Exception:
            restam_fila = len(rows)
        for i, row in enumerate(rows, start=1):
            database.registrar_heartbeat_bot()
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
                    ui_indice=i,
                    ui_total_lote=len(rows),
                    ui_pendentes=max(0, restam_fila - i + 1),
                )
                if resultado is not None:
                    ok += 1
                    total_ok += 1
                # resultado None: download/OCR já registrou falha (ou lock)
            except Exception:
                logger.exception(
                    "Automação: falha inesperada na edição id=%s", row["id"]
                )
                try:
                    database.registrar_falha_processamento(
                        int(row["id"]), "exceção não tratada no lote"
                    )
                except Exception:
                    pass
                continue
        database.update_job(
            job,
            "concluido",
            mensagem=f"Lote {lote_n}: {ok}/{len(rows)} ok (acumulado {total_ok})",
        )
        logger.info(
            "Automação: lote %s → %s/%s ok (acumulado %s/%s)",
            lote_n,
            ok,
            len(rows),
            total_ok,
            teto,
        )
        try:
            st = database.get_status_automacao()
            console_ui.cockpit(
                pendentes=st.get("pendentes_ocr"),
                fila=st.get("fila_proximo_ciclo"),
                quarentena=st.get("quarentena_count"),
                bot_vivo=True,
            )
        except Exception:
            try:
                console_ui.status_fila()
            except Exception:
                pass
        if not lotes:
            break
        # Evita loop infinito se OCR falhar e ocr_processado continuar 0
        if ok == 0:
            logger.warning(
                "Automação: lote sem progresso — interrompendo para evitar loop."
            )
            break

    if total_ok:
        logger.info("Automação: total processado nesta rodada: %s", total_ok)
    return total_ok


def executar_ciclo(
    force_rescan: bool = False,
    process_all: bool = False,
    force_ocr: bool = False,
    fast_ocr: bool = True,
) -> None:
    """Ciclo completo automatizado (BOT): listar → processar novas → pendentes → IA.

    A interface WEB só cadastra edições; o OCR e as notificações ficam neste ciclo.
    """
    console_ui.ciclo_banner(
        "CICLO BOT",
        "varredura → score fila → novas → pendentes → IA → resumo",
    )
    database.init_db()
    database.registrar_heartbeat_bot()
    try:
        n_scores = database.recalcular_scores_pendentes(limit=800)
        if n_scores:
            logger.info("Scores de candidatura atualizados: %s edições", n_scores)
    except Exception:
        logger.debug("recalcular_scores_pendentes falhou", exc_info=True)
    stuck = database.cleanup_stuck_jobs(max_hours=2)
    if stuck:
        logger.warning("Ciclo BOT: %s job(s) travado(s) marcado(s) como erro", stuck)
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
        f"novas_listadas={len(novas)} novas_processadas={processadas} "
        f"fila_processada={pendentes_ok} "
        f"total_ok={processadas + pendentes_ok} auto={SETTINGS.auto_process}"
    )
    database.registrar_evento_ciclo("bot_ciclo", resumo)
    logger.info("Ciclo BOT concluído: %s", resumo)
    console_ui.ciclo_banner("CICLO CONCLUÍDO", resumo)
    try:
        st = database.get_status_automacao()
        console_ui.status_fila(
            pendentes=st.get("pendentes_ocr"),
            fila=st.get("fila_proximo_ciclo"),
            quarentena=st.get("quarentena_count"),
        )
    except Exception:
        pass
    try:
        from inteligencia import gerar_resumo_diario_from_db

        r = gerar_resumo_diario_from_db()
        logger.info("Resumo diário: %s (%s pubs)", r.get("dia"), r.get("n_pubs"))
        try:
            console_ui.step(
                "Resumo diário",
                f"{r.get('n_pubs', 0)} pub(s) no recorte de hoje",
                ok=True,
            )
        except Exception:
            pass
    except Exception:
        logger.debug("gerar_resumo_diario falhou", exc_info=True)
