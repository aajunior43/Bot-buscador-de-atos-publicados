"""Testes da pasta organizada de atos."""
from __future__ import annotations

from pathlib import Path

import atos_arquivo


def test_slugify_windows_safe():
    s = atos_arquivo.slugify('Prefeitura Municipal de Inajá / "Centro"')
    assert "<" not in s and ">" not in s and '"' not in s
    assert "/" not in s and "\\" not in s
    assert "Inaja" in s or "inaja" in s.lower() or "Inaj" in s


def test_parse_data():
    assert atos_arquivo._parse_data_edicao("2026-07-09") == ("2026", "07", "09")
    assert atos_arquivo._parse_data_edicao("09/07/2026") == ("2026", "07", "09")
    assert atos_arquivo._parse_data_edicao(None)[0] == "sem-data"


def test_espelhar_edicao_cria_arvore(tmp_path, mock_settings, db):
    import database

    object.__setattr__(mock_settings, "atos_dir", tmp_path / "atos")
    object.__setattr__(mock_settings, "atos_espelhar", True)
    # atos_arquivo usa SETTINGS do config — rebind no módulo
    import atos_arquivo as aa
    import config as cfg

    object.__setattr__(cfg.SETTINGS, "atos_dir", tmp_path / "atos")
    object.__setattr__(cfg.SETTINGS, "atos_espelhar", True)
    object.__setattr__(cfg.SETTINGS, "atos_por_tipo", True)
    object.__setattr__(cfg.SETTINGS, "atos_por_orgao", True)

    eid = database.insert_or_get_edicao(
        "https://ex.com/j.pdf", "Jornal 09-07-2026", "2026-07-09"
    )
    pubs = [
        {
            "id": 1,
            "tipo": "Decreto",
            "numero": "042/2026",
            "orgao": "Prefeitura Municipal de Inajá",
            "pagina": 4,
            "valor": "R$ 1.000,00",
            "resumo_ia": "Teste de decreto",
            "trecho": "DECRETO Nº 042/2026 ...",
            "data_documento": "08/07/2026",
        },
        {
            "id": 2,
            "tipo": "Portaria",
            "numero": "10/2026",
            "orgao": "Câmara Municipal de Inajá",
            "pagina": 5,
            "trecho": "PORTARIA ...",
        },
    ]
    n = aa.espelhar_edicao(
        eid,
        pubs,
        edicao_meta={
            "id": eid,
            "titulo": "Jornal 09-07-2026",
            "data_publicacao": "2026-07-09",
            "url": "https://ex.com/j.pdf",
        },
        root=tmp_path / "atos",
    )
    assert n == 2
    dia = tmp_path / "atos" / "por-data" / "2026" / "07" / "09"
    assert dia.is_dir()
    mds = list(dia.glob("*.md"))
    assert any("_indice-dia" in p.name for p in mds)
    assert len([p for p in mds if not p.name.startswith("_")]) == 2
    assert (tmp_path / "atos" / "INDICE.md").exists()
    assert (tmp_path / "atos" / "por-tipo").exists()
    assert (tmp_path / "atos" / "por-orgao").exists()
    # Conteúdo legível
    algum = next(p for p in mds if p.name.startswith("01_"))
    text = algum.read_text(encoding="utf-8")
    assert "Decreto" in text
    assert "Prefeitura" in text or "Inaja" in text or "Inajá" in text


def test_exportar_midia_pagina(tmp_path, mock_settings, monkeypatch):
    import atos_arquivo as aa
    import config as cfg
    from PIL import Image

    object.__setattr__(cfg.SETTINGS, "atos_exportar_midia", True)
    object.__setattr__(cfg.SETTINGS, "atos_pagina_dpi", 72)
    object.__setattr__(cfg.SETTINGS, "poppler_path", "")

    # PDF mínimo válido
    try:
        from pypdf import PdfWriter

        pdf = tmp_path / "ed.pdf"
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        with open(pdf, "wb") as f:
            w.write(f)
    except Exception:
        return  # ambiente sem pypdf — skip

    dest = tmp_path / "ato01"

    def fake_convert(*a, **k):
        return [Image.new("RGB", (100, 140), color=(255, 255, 255))]

    monkeypatch.setattr(
        "pdf2image.convert_from_path", fake_convert
    )
    r = aa.exportar_midia_pagina(pdf, 1, dest)
    assert r["pdf"] is True
    assert dest.with_suffix(".pdf").exists()
    assert r["png"] is True
    assert dest.with_suffix(".png").exists()


def test_reconstruir_do_banco(tmp_path, mock_settings, db):
    import database
    import atos_arquivo as aa
    import config as cfg

    object.__setattr__(cfg.SETTINGS, "atos_dir", tmp_path / "atos")
    object.__setattr__(cfg.SETTINGS, "atos_espelhar", True)
    object.__setattr__(cfg.SETTINGS, "atos_por_tipo", True)
    object.__setattr__(cfg.SETTINGS, "atos_por_orgao", True)

    eid = database.insert_or_get_edicao(
        "https://ex.com/r.pdf", "Rebuild", "2026-06-15"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Lei",
                "numero": "1/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "LEI 1",
                "assunto": "x",
            }
        ],
    )
    stats = aa.reconstruir_tudo_do_banco(root=tmp_path / "atos", limpar=True)
    assert stats["atos"] >= 1
    assert (tmp_path / "atos" / "por-data" / "2026" / "06" / "15").exists()
