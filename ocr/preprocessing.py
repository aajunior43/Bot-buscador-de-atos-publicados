"""
Image preprocessing utilities for OCR.

Includes:
- Grayscale conversion + contrast/sharpen/equalize
- Binarization
- Resizing with limits
- Page loading via pdf2image
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from config import SETTINGS
from pdf2image import convert_from_path


def _ocr_rapido_min_chars() -> int:
    return max(SETTINGS.min_text_chars_per_page, SETTINGS.ocr_fast_min_chars)


def _ocr_rapido_texto_valido(texto: str) -> bool:
    return len((texto or "").strip()) >= _ocr_rapido_min_chars()


def _limitar_dimensao(imagem: Image.Image, max_dim: int | None = None) -> Image.Image:
    limite = max_dim or SETTINGS.ocr_fast_max_dimension
    largura, altura = imagem.size
    maior = max(largura, altura)
    if maior <= limite:
        return imagem
    escala = limite / maior
    return imagem.resize(
        (max(1, int(largura * escala)), max(1, int(altura * escala))),
        Image.LANCZOS,
    )


def _preprocessar(imagem: Image.Image) -> Image.Image:
    cinza = imagem.convert("L")
    contraste = ImageEnhance.Contrast(cinza).enhance(1.5)
    nitido = contraste.filter(ImageFilter.SHARPEN)
    equalizada = ImageOps.equalize(nitido)
    return equalizada


def _preprocessar_forte(imagem: Image.Image) -> Image.Image:
    cinza = imagem.convert("L")
    suavizada = cinza.filter(ImageFilter.MedianFilter(size=3))
    contraste = ImageEnhance.Contrast(suavizada).enhance(2.2)
    nitido = contraste.filter(ImageFilter.SHARPEN)
    return ImageOps.autocontrast(nitido)


def _ampliar_imagem(imagem: Image.Image, fator: float = 2.0) -> Image.Image:
    if fator <= 1.0:
        return imagem
    largura, altura = imagem.size
    return imagem.resize(
        (max(1, int(largura * fator)), max(1, int(altura * fator))),
        Image.LANCZOS,
    )


def _binarizar(imagem: Image.Image, threshold: int = 140) -> Image.Image:
    """Binarização por threshold — eficaz em imagens de baixa qualidade."""
    cinza = imagem.convert("L")
    return cinza.point(lambda x: 0 if x < threshold else 255, "1")


def _carregar_pagina_pdf(pdf_path: Path, pagina: int, dpi: int) -> Image.Image | None:
    imagens = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=pagina,
        last_page=pagina,
        poppler_path=SETTINGS.poppler_path or None,
    )
    return imagens[0] if imagens else None
