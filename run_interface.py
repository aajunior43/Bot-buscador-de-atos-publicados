from __future__ import annotations

import logging
import os
import sys
import webbrowser


class FiltroRuido(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/api/atividade" in msg:
            return False
        if "/favicon.ico" in msg:
            return False
        if "/static/" in msg:
            return False
        return True


class FormatadorLimpo(logging.Formatter):
    CORES = {
        "INFO": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "CRITICAL": "\033[91m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        cor = self.CORES.get(record.levelname, "")
        nivel = f"{cor}{record.levelname:<8}{self.RESET}" if cor else record.levelname
        msg = record.getMessage()
        if "GET " in msg or "POST " in msg:
            partes = msg.split(" - ")
            if len(partes) >= 2:
                rota = partes[-1].strip('"').split(" ")[1] if '"' in partes[-1] else ""
                status_parts = partes[-1].split(" ")
                status = status_parts[-1] if status_parts else ""
                return f"  {nivel} {rota:<40} {status}"
        return f"{nivel} {msg}"


def configurar_logging() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

    fmt = FormatadorLimpo("%(asctime)s %(levelname)s %(message)s")
    filtro = FiltroRuido()

    for nome in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(nome)
        logger.handlers.clear()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        handler.addFilter(filtro)
        logger.handlers.append(handler)
        logger.propagate = False


def main() -> None:
    configurar_logging()
    print("=" * 60)
    print("  Monitor de Atos - Interface Web")
    print("  Acesse: http://localhost:8001")
    print("=" * 60)
    print()

    # Abre o navegador automaticamente (desativar com ABRIR_NAVEGADOR=0)
    if os.getenv("ABRIR_NAVEGADOR", "1").strip() in {"1", "true", "sim", "yes", "on"}:
        try:
            webbrowser.open("http://localhost:8001", new=2, autoraise=True)
        except Exception:
            pass

    import uvicorn

    reload = os.getenv("DEV_RELOAD", "1").strip().casefold() in {"1", "true", "sim", "yes", "on"}
    uvicorn.run(
        "webapp:app",
        host="0.0.0.0",
        port=8001,
        log_level="info",
        access_log=True,
        reload=reload,
        reload_delay=1.5,
        reload_excludes=[
            "tests/*",
            "*.db",
            "*.db-*",
            "agent-tools/*",
            "terminals/*",
            ".omo/*",
            "logs/*",
            "alertas/*",
            "edicoes/*",
            "atos/*",
            "exportacoes/*",
            "relatorios/*",
            "scripts/__pycache__/*",
            "_tmp_*.py",
            "temp_*.py",
        ],
    )


if __name__ == "__main__":
    main()
