from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler

import schedule

import database
from config import SETTINGS
from notifier import enviar_teste
from pipeline import executar_ciclo, processar_pendentes_automatico


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
    database.registrar_heartbeat_bot()

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
        "Agendando verificação a cada %s hora(s). Heartbeat a cada 30s. "
        "Fila contínua=%s (lote=%s, máx/ciclo=%s, dias=%s, desde=%s).",
        SETTINGS.check_interval_hours,
        SETTINGS.auto_process_continuo,
        SETTINGS.auto_process_limit,
        SETTINGS.auto_process_max_por_ciclo,
        SETTINGS.auto_process_dias,
        SETTINGS.auto_process_desde or "sem piso",
    )
    executar_ciclo()
    schedule.every(SETTINGS.check_interval_hours).hours.do(executar_ciclo)
    while True:
        try:
            database.registrar_heartbeat_bot()
        except Exception:
            logger.debug("Falha ao gravar heartbeat do BOT", exc_info=True)
        schedule.run_pending()

        # Entre ciclos: esvazia a fila de OCR lote a lote (sem esperar 6h)
        if SETTINGS.auto_process and SETTINGS.auto_process_continuo:
            try:
                n = processar_pendentes_automatico(
                    force_ocr=True,
                    fast_ocr=True,
                    # Um lote por vez no idle; o próximo loop pega o seguinte
                    max_total=SETTINGS.auto_process_limit,
                    lotes=False,
                    quiet=True,
                )
                if n > 0:
                    # Continua na hora se ainda houver trabalho
                    continue
            except Exception:
                logger.exception("Falha no processamento contínuo da fila.")

        time.sleep(30)


if __name__ == "__main__":
    main()
