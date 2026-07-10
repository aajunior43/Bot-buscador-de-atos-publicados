# -*- coding: utf-8 -*-
"""Âncora de valor monetário (anti-alucinação)."""
from __future__ import annotations

from inteligencia import valor_ancorado_no_trecho, validar_campos_ia


def test_valor_com_centavos_ancora_em_ocr_sem_centavos():
    # Caso real: IA devolve R$ 255.800,00; OCR tem 255.800 sem ,00
    valor = "R$ 255.800,00"
    trecho = "EXTRATO DO CONTRATO valor total de 255.800 para aquisicao"
    assert valor_ancorado_no_trecho(valor, trecho) is True


def test_valor_ancorado_digitos_colados():
    assert valor_ancorado_no_trecho("R$ 10.000,00", "total 10000 reais") is True


def test_valor_nao_ancorado():
    assert valor_ancorado_no_trecho("R$ 999.999,00", "contrato sem montante") is False


def test_validar_campos_ia_mantem_valor():
    pub = {
        "trecho": "Contrato n 04/2026 valor de R$ 255.800,00 com a empresa X"
    }
    ia = {"valor": "R$ 255.800,00", "tipo": "Extrato de Contrato", "numero": "04/2026"}
    out = validar_campos_ia(pub, ia)
    assert out.get("valor")  # não remove
