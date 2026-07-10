"""Lock de arquivo para evitar OCR/processamento concorrente (CLI + webapp)."""
from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import SETTINGS

logger = logging.getLogger(__name__)

DEFAULT_LOCK = Path(os.getenv("PROCESS_LOCK_PATH", str(SETTINGS.log_dir / "processamento.lock")))


class ProcessLockError(RuntimeError):
    """Não foi possível adquirir o lock a tempo."""


def lock_age_minutes(path: Path | None = None) -> float | None:
    """Idade do arquivo de lock em minutos (mtime), ou None se não existir."""
    lock_path = path or DEFAULT_LOCK
    if not lock_path.exists():
        return None
    try:
        mtime = lock_path.stat().st_mtime
        return max(0.0, (time.time() - mtime) / 60.0)
    except OSError:
        return 0.0


def lock_holder_text(path: Path | None = None) -> str:
    """Conteúdo textual do lock (``pid:label``), se legível."""
    lock_path = path or DEFAULT_LOCK
    if not lock_path.exists():
        return ""
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="replace").strip()
        return raw.splitlines()[0].strip() if raw else ""
    except OSError:
        return ""


def is_lock_held(path: Path | None = None) -> bool:
    """True se outro processo/handle detém o lock OS (não apenas se o arquivo existe).

    O arquivo ``processamento.lock`` permanece no disco após o unlock; usar
    ``exists()`` sozinho gera falso positivo. Esta função faz probe non-blocking.
    """
    lock_path = path or DEFAULT_LOCK
    if not lock_path.exists():
        return False
    try:
        fh = open(lock_path, "r+b")
    except OSError:
        # Sem permissão / race de remoção — trate como não bloqueante
        return False
    try:
        try:
            fh.seek(0)
            if fh.read(1) == b"":
                # Arquivo vazio residual: não está em uso
                return False
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False
        except OSError:
            return True
    finally:
        try:
            fh.close()
        except OSError:
            pass


def lock_status(path: Path | None = None) -> dict[str, Any]:
    """Resumo para logs/UI: held, age, holder."""
    lock_path = path or DEFAULT_LOCK
    age = lock_age_minutes(lock_path)
    holder = lock_holder_text(lock_path)
    held = is_lock_held(lock_path)
    return {
        "path": str(lock_path),
        "exists": lock_path.exists(),
        "held": held,
        "age_min": age,
        "holder": holder,
    }


@contextmanager
def process_lock(
    path: Path | None = None,
    *,
    timeout: float = 600.0,
    poll: float = 0.5,
    label: str = "processamento",
) -> Iterator[None]:
    """Adquire lock exclusivo; bloqueia até ``timeout`` segundos.

    Windows: ``msvcrt.locking``. Unix: ``fcntl.flock``.
    """
    lock_path = path or DEFAULT_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    acquired = False
    deadline = time.time() + max(0.0, timeout)
    try:
        while True:
            try:
                fh.seek(0)
                if fh.read(1) == b"":
                    fh.write(b"\0")
                    fh.flush()
                fh.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                fh.seek(0)
                fh.truncate()
                fh.write(f"{os.getpid()}:{label}\n".encode("utf-8", errors="replace"))
                fh.flush()
                logger.debug("Lock adquirido (%s) pid=%s", lock_path, os.getpid())
                break
            except OSError:
                if time.time() >= deadline:
                    raise ProcessLockError(
                        f"Timeout ao aguardar lock de {label} ({lock_path}). "
                        "Outro processo (web ou main) pode estar processando."
                    ) from None
                time.sleep(poll)
        yield
    finally:
        if acquired:
            try:
                fh.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()
