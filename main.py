from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import schedule

import database
from config import SETTINGS
from detector import detectar
from downloader import baixar_edicao
from notifier import enviar_teste, notificar
from ocr_processor import extrair_texto, extrair_texto_rapido_com_estruturado_candidato
from scraper import Edicao, listar_edicoes


logger = logging.getLogger(__name__)


def configurar_logging() -> None:
    SETTINGS.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = SETTINGS.log_dir / "monitor.log"
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    logging.basicConfig(level=logging.INFO, handlers=[handler, console])


def _processar_edicao(
    edicao: Edicao,
    force_ocr: bool = False,
    fast_ocr: bool = True,
) -> None:
    download_job = database.start_job(
        "baixando PDF",
        titulo=edicao.titulo,
        mensagem=edicao.url,
    )
    try:
        download = baixar_edicao(edicao)
        database.update_job(
            download_job,
            "concluido",
            mensagem=f"PDF salvo em {download.caminho}",
            edicao_id=download.edicao_id,
        )
    except Exception:
        logger.exception("Falha ao baixar edição %s", edicao.url)
        database.update_job(download_job, "erro", mensagem=f"Falha ao baixar {edicao.url}")
        return

    ocr_job = database.start_job(
        "rodando OCR",
        titulo=edicao.titulo,
        edicao_id=download.edicao_id,
        mensagem=(
            "OCR rápido + estruturado em páginas candidatas"
            if force_ocr and fast_ocr
            else "OCR forçado estruturado completo"
            if force_ocr
            else "OCR híbrido"
        ),
    )
    try:
        def on_progress(msg: str):
            database.update_job(ocr_job, "rodando", mensagem=msg)
        if fast_ocr and force_ocr:
            ocr = extrair_texto_rapido_com_estruturado_candidato(download.caminho, on_progress=on_progress)
        else:
            ocr = extrair_texto(download.caminho, force_ocr=force_ocr, on_progress=on_progress)
        ocr_status = "aviso" if ocr.avisos else "concluido"
        ocr_mensagem = f"{len(ocr.paginas)} página(s), {len(ocr.texto_completo)} caracteres"
        if ocr.avisos:
            ocr_mensagem += " | " + "; ".join(ocr.avisos)
        database.update_job(
            ocr_job,
            ocr_status,
            mensagem=ocr_mensagem,
        )

        detectar_job = database.start_job(
            "detectando publicações",
            titulo=edicao.titulo,
            edicao_id=download.edicao_id,
        )
        resultado = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
        database.insert_mencoes(download.edicao_id, resultado.mencoes_db)
        database.insert_publicacoes(download.edicao_id, resultado.publicacoes)
        database.salvar_arquivos_atos_locais(ocr.texto_path, resultado.publicacoes)
        database.update_ocr(download.edicao_id, ocr.texto_path, resultado.encontrado)
        database.update_job(
            detectar_job,
            "concluido",
            mensagem=(
                f"{len(resultado.publicacoes)} publicação(ões), "
                f"{len(resultado.mencoes_db)} menção(ões)"
            ),
        )
        if resultado.encontrado:
            notify_job = database.start_job(
                "notificando",
                titulo=edicao.titulo,
                edicao_id=download.edicao_id,
            )
            notificar(resultado, edicao)
            database.update_job(notify_job, "concluido", mensagem="Alerta emitido")
        logger.info(
            "Edição processada: id=%s tem_inaja=%s",
            download.edicao_id,
            resultado.encontrado,
        )
    except Exception:
        logger.exception("Falha ao processar OCR/detecção da edição %s", edicao.url)
        database.update_job(
            ocr_job,
            "erro",
            mensagem=f"Falha ao processar OCR/detecção: {edicao.url}",
        )


def executar_ciclo(
    force_rescan: bool = False,
    process_all: bool = False,
    force_ocr: bool = False,
    fast_ocr: bool = True,
) -> None:
    database.init_db()
    if force_rescan:
        logger.warning("Reprocessamento forçado ativado; limpando status anterior.")
        database.reset_processing()

    try:
        listar_job = database.start_job("verificando novas edições", mensagem=SETTINGS.site_url)
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
        _processar_edicao(edicao, force_ocr=force_ocr, fast_ocr=fast_ocr)

    if process_all:
        for row in database.get_pending_edicoes(process_all=True):
            caminho = row["caminho_local"]
            if not caminho or not Path(caminho).exists():
                continue
            edicao = Edicao(
                url=row["url"],
                titulo=row["titulo"] or f"Edição {row['id']}",
                data_publicacao=row["data_publicacao"],
            )
            _processar_edicao(edicao, force_ocr=force_ocr, fast_ocr=fast_ocr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor automático de edições do O Regional Jornal."
    )
    parser.add_argument("--once", action="store_true", help="Executa um ciclo e encerra.")
    parser.add_argument(
        "--force-rescan",
        action="store_true",
        help="Força nova varredura e reprocessamento das edições conhecidas.",
    )
    parser.add_argument(
        "--process-all",
        action="store_true",
        help="Tenta processar todas as edições registradas com PDF local.",
    )
    parser.add_argument(
        "--notify-test",
        action="store_true",
        help="Envia uma notificação de teste ou grava em ./alertas.",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Força OCR com Tesseract em todas as páginas, mesmo com texto embutido.",
    )
    parser.add_argument(
        "--full-structured-ocr",
        action="store_true",
        help="Usa OCR estruturado em todas as páginas; mais lento, útil para auditoria manual.",
    )
    return parser.parse_args()


def main() -> None:
    configurar_logging()
    args = parse_args()
    database.init_db()

    if args.notify_test:
        enviar_teste()
        return

    if args.once or args.force_rescan or args.process_all:
        executar_ciclo(
            force_rescan=args.force_rescan,
            process_all=args.process_all,
            force_ocr=args.force_ocr,
            fast_ocr=not args.full_structured_ocr,
        )
        return

    logger.info(
        "Agendando verificação a cada %s hora(s).",
        SETTINGS.check_interval_hours,
    )
    executar_ciclo()
    schedule.every(SETTINGS.check_interval_hours).hours.do(executar_ciclo)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
