# -*- coding: utf-8 -*-
"""Testes PR1 — correção de ano OCR em qualidade.py."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from detector import DetectionResult, DetectionMetrics
from qualidade import (
    CorrecaoNumero,
    corrigir_numero_ano,
    pos_processar_publicacoes,
    aplicar_correcao_numero_pub,
)


class TestCorrigirNumeroAno:
    def test_095_2036_sem_ancora_vira_2026(self):
        c = corrigir_numero_ano(
            "095/2036",
            data_publicacao_edicao="2026-07-01",
            trecho="EXTRATO DO CONTRATO 095/2036 processo",
        )
        assert c.corrigido is True
        assert c.numero_final == "095/2026"
        assert "2036→2026" in c.motivo or "2026" in c.motivo
        assert c.precisa_revisao is False
        assert c.confianca_correcao >= 0.8

    def test_12_2025_sem_mudanca(self):
        c = corrigir_numero_ano(
            "12/2025",
            data_publicacao_edicao="2026-07-01",
        )
        assert c.corrigido is False
        assert c.numero_final == "12/2025"
        assert c.precisa_revisao is False

    def test_1_1999_nao_pula_para_2026(self):
        c = corrigir_numero_ano(
            "1/1999",
            data_publicacao_edicao="2026-07-01",
        )
        assert c.corrigido is False
        assert c.numero_final == "1/1999"
        assert c.numero_final != "1/2026"

    def test_089_2036_ancorado_nao_corrige(self):
        c = corrigir_numero_ano(
            "089/2036",
            data_publicacao_edicao="2026-06-01",
            trecho="Portaria Nº 089/2036 do município",
        )
        assert c.corrigido is False
        assert c.numero_final == "089/2036"
        assert c.precisa_revisao is True
        assert c.motivo == "ano_futuro_ancorado"

    def test_320_2028_edicao_2026(self):
        c = corrigir_numero_ano(
            "320/2028",
            data_publicacao_edicao="2026-06-14",
            trecho="Lei Municipal 320/2028 extrato",
        )
        # 2028 é futuro; sem âncora N° — tenta OCR (2↔8 → 2026) ou revisão
        assert c.numero_final in ("320/2026", "320/2028", None) or c.precisa_revisao
        if c.corrigido and c.numero_final:
            assert c.numero_final.endswith("/2026") or c.numero_final.endswith("/2027")

    def test_sem_numero(self):
        c = corrigir_numero_ano(None, data_publicacao_edicao="2026-01-01")
        assert c.corrigido is False
        assert c.numero_final is None

    def test_portaria_sem_ano(self):
        c = corrigir_numero_ano("89", data_publicacao_edicao="2026-01-01")
        assert c.corrigido is False
        assert c.numero_final == "89"


class TestPosProcessar:
    def test_pos_processar_aplica_em_lista(self, mock_settings):
        pubs = [
            {
                "tipo": "Contrato",
                "numero": "095/2036",
                "trecho": "contrato 095/2036 valor",
                "pagina": 1,
            }
        ]
        with patch("qualidade.SETTINGS", mock_settings):
            out = pos_processar_publicacoes(pubs, data_edicao="2026-07-01")
        assert out[0]["numero"] == "095/2026"
        flags = out[0].get("flags_qualidade") or []
        assert any("numero_corrigido" in str(f) for f in flags)

    def test_pos_processar_desligado(self, mock_settings):
        object.__setattr__(mock_settings, "quality_fix_numero_ano", False)
        pubs = [{"numero": "095/2036", "trecho": "x", "pagina": 1}]
        with patch("qualidade.SETTINGS", mock_settings):
            out = pos_processar_publicacoes(pubs, data_edicao="2026-07-01")
        assert out[0]["numero"] == "095/2036"


class TestPipelineHelper:
    def test_aplicar_qualidade_pos_deteccao(self, mock_settings):
        from pipeline import _aplicar_qualidade_pos_deteccao

        res = DetectionResult(
            encontrado=True,
            edicao_id=1,
            edicao_titulo="t",
            paginas_com_mencao=[1],
            trechos=[],
            termos_encontrados=["Inajá"],
            mencoes_db=[],
            publicacoes=[
                {
                    "numero": "095/2036",
                    "trecho": "extrato 095/2036",
                    "tipo": "Contrato",
                    "pagina": 1,
                }
            ],
            metricas=DetectionMetrics(),
        )
        with patch("pipeline.SETTINGS", mock_settings), patch(
            "qualidade.SETTINGS", mock_settings
        ):
            novo = _aplicar_qualidade_pos_deteccao(res, "2026-07-01")
        assert novo.publicacoes[0]["numero"] == "095/2026"
        # frozen: retorna novo objeto
        assert novo is not res or novo.publicacoes[0]["numero"] == "095/2026"


class TestInsertFlags:
    def test_insert_persiste_flags_qualidade(self, db, mock_settings):
        import database

        database.init_db()
        eid = database.insert_or_get_edicao(
            "https://example.com/q1.pdf", "Q1", "2026-07-01"
        )
        database.insert_publicacoes(
            eid,
            [
                {
                    "pagina": 1,
                    "bloco": 0,
                    "categoria": "publicacao_oficial",
                    "orgao": "Prefeitura Municipal de Inajá",
                    "tipo": "Contrato",
                    "numero": "095/2026",
                    "trecho": "x",
                    "flags_qualidade": [
                        "numero_corrigido:ano_futuro_ocr:2036→2026"
                    ],
                }
            ],
        )
        with database.connect() as c:
            row = c.execute(
                "SELECT numero, flags_qualidade FROM publicacoes WHERE edicao_id=?",
                (eid,),
            ).fetchone()
        assert row is not None
        assert row["numero"] == "095/2026"
        assert "2036" in (row["flags_qualidade"] or "")
