"""
Tesseract OCR engine integration + structured block extraction.

Key features:
- Multiple PSM strategies + preprocessing fallbacks
- Automatic column-aware extraction
- Robust grouping of words into paragraphs/blocks
- Graceful degradation on OCR failures
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytesseract
from PIL import Image

from config import SETTINGS
from ocr.column_detection import _detectar_faixas_colunas
from ocr.models import PageText, TextBlock
from ocr.preprocessing import (
    _ampliar_imagem,
    _binarizar,
    _limitar_dimensao,
    _ocr_rapido_texto_valido,
    _preprocessar,
    _preprocessar_forte,
)

logger = logging.getLogger(__name__)

_TESSERACT_OEM = "--oem 1"


def _tesseract_config(psm: int) -> str:
    return f"{_TESSERACT_OEM} --psm {psm}"


def _tentar_image_to_data(
    imagem: Image.Image,
    pagina: int,
    coluna: int,
    timeout: int,
    psm: int,
) -> dict | None:
    """Tenta executar image_to_data; retorna None se falhar."""
    try:
        return pytesseract.image_to_data(
            imagem,
            lang=SETTINGS.ocr_language,
            config=_tesseract_config(psm),
            output_type=pytesseract.Output.DICT,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning(
            "OCR estruturado falhou (página %s coluna %s psm=%s): %s",
            pagina,
            coluna + 1,
            psm,
            exc,
        )
        return None


def _tentar_image_to_string(
    imagem: Image.Image,
    pagina: int,
    estrategia: str,
    psm: int,
    timeout: int,
) -> str | None:
    try:
        texto = pytesseract.image_to_string(
            imagem,
            lang=SETTINGS.ocr_language,
            config=_tesseract_config(psm),
            timeout=timeout,
        )
        texto = (texto or "").strip()
        return texto or None
    except Exception as exc:
        logger.warning("OCR rápido falhou na página %s (%s): %s", pagina, estrategia, exc)
        return None


def _agrupar_data_tesseract(
    data: dict,
    pagina: int,
    coluna: int,
    x_offset: int,
) -> list[TextBlock]:
    agrupado: dict[tuple[int, int, int, int], dict[str, object]] = {}
    total = len(data.get("text", []))
    for idx in range(total):
        palavra = str(data["text"][idx]).strip()
        if not palavra:
            continue
        try:
            conf = float(data["conf"][idx])
        except ValueError:
            conf = -1
        if conf < 0:
            continue

        block_num = int(data["block_num"][idx])
        par_num = int(data["par_num"][idx])
        line_num = int(data["line_num"][idx])
        left = int(data["left"][idx])
        top = int(data["top"][idx])
        width = int(data["width"][idx])
        height = int(data["height"][idx])
        chave = (coluna, block_num, par_num, line_num)
        item = agrupado.setdefault(
            chave,
            {"words": [], "left": [], "top": [], "right": [], "bottom": []},
        )
        item["words"].append(palavra)  # type: ignore[index, union-attr]
        item["left"].append(left + x_offset)  # type: ignore[index, union-attr]
        item["top"].append(top)  # type: ignore[index, union-attr]
        item["right"].append(left + x_offset + width)  # type: ignore[index, union-attr]
        item["bottom"].append(top + height)  # type: ignore[index, union-attr]

    blocos_por_numero: dict[
        tuple[int, int], list[tuple[int, str, tuple[int, int, int, int]]]
    ] = {}
    for (coluna, block_num, _par_num, line_num), item in agrupado.items():
        words = item["words"]  # type: ignore[index]
        if not words:
            continue
        linha = " ".join(words)  # type: ignore[arg-type]
        bbox = (
            min(item["left"]),  # type: ignore[arg-type, index]
            min(item["top"]),  # type: ignore[arg-type, index]
            max(item["right"]),  # type: ignore[arg-type, index]
            max(item["bottom"]),  # type: ignore[arg-type, index]
        )
        blocos_por_numero.setdefault((coluna, block_num), []).append(
            (line_num, linha, bbox)
        )

    blocos: list[TextBlock] = []
    for (coluna, block_num), linhas in sorted(
        blocos_por_numero.items(),
        key=lambda item: (
            min(linha[2][1] for linha in item[1]),
            item[0][0],
            item[0][1],
        ),
    ):
        linhas_ordenadas = sorted(linhas, key=lambda item: item[0])
        texto = "\n".join(linha for _, linha, _ in linhas_ordenadas).strip()
        if not texto:
            continue
        bbox = (
            min(item[2][0] for item in linhas_ordenadas),
            min(item[2][1] for item in linhas_ordenadas),
            max(item[2][2] for item in linhas_ordenadas),
            max(item[2][3] for item in linhas_ordenadas),
        )
        blocos.append(
            TextBlock(
                pagina=pagina,
                bloco=(coluna + 1) * 1000 + block_num,
                texto=texto,
                bbox=bbox,
            )
        )
    return blocos


def _extrair_blocos_tesseract_imagem(
    imagem: Image.Image,
    pagina: int,
    coluna: int,
    x_offset: int,
    timeout: int,
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    base = _preprocessar_forte(imagem)
    limitada = _limitar_dimensao(base, SETTINGS.ocr_max_dimension)
    ampliada = _limitar_dimensao(
        _ampliar_imagem(base, 1.5),
        int(SETTINGS.ocr_max_dimension * 1.25),
    )
    tentativas: list[tuple[str, Image.Image, int]] = [
        ("psm 4", limitada, 4),
        ("psm 6", limitada, 6),
        ("psm 11 texto esparso", limitada, 11),
        ("binarização + psm 6", _binarizar(limitada, 140), 6),
        ("binarização 120 + psm 6", _binarizar(limitada, 120), 6),
        ("pré-processamento forte + psm 6", ampliada, 6),
    ]

    reduzida = limitada.resize(
        (max(1, limitada.width // 2), max(1, limitada.height // 2)),
        Image.LANCZOS,
    )
    tentativas.extend(
        [
            ("resolução reduzida + psm 6", reduzida, 6),
            ("binarização reduzida + psm 6", _binarizar(reduzida), 6),
            ("psm 3 automático", limitada, 3),
        ]
    )

    timeout_retry = max(timeout, timeout * 2)
    for idx, (estrategia, candidata, psm) in enumerate(tentativas):
        data = _tentar_image_to_data(
            candidata,
            pagina,
            coluna,
            timeout if idx < 2 else timeout_retry,
            psm,
        )
        if data is not None:
            blocos = _agrupar_data_tesseract(data, pagina, coluna, x_offset)
            if blocos:
                if estrategia != "psm 4":
                    logger.debug(
                        "OCR na página %s coluna %s recuperado (%s)",
                        pagina,
                        coluna + 1,
                        estrategia,
                    )
                return blocos

    logger.warning("Falha de OCR na página %s coluna %s após todas as estratégias", pagina, coluna + 1)
    if avisos is not None:
        avisos.append(f"Falha de OCR na página {pagina}, coluna {coluna + 1}")
    return []


def _extrair_blocos_tesseract_por_coluna(
    imagem: Image.Image,
    pagina: int,
    faixas: list[tuple[int, int]],
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    recortes: list[tuple[int, Image.Image, int]] = []
    for coluna, (x0, x1) in enumerate(faixas):
        if x1 <= x0:
            continue
        recorte = imagem.crop((x0, 0, x1, imagem.height))
        recortes.append((coluna, recorte, x0))

    blocos: list[TextBlock] = []
    for coluna, recorte, x0 in recortes:
        resultado = _extrair_blocos_tesseract_imagem(
            recorte,
            pagina=pagina,
            coluna=coluna,
            x_offset=x0,
            timeout=SETTINGS.ocr_timeout_seconds,
            avisos=avisos,
        )
        if not resultado:
            resultado = _extrair_blocos_tesseract_imagem(
                _ampliar_imagem(recorte, 2.0),
                pagina=pagina,
                coluna=coluna,
                x_offset=x0,
                timeout=max(SETTINGS.ocr_timeout_seconds, SETTINGS.ocr_timeout_seconds * 2),
                avisos=avisos,
            )
            if resultado:
                logger.debug(
                    "OCR na página %s coluna %s recuperado (ampliação 2x)",
                    pagina,
                    coluna + 1,
                )
        blocos.extend(resultado)

    return sorted(
        blocos,
        key=lambda bloco: (
            (bloco.bloco // 1000) if bloco.bloco >= 1000 else 0,
            bloco.bbox[1] if bloco.bbox else 0,
            bloco.bbox[0] if bloco.bbox else 0,
            bloco.bloco,
        ),
    )


def _extrair_blocos_tesseract(
    imagem: Image.Image,
    pagina: int,
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    faixas = _detectar_faixas_colunas(imagem)
    if faixas:
        logger.debug("Página %s: %s coluna(s) detectada(s)", pagina, len(faixas))
        return _extrair_blocos_tesseract_por_coluna(imagem, pagina, faixas, avisos)

    return _extrair_blocos_tesseract_imagem(
        imagem,
        pagina=pagina,
        coluna=0,
        x_offset=0,
        timeout=SETTINGS.ocr_timeout_seconds,
        avisos=avisos,
    )


def _ocr_completo_pagina(
    idx: int,
    imagem: Image.Image,
    avisos: list[str],
    *,
    alta_qualidade: bool = False,
) -> PageText:
    candidatos: list[PageText] = []
    variantes = (
        [(_preprocessar_forte, "ocr-hq"), (_preprocessar, "ocr")]
        if alta_qualidade
        else [(_preprocessar, "ocr")]
    )
    for preprocessar, metodo in variantes:
        processada = preprocessar(imagem)
        blocks = _extrair_blocos_tesseract(processada, idx, avisos)
        texto = "\n\n".join(block.texto for block in blocks).strip()
        candidatos.append(
            PageText(pagina=idx, texto=texto, metodo=metodo, blocks=blocks)
        )

    melhor = max(candidatos, key=lambda pagina: len(pagina.texto))
    if alta_qualidade and not _ocr_rapido_texto_valido(melhor.texto):
        ampliada = _preprocessar_forte(_ampliar_imagem(imagem, 2.0))
        texto = _tentar_image_to_string(ampliada, idx, "ampliação 2x + psm 6", 6, SETTINGS.ocr_timeout_seconds)
        if texto and len(texto) > len(melhor.texto):
            melhor = PageText(
                pagina=idx,
                texto=texto,
                metodo="ocr-hq-string",
                blocks=[TextBlock(pagina=idx, bloco=1, texto=texto)],
            )
    return melhor
