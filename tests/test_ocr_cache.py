# -*- coding: utf-8 -*-
"""Cache OCR: legibilidade de versões antigas e fallback."""
from __future__ import annotations

import json
from pathlib import Path

from ocr.cache import _carregar_cache_ocr
from ocr.models import PageText, TextBlock
from detector import _segmentos_fallback_texto, _chave_pub_dedup


def test_cache_v1_sem_versao_carrega(tmp_path: Path):
    pdf = tmp_path / "ed.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (tmp_path / "ed.ocr.json").write_text(
        json.dumps(
            {
                "texto_completo": "PREFEITURA MUNICIPAL DE INAJA",
                "avisos": [],
                "paginas": [
                    {
                        "pagina": 1,
                        "texto": "PREFEITURA MUNICIPAL DE INAJA\nDECRETO Nº 1/2026",
                        "metodo": "ocr",
                        "blocks": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    r = _carregar_cache_ocr(pdf)
    assert r is not None
    assert len(r.paginas) == 1
    assert "INAJA" in r.paginas[0].texto


def test_fallback_segmentos_encontra_cabecalho():
    pagina = PageText(
        pagina=3,
        texto=(
            "OUTRO TEXTO\n"
            "PREFEITURA MUNICIPAL DE INAJÁ\n"
            "ESTADO DO PARANÁ\n"
            "DECRETO Nº 10/2026\n"
            "Dispõe sobre obras no município.\n" * 5
        ),
        metodo="ocr",
        blocks=[],
    )
    segs = _segmentos_fallback_texto(pagina)
    assert segs
    assert any("INAJ" in (s.texto or "").upper() for s in segs)


def test_chave_dedup():
    a = {"pagina": 1, "tipo": "Decreto", "numero": "1/2026", "orgao": "Prefeitura"}
    b = {"pagina": 1, "tipo": "Decreto", "numero": "1/2026", "orgao": "Prefeitura Municipal"}
    # mesmo número+página+tipo → mesma base de número
    assert "1/2026" in _chave_pub_dedup(a)
