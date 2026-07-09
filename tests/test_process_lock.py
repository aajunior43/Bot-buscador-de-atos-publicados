"""Testes do lock de processamento concorrente."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from process_lock import ProcessLockError, process_lock


def test_lock_exclusivo(tmp_path: Path):
    lock_file = tmp_path / "t.lock"
    acquired_second = []

    def other():
        try:
            with process_lock(lock_file, timeout=0.3, poll=0.05, label="other"):
                acquired_second.append(True)
        except ProcessLockError:
            acquired_second.append(False)

    with process_lock(lock_file, timeout=2.0, label="main"):
        t = threading.Thread(target=other)
        t.start()
        t.join(timeout=3)
        assert acquired_second == [False]

    # Após liberar, consegue adquirir
    with process_lock(lock_file, timeout=1.0, label="after"):
        pass
