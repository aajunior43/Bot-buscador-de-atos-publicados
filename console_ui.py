"""Terminal rico para observar o BOT em tempo real.

- Cores ANSI (Windows 10+ / terminais modernos)
- Banners de edição, barras de progresso OCR, cards de resumo
- Contadores de sessão (processadas, Inajá, falhas, tempo)
- Formatter de logging colorido para o console (arquivo permanece limpo)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime


# ── ANSI ────────────────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_RED = "\033[41m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_DARK = "\033[48;5;236m"


_NO_COLOR = bool(os.getenv("NO_COLOR", "").strip())
_ENABLED = sys.stdout.isatty() and not _NO_COLOR
_WIDTH = 72
_lock = threading.Lock()
_last_progress_line = ""


def enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _c(code: str, text: str) -> str:
    if not _ENABLED:
        return text
    return f"{code}{text}{C.RESET}"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _line(char: str = "─", width: int = _WIDTH) -> str:
    return char * width


def bar(current: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "░" * width
    pct = max(0.0, min(1.0, current / total))
    filled = int(round(pct * width))
    return "█" * filled + "░" * (width - filled)


def pct(current: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round(100 * current / total))


def _emit(text: str = "", *, end: str = "\n", flush: bool = True) -> None:
    """Escreve no stdout sem passar pelo logging (evita formatação dupla)."""
    with _lock:
        sys.stdout.write(text + end)
        if flush:
            sys.stdout.flush()


def rule(title: str = "", color: str = C.BRIGHT_CYAN) -> None:
    if title:
        pad = max(0, _WIDTH - len(title) - 4)
        left = pad // 2
        right = pad - left
        body = f"{'─' * left} {title} {'─' * right}"
        _emit(_c(color + C.BOLD, body[:_WIDTH]))
    else:
        _emit(_c(C.DIM, _line()))


def banner_startup(
    *,
    interval_h: int,
    continuo: bool,
    lote: int,
    max_ciclo: int,
    dias: int,
    desde: str,
    max_falhas: int,
) -> None:
    enable_windows_ansi()
    _emit()
    rule("MONITOR INAJÁ · BOT", C.BRIGHT_MAGENTA)
    _emit(_c(C.BOLD + C.BRIGHT_WHITE, "  Rastreador de atos · O Regional Jornal"))
    _emit(_c(C.DIM, f"  {_now()} · saída colorida em tempo real"))
    _emit()
    rows = [
        ("Ciclo completo", f"a cada {interval_h}h"),
        ("Fila contínua", "SIM ✓" if continuo else "não"),
        ("Lote / máx ciclo", f"{lote} / {max_ciclo}"),
        ("Janela dias", str(dias) if dias else "sem limite"),
        ("Desde", desde or "sem piso"),
        ("Quarentena", f"após {max_falhas} falhas"),
    ]
    for k, v in rows:
        _emit(f"  {_c(C.CYAN, '▸')} {_c(C.DIM, k + ':')} {_c(C.BRIGHT_WHITE, v)}")
    rule(color=C.BRIGHT_MAGENTA)
    _emit()


@dataclass
class SessionStats:
    started_at: float = field(default_factory=time.time)
    processadas: int = 0
    com_inaja: int = 0
    publicacoes: int = 0
    falhas: int = 0
    cache_hits: int = 0
    segundos_ocr: float = 0.0
    ultima_edicao: str = ""

    def elapsed(self) -> str:
        secs = int(time.time() - self.started_at)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h{m:02d}m"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"


SESSION = SessionStats()


def status_fila(
    *,
    pendentes: int | None = None,
    fila: int | None = None,
    quarentena: int | None = None,
    quiet: bool = False,
) -> None:
    if quiet:
        return
    bits = [
        f"⏱  sessão {SESSION.elapsed()}",
        f"✓ {SESSION.processadas} ok",
        f"🏛 {SESSION.com_inaja} Inajá",
        f"📄 {SESSION.publicacoes} pubs",
    ]
    if SESSION.falhas:
        bits.append(f"⚠ {SESSION.falhas} falhas")
    if pendentes is not None:
        bits.append(f"📋 {pendentes} pend.")
    if fila is not None:
        bits.append(f"⏭ {fila} na fila")
    if quarentena:
        bits.append(f"🚫 {quarentena} quar.")
    line = "  ·  ".join(bits)
    _emit(_c(C.DIM, f"  [{_now()}] {line}"))


def edition_start(
    *,
    titulo: str,
    data: str | None,
    edicao_id: int | None,
    indice: int | None = None,
    total_lote: int | None = None,
    pendentes_restantes: int | None = None,
) -> float:
    """Banner de início de edição. Retorna timestamp para medir duração."""
    global _last_progress_line
    _last_progress_line = ""
    t0 = time.time()
    SESSION.ultima_edicao = titulo or (f"id={edicao_id}" if edicao_id else "?")
    _emit()
    rule("NOVA EDIÇÃO", C.BRIGHT_BLUE)
    lote = ""
    if indice and total_lote:
        lote = f"  {_c(C.YELLOW, f'[{indice}/{total_lote}]')}"
    _emit(
        f"  {_c(C.BOLD + C.BRIGHT_WHITE, titulo or 'Sem título')}"
        f"{lote}"
    )
    meta = []
    if data:
        meta.append(f"📅 {data}")
    if edicao_id:
        meta.append(f"id={edicao_id}")
    if pendentes_restantes is not None:
        meta.append(f"restam ~{pendentes_restantes} na fila")
    if meta:
        _emit(_c(C.DIM, "  " + "  ·  ".join(meta)))
    _emit(_c(C.DIM, f"  iniciado {_now()}"))
    return t0


def step(name: str, detail: str = "", *, ok: bool | None = None) -> None:
    if ok is True:
        icon = _c(C.BRIGHT_GREEN, "✓")
    elif ok is False:
        icon = _c(C.BRIGHT_RED, "✗")
    else:
        icon = _c(C.BRIGHT_CYAN, "→")
    detail_s = f"  {_c(C.DIM, detail)}" if detail else ""
    _emit(f"  {icon} {_c(C.BOLD, name)}{detail_s}")


def progress(
    current: int,
    total: int,
    *,
    label: str = "OCR",
    extra: str = "",
) -> None:
    """Barra de progresso (uma linha, atualiza no mesmo lugar se possível)."""
    global _last_progress_line
    p = pct(current, total)
    b = bar(current, total, 22)
    color = C.BRIGHT_GREEN if p >= 100 else C.BRIGHT_CYAN
    extra_s = f"  {_c(C.DIM, extra)}" if extra else ""
    body = (
        f"  {_c(C.DIM, '│')} {_c(color, b)} "
        f"{_c(C.BOLD, f'{p:3d}%')}  "
        f"{_c(C.WHITE, f'{current}/{total}')}  "
        f"{_c(C.CYAN, label)}{extra_s}"
    )
    with _lock:
        # Quebra de linha normal: mais compatível com iniciar_tudo (pipe)
        if body != _last_progress_line:
            sys.stdout.write(body + "\n")
            sys.stdout.flush()
            _last_progress_line = body


def edition_end(
    *,
    ok: bool,
    tem_inaja: bool = False,
    n_pubs: int = 0,
    n_mencoes: int = 0,
    t0: float | None = None,
    erro: str = "",
    from_cache: bool = False,
) -> None:
    dur = ""
    if t0 is not None:
        secs = time.time() - t0
        SESSION.segundos_ocr += secs
        dur = f"{secs:.1f}s"
        if secs >= 60:
            dur = f"{int(secs // 60)}m{int(secs % 60):02d}s"

    if ok:
        SESSION.processadas += 1
        if tem_inaja:
            SESSION.com_inaja += 1
        SESSION.publicacoes += n_pubs
        if from_cache:
            SESSION.cache_hits += 1

        if tem_inaja:
            head = _c(C.BG_GREEN + C.BOLD + C.BLACK, " INAJÁ ")
            head += f"  {_c(C.BRIGHT_GREEN + C.BOLD, f'{n_pubs} publicação(ões)')}"
        else:
            head = _c(C.DIM, " sem Inajá")
            if n_mencoes:
                head += f"  {_c(C.YELLOW, f'{n_mencoes} menção(ões)')}"

        _emit(f"  {_c(C.BRIGHT_GREEN, '✓ CONCLUÍDA')}{head}")
        bits = []
        if dur:
            bits.append(f"⏱ {dur}")
        if from_cache:
            bits.append("💾 cache OCR")
        bits.append(f"sessão: {SESSION.processadas} ok · {SESSION.com_inaja} Inajá · {SESSION.publicacoes} pubs")
        _emit(_c(C.DIM, "  " + "  ·  ".join(bits)))
    else:
        SESSION.falhas += 1
        _emit(f"  {_c(C.BRIGHT_RED + C.BOLD, '✗ FALHOU')}" + (f"  {_c(C.RED, erro[:120])}" if erro else ""))
        if dur:
            _emit(_c(C.DIM, f"  ⏱ {dur}  ·  falhas sessão: {SESSION.falhas}"))
    rule(color=C.DIM)


def ciclo_banner(titulo: str, detalhe: str = "") -> None:
    _emit()
    rule(titulo, C.BRIGHT_YELLOW)
    if detalhe:
        _emit(_c(C.DIM, f"  {detalhe}"))


def idle_heartbeat(msg: str = "aguardando fila / próximo ciclo…") -> None:
    _emit(
        _c(
            C.DIM,
            f"  · [{_now()}] {msg}  ·  sessão {SESSION.elapsed()}  ·  "
            f"{SESSION.processadas} ok / {SESSION.com_inaja} Inajá / {SESSION.falhas} falhas",
        )
    )


class RichConsoleFormatter(logging.Formatter):
    """Formatter colorido para o console (arquivo usa Formatter plain)."""

    LEVEL_STYLES = {
        logging.DEBUG: (C.DIM, "DBG"),
        logging.INFO: (C.BRIGHT_CYAN, "INF"),
        logging.WARNING: (C.BRIGHT_YELLOW, "WRN"),
        logging.ERROR: (C.BRIGHT_RED, "ERR"),
        logging.CRITICAL: (C.BG_RED + C.BOLD + C.WHITE, "CRT"),
    }

    # Módulos → emoji curto
    MOD_ICON = {
        "pipeline": "⚙",
        "scraper": "🌐",
        "downloader": "⬇",
        "ocr.extractor": "👁",
        "ocr.tesseract": "🔤",
        "ocr.cache": "💾",
        "detector": "🔍",
        "ai_processor": "✦",
        "notifier": "📣",
        "database": "🗄",
        "__main__": "▶",
        "main": "▶",
    }

    # Mensagens muito verbosas de coluna → omitidas no console (ficam no arquivo)
    _SKIP_CONSOLE_SUBSTR = (
        "coluna(s) detectada(s)",
        "coluna ",
        "recuperado (psm",
        "ObjectCache",
        "LEAK!",
    )

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno <= logging.INFO:
            for s in self._SKIP_CONSOLE_SUBSTR:
                if s in msg:
                    return ""  # handler pode filtrar vazios

        style, tag = self.LEVEL_STYLES.get(
            record.levelno, (C.WHITE, record.levelname[:3])
        )
        name = record.name
        if name.startswith("ocr."):
            short = name
        else:
            short = name.split(".")[-1]
        icon = self.MOD_ICON.get(name) or self.MOD_ICON.get(short) or "·"
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        # Destaques semânticos no texto
        colored_msg = msg
        if _ENABLED:
            low = msg.lower()
            if "inajá" in low or "inaja" in low:
                colored_msg = _c(C.BRIGHT_GREEN, msg)
            elif "quarentena" in low:
                colored_msg = _c(C.BRIGHT_YELLOW + C.BOLD, msg)
            elif "falha" in low or "erro" in low:
                if record.levelno >= logging.WARNING:
                    colored_msg = _c(C.BRIGHT_RED, msg)
            elif "conclu" in low or "processada" in low:
                colored_msg = _c(C.BRIGHT_WHITE, msg)
            elif "cache" in low:
                colored_msg = _c(C.MAGENTA, msg)

        prefix = (
            f"{_c(C.DIM, ts)} "
            f"{_c(style + C.BOLD, tag)} "
            f"{icon} {_c(C.DIM, short)}"
        )
        return f"{prefix}  {colored_msg}"


class _SkipEmptyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Deixa o formatter decidir; se formatar vazio, ainda emite — tratamos no emit
        return True


class RichStreamHandler(logging.StreamHandler):
    """Não imprime linhas vazias (logs verbosos filtrados pelo formatter)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if not msg or not str(msg).strip():
                return
            self.stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def attach_rich_console(root_level: int = logging.INFO) -> None:
    """Troca o handler de console do root por um rico (mantém handlers de arquivo)."""
    enable_windows_ansi()
    root = logging.getLogger()
    # Remove StreamHandlers existentes apontando para stdout/stderr
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h, logging.FileHandler
        ):
            if getattr(h, "stream", None) in (sys.stdout, sys.stderr):
                root.removeHandler(h)
    console = RichStreamHandler(sys.stdout)
    console.setLevel(root_level)
    console.setFormatter(RichConsoleFormatter())
    root.addHandler(console)


def parse_progress_payload(msg: str | dict) -> tuple[int | None, int | None, str]:
    """Extrai (current, total, label) de callbacks OCR."""
    import re

    if isinstance(msg, dict):
        cur = msg.get("current")
        tot = msg.get("total")
        step = str(msg.get("step") or msg.get("msg") or "OCR")
        label = {
            "ocr_fast": "OCR rápido",
            "ocr_structured": "OCR estruturado",
            "ocr_completo": "OCR completo",
            "ocr": "OCR",
            "download": "Download",
            "detect": "Detecção",
            "ia": "IA",
        }.get(str(msg.get("step") or ""), str(step))
        try:
            return (
                int(cur) if cur is not None else None,
                int(tot) if tot is not None else None,
                label,
            )
        except (TypeError, ValueError):
            return None, None, label

    text = str(msg)
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        label = "OCR"
        if "rápido" in text.lower() or "rapido" in text.lower():
            label = "OCR rápido"
        elif "estrutur" in text.lower():
            label = "OCR estruturado"
        elif "pdfplumber" in text.lower():
            label = "Texto nativo"
        elif "candidat" in text.lower():
            label = "Candidatas"
        return int(m.group(1)), int(m.group(2)), label
    return None, None, text[:40]
