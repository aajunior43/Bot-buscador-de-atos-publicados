# -*- coding: utf-8 -*-
"""Testes das helpers do menu CLI (sem I/O interativo)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MENU_PATH = ROOT / "scripts" / "_menu_cli.py"


@pytest.fixture(scope="module")
def menu():
    spec = importlib.util.spec_from_file_location("menu_cli_under_test", MENU_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_search_funcoes_encontra_ocr(menu):
    hits = menu.search_funcoes("ocr")
    assert hits
    # processar pendentes / force-ocr / invalidar
    assert any(k in hits for k in ("8", "O", "V", "HOJE"))


def test_search_funcoes_alerta_arquivo(menu):
    hits = menu.search_funcoes("alerta") or menu.search_funcoes("notific") or menu.search_funcoes("arquivo")
    assert hits or menu.search_funcoes("diagnostico")


def test_search_vazio(menu):
    assert menu.search_funcoes("") == []
    assert menu.search_funcoes("   ") == []


def test_normalize_aliases(menu):
    assert menu.normalize_op("hoje") == "HOJE"
    assert menu.normalize_op("!") == "HOJE"
    assert menu.normalize_op("diag") == "Z"
    assert menu.normalize_op("agente") == "AG"


def test_fmt_dur(menu):
    assert menu._fmt_dur(3.2).endswith("s")
    assert "m" in menu._fmt_dur(75)


def test_favorites_toggle(menu, tmp_path, monkeypatch):
    prefs = tmp_path / "menu_prefs.json"
    monkeypatch.setattr(menu, "_PREFS_PATH", prefs)
    # reset
    prefs.write_text(
        json.dumps({"favorites": ["S", "U"], "compact": True}),
        encoding="utf-8",
    )
    assert menu.get_favorites() == ["S", "U"]
    msg = menu.toggle_favorite("Z")
    assert "Adicionado" in msg
    assert "Z" in menu.get_favorites()
    msg2 = menu.toggle_favorite("Z")
    assert "Removido" in msg2
    assert "Z" not in menu.get_favorites()


def test_default_favorites_include_hoje(menu):
    assert "HOJE" in menu._DEFAULT_FAVORITES
    assert "HOJE" in menu.FUNCOES
    assert "HOJE" in menu.ACOES


def test_status_snapshot_com_db(menu, db):
    snap = menu._status_snapshot()
    assert snap.get("ok") is True
    assert "pend" in snap
    assert "pubs" in snap
    assert "tg" not in snap
