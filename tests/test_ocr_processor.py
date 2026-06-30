# -*- coding: utf-8 -*-
"""
Testes unitarios para ocr_processor.py
Cobre pre-processamento, binarizacao e estrategias de fallback de OCR.
"""
from __future__ import annotations

from PIL import Image
from ocr_processor import _preprocessar, _binarizar, _agrupar_data_tesseract


# ── _preprocessar ──────────────────────────────────────────

class TestPreprocessar:
    def test_converte_para_cinza(self):
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        result = _preprocessar(img)
        assert result.mode == "L"

    def test_mantem_dimensoes(self):
        img = Image.new("RGB", (200, 300), color=(128, 128, 128))
        result = _preprocessar(img)
        assert result.size == (200, 300)

    def test_imagem_vazia_nao_falha(self):
        img = Image.new("RGB", (10, 10), color=(0, 0, 0))
        result = _preprocessar(img)
        assert result is not None


# ── _binarizar ─────────────────────────────────────────────

class TestBinarizar:
    def test_produz_imagem_binaria(self):
        img = Image.new("RGB", (100, 100), color=(200, 200, 200))
        result = _binarizar(img)
        assert result.mode == "1"

    def test_pixel_claro_vira_branco(self):
        img = Image.new("L", (10, 10), color=250)
        result = _binarizar(img)
        pixel = result.getpixel((5, 5))
        assert pixel == 255

    def test_pixel_escuro_vira_preto(self):
        img = Image.new("L", (10, 10), color=50)
        result = _binarizar(img)
        pixel = result.getpixel((5, 5))
        assert pixel == 0

    def test_mantem_dimensoes(self):
        img = Image.new("RGB", (150, 200), color=(100, 100, 100))
        result = _binarizar(img)
        assert result.size == (150, 200)


# ── _agrupar_data_tesseract ───────────────────────────────

class TestAgruparDataTesseract:
    def test_dados_vazios_retorna_lista_vazia(self):
        data = {
            "text": [], "conf": [], "block_num": [], "par_num": [],
            "line_num": [], "left": [], "top": [], "width": [], "height": [],
        }
        result = _agrupar_data_tesseract(data, pagina=1, coluna=0, x_offset=0)
        assert result == []

    def test_dados_simples_agrupa_bloco(self):
        data = {
            "text": ["DECRETO", "N", "001"],
            "conf": ["95.0", "90.0", "88.0"],
            "block_num": [1, 1, 1],
            "par_num": [1, 1, 1],
            "line_num": [1, 1, 1],
            "left": [10, 80, 110],
            "top": [20, 20, 20],
            "width": [60, 20, 30],
            "height": [15, 15, 15],
        }
        result = _agrupar_data_tesseract(data, pagina=1, coluna=0, x_offset=0)
        assert len(result) == 1
        assert "DECRETO" in result[0].texto
        assert result[0].pagina == 1

    def test_confianca_baixa_ignorada(self):
        data = {
            "text": ["boa", "ruim"],
            "conf": ["90.0", "-1"],
            "block_num": [1, 1],
            "par_num": [1, 1],
            "line_num": [1, 1],
            "left": [10, 80],
            "top": [20, 20],
            "width": [60, 20],
            "height": [15, 15],
        }
        result = _agrupar_data_tesseract(data, pagina=1, coluna=0, x_offset=0)
        assert len(result) == 1
        assert "boa" in result[0].texto
        assert "ruim" not in result[0].texto

    def test_x_offset_aplicado(self):
        data = {
            "text": ["texto"],
            "conf": ["90.0"],
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
            "left": [10],
            "top": [20],
            "width": [60],
            "height": [15],
        }
        result = _agrupar_data_tesseract(data, pagina=2, coluna=1, x_offset=500)
        assert len(result) == 1
        bbox = result[0].bbox
        assert bbox[0] == 510  # left + x_offset
