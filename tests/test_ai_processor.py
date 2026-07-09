# -*- coding: utf-8 -*-
"""
Testes unitários para ai_processor.py
Cobre pré-limpeza de OCR e pós-processamento (normalização de valor, data e órgão).
"""
from __future__ import annotations

from ai_processor import (
    _limpar_trecho_ocr,
    _normalizar_valor_ia,
    _normalizar_data_ia,
    _normalizar_orgao_ia,
    normalizar_tipo_ato,
)


# ── _limpar_trecho_ocr ──────────────────────────────────────

class TestLimparTrechoOCR:
    def test_remove_caracteres_controle(self):
        texto = "DECRETO\x00Nº\x0b001"
        limpo = _limpar_trecho_ocr(texto)
        assert "\x00" not in limpo
        assert "\x0b" not in limpo

    def test_normaliza_espacos_excessivos(self):
        texto = "DECRETO    Nº   001"
        limpo = _limpar_trecho_ocr(texto)
        assert "  " not in limpo

    def test_limpa_linhas_vazias_consecutivas(self):
        texto = "DECRETO\n\n\n\n\nPORTARIA"
        limpo = _limpar_trecho_ocr(texto)
        assert "\n\n\n" not in limpo

    def test_corrigir_lei_sem_espaco(self):
        texto = "LEINº 123/2026"
        limpo = _limpar_trecho_ocr(texto)
        assert "LEI Nº" in limpo

    def test_limita_tamanho(self):
        texto = "A" * 10000
        limpo = _limpar_trecho_ocr(texto)
        assert len(limpo) <= 4000

    def test_vazio(self):
        assert _limpar_trecho_ocr("") == ""
        assert _limpar_trecho_ocr(None) == ""


# ── _normalizar_valor_ia ────────────────────────────────────

class TestNormalizarValor:
    def test_valor_padrao(self):
        assert _normalizar_valor_ia("R$ 15.000,00") == "R$ 15.000,00"

    def test_valor_com_prefixo_texto(self):
        assert _normalizar_valor_ia("no valor de R$ 10.000,00") == "R$ 10.000,00"

    def test_valor_rs_colado(self):
        assert _normalizar_valor_ia("RS15000,00") == "R$ 15000,00"

    def test_vazio(self):
        assert _normalizar_valor_ia(None) is None
        assert _normalizar_valor_ia("") is None
        assert _normalizar_valor_ia("   ") is None


# ── _normalizar_data_ia ─────────────────────────────────────

class TestNormalizarData:
    def test_data_extenso(self):
        assert _normalizar_data_ia("15 de março de 2026") == "15/03/2026"

    def test_data_extenso_sem_acento(self):
        assert _normalizar_data_ia("15 de marco de 2026") == "15/03/2026"

    def test_data_numerica_barras(self):
        assert _normalizar_data_ia("25/06/2026") == "25/06/2026"

    def test_data_numerica_curta(self):
        assert _normalizar_data_ia("25/06/26") == "25/06/2026"

    def test_data_numerica_tracos(self):
        assert _normalizar_data_ia("25-06-2026") == "25/06/2026"

    def test_vazio(self):
        assert _normalizar_data_ia(None) is None
        assert _normalizar_data_ia("") is None


# ── _normalizar_orgao_ia ───────────────────────────────────

class TestNormalizarOrgao:
    def test_prefeitura_variacoes(self):
        assert _normalizar_orgao_ia("PREFEITURA MUNICIPAL DE INAJÁ-PR") == "Prefeitura Municipal de Inajá"
        assert _normalizar_orgao_ia("Prefeitura de Inajá") == "Prefeitura Municipal de Inajá"

    def test_camara(self):
        assert _normalizar_orgao_ia("Câmara Municipal de Inajá") == "Câmara Municipal de Inajá"

    def test_municipio(self):
        assert _normalizar_orgao_ia("Município de Inajá") == "Município de Inajá"

    def test_outro_municipio_preservado(self):
        # Órgão de outro município não é normalizado (filtro posterior decide)
        assert _normalizar_orgao_ia("Prefeitura de Cruzeiro do Sul") == "Prefeitura de Cruzeiro do Sul"

    def test_vazio(self):
        assert _normalizar_orgao_ia(None) is None
        assert _normalizar_orgao_ia("") is None


class TestNormalizarTipo:
    def test_extrato_contrato(self):
        assert normalizar_tipo_ato("Extrato de Contrato") == "Extrato de Contrato"

    def test_homologacao_longa(self):
        assert (
            normalizar_tipo_ato("Termo de Homologação e Adjudicação")
            == "Homologação/Adjudicação"
        )

    def test_dispensa(self):
        assert normalizar_tipo_ato("Dispensa de Licitação") == "Dispensa"
        assert normalizar_tipo_ato("Dispensa Eletrônica") == "Dispensa"

    def test_portaria_decreto(self):
        assert normalizar_tipo_ato("PORTARIA") == "Portaria"
        assert normalizar_tipo_ato("Decreto") == "Decreto"

    def test_vazio(self):
        assert normalizar_tipo_ato(None) is None
        assert normalizar_tipo_ato("") is None
