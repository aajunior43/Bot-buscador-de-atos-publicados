# -*- coding: utf-8 -*-
"""Testes do agente de vigilância (sem OCR real)."""
from __future__ import annotations

from unittest.mock import patch


def test_modo_efetivo_e_set(db):
    from agente import modo_efetivo, set_agente_modo, set_agente_ativo, agente_esta_ativo

    set_agente_ativo(True)
    assert agente_esta_ativo() is True
    set_agente_modo("escudo")
    assert modo_efetivo() == "escudo"
    set_agente_modo("auto")
    assert modo_efetivo() == "auto"
    set_agente_ativo(False)
    assert agente_esta_ativo() is False
    set_agente_ativo(True)


def test_run_pulse_escudo(db):
    from agente import set_agente_modo, set_agente_ativo, run_pulse

    set_agente_ativo(True)
    set_agente_modo("escudo")
    res = run_pulse(force=True)
    assert res.ciclo == "pulse"
    assert res.modo == "escudo"
    assert any(a.acao == "estado" for a in res.acoes)


def test_run_cerebro_sentinela(db):
    from agente import set_agente_modo, set_agente_ativo, run_cerebro

    set_agente_ativo(True)
    set_agente_modo("sentinela")
    res = run_cerebro(force=True)
    assert res.ciclo == "cerebro"
    assert any(a.acao == "skip" for a in res.acoes)


def test_run_cerebro_skip_ocr_quando_lock_held(db):
    """Cérebro não inicia OCR se o lock OS estiver em uso."""
    from agente import set_agente_modo, set_agente_ativo, run_cerebro

    set_agente_ativo(True)
    set_agente_modo("formiga")
    with patch("agente.is_lock_held", return_value=True), patch(
        "agente.lock_status",
        return_value={
            "path": "x",
            "exists": True,
            "held": True,
            "age_min": 1.0,
            "holder": "1:edicao:teste",
        },
    ), patch("agente.lock_holder_text", return_value="1:edicao:teste"):
        res = run_cerebro(force=True)
    assert res.ciclo == "cerebro"
    assert any(a.acao == "skip_ocr" for a in res.acoes)
    assert not any(a.acao == "ocr" for a in res.acoes)


def test_tick_from_bot_adia_cerebro_se_lock(db, monkeypatch):
    from agente import set_agente_ativo, set_agente_modo, tick_from_bot
    import agente as ag

    set_agente_ativo(True)
    set_agente_modo("formiga")
    # SETTINGS é frozen dataclass — usar object.__setattr__
    object.__setattr__(ag.SETTINGS, "agente_no_bot", True)
    called = {"cerebro": 0}

    def fake_cerebro(**kwargs):
        called["cerebro"] += 1
        return ag.CicloResultado(ciclo="cerebro", modo="formiga")

    with patch.object(ag, "deve_rodar_pulse", return_value=False), patch.object(
        ag, "deve_rodar_cerebro", return_value=True
    ), patch.object(ag, "is_lock_held", return_value=True), patch.object(
        ag, "run_cerebro", side_effect=fake_cerebro
    ), patch.object(ag, "lock_status", return_value={
        "path": "x", "exists": True, "held": True, "age_min": 0.5, "holder": "9:x"
    }):
        tick_from_bot()
    assert called["cerebro"] == 0


def test_status_e_log(db):
    from agente import (
        set_agente_ativo,
        set_agente_modo,
        run_pulse,
        status_agente,
        listar_log,
        log_acao,
    )

    set_agente_ativo(True)
    set_agente_modo("escudo")
    run_pulse(force=True)
    log_acao(ciclo="pulse", modo="escudo", acao="teste", detalhe="unit")
    st = status_agente()
    assert st["ativo"] is True
    assert "modo_config" in st
    rows = listar_log(5)
    assert isinstance(rows, list)
