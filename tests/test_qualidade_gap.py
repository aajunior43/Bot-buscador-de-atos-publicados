# -*- coding: utf-8 -*-
"""Testes PR4 — merge multi-key e gap."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import qualidade


def test_merge_k2_preserva_feedback_com_numero_corrigido():
    snapshot = [
        {
            "pagina": 3,
            "tipo": "Extrato de Contrato",
            "numero": "095/2036",
            "trecho": "EXTRATO DO CONTRATO Nº 095/2036 MUNICIPIO " + ("x" * 80),
            "resumo_ia": "Resumo antigo",
            "feedback": "correto",
            "feedback_em": "2026-07-01",
            "ia_tentativas": 2,
            "orgao": "Prefeitura Municipal de Inajá",
        }
    ]
    novas = [
        {
            "pagina": 3,
            "tipo": "Extrato de Contrato",
            "numero": "095/2026",  # corrigido
            "trecho": "EXTRATO DO CONTRATO Nº 095/2036 MUNICIPIO " + ("x" * 80),
            "resumo_ia": None,
            "orgao": "Prefeitura Municipal de Inajá",
        }
    ]
    out = qualidade.aplicar_merge_reprocess(novas, snapshot)
    assert out[0]["feedback"] == "correto"
    assert out[0]["numero"] == "095/2026"
    assert out[0]["resumo_ia"] == "Resumo antigo"
    assert out[0]["ia_tentativas"] == 2


def test_diagnosticar_gap_under(tmp_path, mock_settings):
    pdf = tmp_path / "ed.pdf"
    txt = tmp_path / "ed.txt"
    pdf.write_bytes(b"%PDF-1.4")
    # muitos hits, 1 pub
    body = "\n".join(
        [
            "PREFEITURA MUNICIPAL DE INAJA",
            "MUNICIPIO DE INAJA ato 1",
            "inaja inaja inaja inaja",
            "CAMARA MUNICIPAL DE INAJA",
        ]
    )
    txt.write_text(body, encoding="utf-8")
    ed = {"caminho_local": str(pdf), "texto_extraido_path": str(txt)}
    with patch("qualidade_gap.SETTINGS", mock_settings):
        d = qualidade.diagnosticar_gap_edicao(ed, n_pub=1, mode="reprocess", min_hits=3)
    assert d["hits"] >= 3
    assert d["severidade"] in ("under", "low", "critical")
    assert d["severidade"] != "none"


def test_diagnosticar_gap_none(tmp_path, mock_settings):
    txt = tmp_path / "ed.txt"
    txt.write_text("sem mencao relevante", encoding="utf-8")
    ed = {"caminho_local": str(txt), "texto_extraido_path": str(txt)}
    with patch("qualidade_gap.SETTINGS", mock_settings):
        d = qualidade.diagnosticar_gap_edicao(ed, n_pub=5, mode="reprocess")
    assert d["severidade"] == "none"


def test_avaliar_e_persistir_gap(db, mock_settings, tmp_path):
    import database

    database.init_db()
    pdf = tmp_path / "j.pdf"
    txt = tmp_path / "j.txt"
    pdf.write_bytes(b"%PDF")
    txt.write_text(
        "PREFEITURA MUNICIPAL DE INAJA\ninaja inaja inaja\nMUNICIPIO DE INAJA",
        encoding="utf-8",
    )
    eid = database.insert_or_get_edicao(
        "https://ex.com/gap.pdf", "Gap", "2026-06-04"
    )
    with database.connect() as c:
        c.execute(
            "UPDATE edicoes SET caminho_local=?, texto_extraido_path=?, tem_inaja=1 WHERE id=?",
            (str(pdf), str(txt), eid),
        )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Portaria",
                "numero": "1/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "x" * 40,
            }
        ],
    )
    with patch("qualidade_gap.SETTINGS", mock_settings), patch(
        "qualidade.SETTINGS", mock_settings
    ):
        diag = qualidade.avaliar_e_persistir_gap(eid)
    assert diag is not None
    with database.connect() as c:
        row = dict(c.execute("SELECT * FROM edicoes WHERE id=?", (eid,)).fetchone())
    assert row.get("gap_avaliado_em")
    assert row.get("gap_severidade") is not None


def test_resumo_operacional(db, mock_settings):
    import database

    database.init_db()
    with patch("qualidade.SETTINGS", mock_settings), patch(
        "qualidade_gap.SETTINGS", mock_settings
    ):
        r = qualidade.resumo_operacional()
    assert "fila_re_ia" in r
    assert "confianca_revisar" in r
