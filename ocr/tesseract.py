from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
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

# Lock compartilhado para operacoes thread-safe na lista avisos
_avisos_lock = threading.Lock()


def _avisos_append(avisos: list[str] | None, msg: str) -> None:
    if avisos is not None:
        with _avisos_lock:
            avisos.append(msg)


def _tesseract_config(psm: int) -> str:
    return f"{_TESSERACT_OEM} --psm {psm}"


def _executar_pytesseract(fn, fn_args, timeout: int) -> object | None:
    """Wrapper que garante timeout real no Windows.

    No Windows o ``timeout`` do pytesseract nao funciona (usa signal.SIGALRM,
    que nao existe no Windows). Este wrapper usa ThreadPoolExecutor para impor
    timeout real em qualquer plataforma.
    """
    if sys.platform != "win32":
        try:
            return fn(*fn_args)
        except Exception as exc:
            logger.warning("OCR falhou: %s", exc)
            return None

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *fn_args)
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            logger.warning("OCR excedeu timeout de %ss", timeout)
            return None
        except Exception as exc:
            logger.warning("OCR falhou: %s", exc)
            return None


def _tentar_image_to_data(
    imagem: Image.Image,
    pagina: int,
    coluna: int,
    timeout: int,
    psm: int,
) -> dict | None:
    def _call():
        return pytesseract.image_to_data(
            imagem,
            lang=SETTINGS.ocr_language,
            config=_tesseract_config(psm),
            output_type=pytesseract.Output.DICT,
            timeout=timeout,
        )
    try:
        result = _executar_pytesseract(lambda: _call(), (), timeout)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        logger.warning(
            "OCR estruturado falhou (pagina %s coluna %s psm=%s): %s",
            pagina, coluna + 1, psm, exc,
        )
        return None


def _tentar_image_to_string(
    imagem: Image.Image,
    pagina: int,
    estrategia: str,
    psm: int,
    timeout: int,
) -> str | None:
    def _call():
        texto = pytesseract.image_to_string(
            imagem,
            lang=SETTINGS.ocr_language,
            config=_tesseract_config(psm),
            timeout=timeout,
        )
        return (texto or "").strip()
    try:
        texto = _executar_pytesseract(lambda: _call(), (), timeout)
        if isinstance(texto, str) and texto.strip():
            return texto.strip()
        return None
    except Exception as exc:
        logger.warning("OCR rapido falhou na pagina %s (%s): %s", pagina, estrategia, exc)
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
        item["words"].append(palavra)
        item["left"].append(left + x_offset)
        item["top"].append(top)
        item["right"].append(left + x_offset + width)
        item["bottom"].append(top + height)

    blocos_por_numero: dict[
        tuple[int, int], list[tuple[int, str, tuple[int, int, int, int]]]
    ] = {}
    for (coluna, block_num, _par_num, line_num), item in agrupado.items():
        words = item["words"]
        if not words:
            continue
        linha = " ".join(words)
        bbox = (
            min(item["left"]),
            min(item["top"]),
            max(item["right"]),
            max(item["bottom"]),
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
        ("binarizacao + psm 6", _binarizar(limitada, 140), 6),
        ("binarizacao 120 + psm 6", _binarizar(limitada, 120), 6),
        ("pre-processamento forte + psm 6", ampliada, 6),
    ]

    reduzida = limitada.resize(
        (max(1, limitada.width // 2), max(1, limitada.height // 2)),
        Image.LANCZOS,
    )
    tentativas.extend(
        [
            ("resolucao reduzida + psm 6", reduzida, 6),
            ("binarizacao reduzida + psm 6", _binarizar(reduzida), 6),
            ("psm 3 automatico", limitada, 3),
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
                        "OCR na pagina %s coluna %s recuperado (%s)",
                        pagina, coluna + 1, estrategia,
                    )
                return blocos

    logger.debug(
        "OCR sem texto na pagina %s coluna %s apos todas as estrategias",
        pagina, coluna + 1,
    )
    _avisos_append(avisos, f"Falha de OCR na pagina {pagina}, coluna {coluna + 1}")
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
                    "OCR na pagina %s coluna %s recuperado (ampliacao 2x)",
                    pagina, coluna + 1,
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
        logger.debug("Pagina %s: %s coluna(s) detectada(s)", pagina, len(faixas))
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
        texto = _tentar_image_to_string(ampliada, idx, "ampliacao 2x + psm 6", 6, SETTINGS.ocr_timeout_seconds)
        if texto and len(texto) > len(melhor.texto):
            melhor = PageText(
                pagina=idx,
                texto=texto,
                metodo="ocr-hq-string",
                blocks=[TextBlock(pagina=idx, bloco=1, texto=texto)],
            )
    return melhor
