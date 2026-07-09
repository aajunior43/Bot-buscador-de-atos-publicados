"""Lock de arquivo para evitar OCR/processamento concorrente (CLI + webapp)."""
from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import SETTINGS

logger = logging.getLogger(__name__)

DEFAULT_LOCK = Path(os.getenv("PROCESS_LOCK_PATH", str(SETTINGS.log_dir / "processamento.lock")))


class ProcessLockError(RuntimeError):
    """Não foi possível adquirir o lock a tempo."""


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
