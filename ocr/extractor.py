"""
High-level OCR extraction strategies.

This module orchestrates the different extraction modes:
- Native text via pdfplumber
- Hybrid (native + OCR on weak pages)
- Forced full OCR
- Fast low-DPI + selective structured OCR on "Inajá candidate" pages

All strategies produce PageText + blocks and are cached.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pdfplumber
from pdf2image import convert_from_path
from PIL import Image

from config import SETTINGS
from ocr.cache import _carregar_cache_ocr, _salvar_cache_ocr
from ocr.models import OCRResult, PageText, TextBlock
from ocr.preprocessing import (
    _ampliar_imagem,
    _binarizar,
    _carregar_pagina_pdf,
    _limitar_dimensao,
    _ocr_rapido_texto_valido,
    _preprocessar,
    _preprocessar_forte,
)
from ocr.tesseract import (
    _extrair_blocos_tesseract,
    _ocr_completo_pagina,
    _tentar_image_to_string,
)

logger = logging.getLogger(__name__)

_WORKERS = SETTINGS.ocr_max_workers



def _texto_pdfplumber(pdf_path: Path, on_progress=None) -> list[PageText]:
    paginas: list[PageText] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        for idx, page in enumerate(pdf.pages, start=1):
            if on_progress:
                on_progress(f"[pdfplumber] Extraindo texto nativo: Página {idx}/{total}")
            texto = page.extract_text() or ""
            texto = texto.strip()
            paginas.append(
                PageText(
                    pagina=idx,
                    texto=texto,
                    metodo="pdfplumber",
                    blocks=[TextBlock(pagina=idx, bloco=1, texto=texto)] if texto else [],
                )
            )
    return paginas


def _pagina_precisa_ocr(pagina: PageText, force_ocr: bool) -> bool:
    if force_ocr:
        return True
    return len(pagina.texto.strip()) < SETTINGS.min_text_chars_per_page


def _texto_ocr_paginas(
    pdf_path: Path,
    paginas: list[int],
    avisos: list[str] | None = None,
    on_progress=None,
) -> dict[int, PageText]:
    if not paginas:
        return {}

    primeira = min(paginas)
    ultima = max(paginas)
    imagens = convert_from_path(
        str(pdf_path),
        dpi=SETTINGS.ocr_dpi,
        first_page=primeira,
        last_page=ultima,
        poppler_path=SETTINGS.poppler_path or None,
        thread_count=min(4, os.cpu_count() or 1),
    )
    paginas_set = set(paginas)
    imagens_filtradas: list[tuple[int, Image.Image]] = []
    numero_pagina = primeira
    for imagem in imagens:
        if numero_pagina in paginas_set:
            imagens_filtradas.append((numero_pagina, imagem))
        numero_pagina += 1

    textos: dict[int, PageText] = {}
    total = len(imagens_filtradas)
    processadas = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        future_to_pagina = {
            pool.submit(_ocr_completo_pagina, num, img, avisos or []): num
            for num, img in imagens_filtradas
        }
        for future in as_completed(future_to_pagina):
            num = future_to_pagina[future]
            textos[num] = future.result()
            with lock:
                processadas += 1
                if on_progress:
                    on_progress({"step": "ocr_structured", "current": processadas, "total": total, "msg": f"[ocr-estruturado] Página {processadas}/{total} processada"})
    return textos


def _texto_ocr_completo(pdf_path: Path, avisos: list[str] | None = None, on_progress=None) -> list[PageText]:
    imagens = convert_from_path(
        str(pdf_path),
        dpi=SETTINGS.ocr_dpi,
        poppler_path=SETTINGS.poppler_path or None,
        thread_count=min(4, os.cpu_count() or 1),
    )
    avisos = avisos if avisos is not None else []
    paginas: list[PageText | None] = [None] * len(imagens)
    total = len(imagens)
    processadas = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {
            pool.submit(_ocr_completo_pagina, idx, imagem, avisos): idx
            for idx, imagem in enumerate(imagens, start=1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            paginas[idx - 1] = future.result()
            with lock:
                processadas += 1
                if on_progress:
                    on_progress({"step": "ocr_completo", "current": processadas, "total": total, "msg": f"[ocr-completo] Página {processadas}/{total} processada"})

    if SETTINGS.ocr_retry_dpi > SETTINGS.ocr_dpi:
        fracas = [
            pagina.pagina
            for pagina in paginas
            if pagina is not None and not _ocr_rapido_texto_valido(pagina.texto)
        ]
        if fracas:
            logger.info(
                "Reprocessando %s página(s) em alta resolução (%s DPI): %s",
                len(fracas),
                SETTINGS.ocr_retry_dpi,
                fracas,
            )
            if on_progress:
                on_progress(
                    f"[ocr-alta-res] Alta resolução ({SETTINGS.ocr_retry_dpi} DPI) em {len(fracas)} página(s)"
                )
            for num in fracas:
                imagem_hq = _carregar_pagina_pdf(pdf_path, num, SETTINGS.ocr_retry_dpi)
                if imagem_hq is None:
                    continue
                anterior = paginas[num - 1]
                nova = _ocr_completo_pagina(num, imagem_hq, avisos, alta_qualidade=True)
                if len(nova.texto) > len(anterior.texto if anterior else ""):
                    logger.info(
                        "Página %s melhorada com OCR alta resolução (%s -> %s chars)",
                        num,
                        len(anterior.texto if anterior else ""),
                        len(nova.texto),
                    )
                    paginas[num - 1] = nova

    return [p for p in paginas if p is not None]


def _ocr_rapido_pagina(idx: int, imagem: Image.Image, avisos: list[str]) -> PageText:
    processada = _preprocessar(imagem)
    limitada = _limitar_dimensao(processada)
    reduzida = limitada.resize(
        (max(1, limitada.width // 2), max(1, limitada.height // 2)),
        Image.LANCZOS,
    )

    timeout_base = SETTINGS.ocr_fast_timeout_seconds
    timeout_curto = timeout_base
    timeout_longo = max(90, timeout_base * 2)

    tentativas: list[tuple[str, Image.Image, int, int]] = [
        ("psm 6", limitada, 6, timeout_curto),
        ("resolução reduzida + psm 6", reduzida, 6, timeout_base),
        ("resolução reduzida + psm 3", reduzida, 3, timeout_base),
        ("binarização + psm 6", _binarizar(limitada), 6, timeout_longo),
        ("binarização reduzida + psm 6", _binarizar(reduzida), 6, timeout_longo),
    ]

    texto = ""
    estrategia_ok = ""
    for estrategia, candidata, psm, timeout in tentativas:
        candidato = _tentar_image_to_string(candidata, idx, estrategia, psm, timeout)
        if candidato and _ocr_rapido_texto_valido(candidato):
            texto = candidato
            estrategia_ok = estrategia
            break
        if candidato:
            logger.info(
                "OCR rápido na página %s (%s) com pouco texto (%s chars); tentando próxima estratégia",
                idx,
                estrategia,
                len(candidato),
            )

    if texto and estrategia_ok != "psm 6":
        logger.info("OCR rápido na página %s recuperado (%s)", idx, estrategia_ok)
    elif not texto:
        logger.info("OCR rápido insuficiente na página %s; aplicando OCR estruturado", idx)
        pagina = _ocr_completo_pagina(idx, imagem, avisos, alta_qualidade=True)
        if _ocr_rapido_texto_valido(pagina.texto):
            return pagina
        ampliada = _preprocessar_forte(_ampliar_imagem(imagem, 2.0))
        texto_hq = _tentar_image_to_string(
            ampliada,
            idx,
            "fallback ampliação 2x + psm 6",
            6,
            max(SETTINGS.ocr_timeout_seconds, SETTINGS.ocr_fast_timeout_seconds * 2),
        )
        if texto_hq and len(texto_hq) > len(pagina.texto):
            pagina = PageText(
                pagina=idx,
                texto=texto_hq,
                metodo="ocr-hq-string",
                blocks=[TextBlock(pagina=idx, bloco=1, texto=texto_hq)],
            )
        if _ocr_rapido_texto_valido(pagina.texto):
            return pagina
        avisos.append(
            f"OCR insuficiente na página {idx} após {len(tentativas)} tentativas rápidas "
            f"e OCR estruturado ({len(pagina.texto)} caracteres)"
        )
        return pagina

    return PageText(
        pagina=idx,
        texto=texto,
        metodo="ocr-rapido",
        blocks=[TextBlock(pagina=idx, bloco=1, texto=texto)] if texto else [],
    )


def _texto_ocr_rapido_pdf(pdf_path: Path, avisos: list[str], on_progress=None) -> list[PageText]:
    imagens = convert_from_path(
        str(pdf_path),
        dpi=SETTINGS.ocr_fast_dpi,
        poppler_path=SETTINGS.poppler_path or None,
        thread_count=min(4, os.cpu_count() or 1),
    )
    paginas: list[PageText | None] = [None] * len(imagens)
    total = len(imagens)
    processadas = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {
            pool.submit(_ocr_rapido_pagina, idx, imagem, avisos): idx
            for idx, imagem in enumerate(imagens, start=1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            paginas[idx - 1] = future.result()
            with lock:
                processadas += 1
                if on_progress:
                    on_progress({"step": "ocr_fast", "current": processadas, "total": total, "msg": f"[ocr-rapido] Página {processadas}/{total} processada"})
    return [p for p in paginas if p is not None]


def _texto_tem_candidato_inaja(texto: str) -> bool:
    import unicodedata

    normalizado = unicodedata.normalize("NFKD", texto or "")
    sem_acentos = "".join(ch for ch in normalizado if not unicodedata.combining(ch))
    texto_norm = sem_acentos.casefold()
    termos = [
        "inaja",
        "75.771.400/0001-48",
        "prefeitura municipal",
        "camara municipal",
        "municipio",
    ]
    has_inaja = "inaja" in texto_norm
    has_official = any(termo in texto_norm for termo in termos)
    return has_inaja or has_official


def extrair_texto_rapido_com_estruturado_candidato(pdf_path: Path, on_progress=None) -> OCRResult:
    """Versão otimizada: OCR rápido em baixa DPI + OCR estruturado apenas nas páginas
    que parecem mencionar Inajá (candidatas).

    Esta é a estratégia padrão recomendada para uso em produção.
    """
    cache = _carregar_cache_ocr(pdf_path)
    if cache and not SETTINGS.force_ocr:
        logger.info("OCR recuperado do cache com sucesso para %s", pdf_path)
        return cache

    logger.info("Executando OCR rápido com estruturação só em páginas candidatas: %s", pdf_path)
    avisos: list[str] = []

    # Reaproveita cache parcial se existir
    paginas_prontas = {}
    if cache:
        paginas_prontas = {p.pagina: p for p in cache.paginas if p.texto.strip()}

    imagens = convert_from_path(
        str(pdf_path),
        dpi=SETTINGS.ocr_fast_dpi,
        poppler_path=SETTINGS.poppler_path or None,
        thread_count=min(4, os.cpu_count() or 1),
    )

    paginas: list[PageText | None] = [None] * len(imagens)
    total = len(imagens)
    processadas = 0
    lock = threading.Lock()

    candidatas_ocr = []
    for idx, imagem in enumerate(imagens, start=1):
        if idx in paginas_prontas:
            paginas[idx - 1] = paginas_prontas[idx]
            processadas += 1
        else:
            candidatas_ocr.append((idx, imagem))

    if candidatas_ocr:
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            futures = {
                pool.submit(_ocr_rapido_pagina, idx, imagem, avisos): idx
                for idx, imagem in candidatas_ocr
            }
            for future in as_completed(futures):
                idx = futures[future]
                paginas[idx - 1] = future.result()
                with lock:
                    processadas += 1
                    if on_progress:
                        on_progress(f"OCR rápido: Página {processadas}/{total} processada")

    # Páginas que precisam de OCR estruturado
    paginas_candidatas = [
        pagina.pagina for pagina in paginas if pagina is not None and _texto_tem_candidato_inaja(pagina.texto)
    ]

    paginas_candidatas = [
        p for p in paginas_candidatas
        if paginas[p - 1] is None or paginas[p - 1].metodo not in ("ocr", "ocr-hq", "ocr-hq-string")
    ]

    if paginas_candidatas:
        logger.info("Páginas candidatas para OCR estruturado: %s", paginas_candidatas)
        if on_progress:
            on_progress(f"Encontradas {len(paginas_candidatas)} páginas candidatas a OCR estruturado")
        estruturadas = _texto_ocr_paginas(pdf_path, paginas_candidatas, avisos, on_progress=on_progress)
        paginas = [estruturadas.get(pagina.pagina, pagina) if pagina is not None else None for pagina in paginas]

    paginas_finais = [p for p in paginas if p is not None]

    texto_completo = "\n\n".join(
        f"--- Página {pagina.pagina} ({pagina.metodo}) ---\n{pagina.texto}"
        for pagina in paginas_finais
    )
    texto_path = pdf_path.with_suffix(".txt")
    texto_path.write_text(texto_completo, encoding="utf-8")
    result = OCRResult(
        texto_completo=texto_completo,
        paginas=paginas_finais,
        texto_path=texto_path,
        avisos=avisos,
    )
    _salvar_cache_ocr(pdf_path, result)
    return result


def extrair_texto(pdf_path: Path, force_ocr: bool | None = None, on_progress=None) -> OCRResult:
    """Extração principal de texto.

    Modos:
    - Padrão (sem force_ocr): pdfplumber + OCR híbrido nas páginas com pouco texto.
    - force_ocr=True: Tesseract em todas as páginas (mais lento e preciso em alguns casos).
    """
    usar_force_ocr = SETTINGS.force_ocr if force_ocr is None else force_ocr
    if not usar_force_ocr:
        cache = _carregar_cache_ocr(pdf_path)
        if cache:
            logger.info("OCR recuperado do cache com sucesso para %s", pdf_path)
            return cache

    logger.info("Extraindo texto de %s", pdf_path)
    avisos: list[str] = []

    if usar_force_ocr:
        logger.info("OCR forçado ativado; aplicando Tesseract em todas as páginas.")
        paginas = _texto_ocr_completo(pdf_path, avisos, on_progress=on_progress)
    else:
        paginas = _texto_pdfplumber(pdf_path, on_progress=on_progress)
        paginas_fracas = [
            pagina.pagina for pagina in paginas if _pagina_precisa_ocr(pagina, False)
        ]
        if not paginas:
            logger.info("PDF sem páginas extraídas por pdfplumber; aplicando OCR completo.")
            paginas = _texto_ocr_completo(pdf_path, avisos, on_progress=on_progress)
        elif paginas_fracas:
            logger.info(
                "Aplicando OCR híbrido nas páginas com pouco texto: %s",
                paginas_fracas,
            )
            if on_progress:
                on_progress(f"[ocr-hibrido] Aplicando em {len(paginas_fracas)} páginas com pouco texto nativo")
            ocr_por_pagina = _texto_ocr_paginas(pdf_path, paginas_fracas, avisos, on_progress=on_progress)
            paginas = [
                ocr_por_pagina.get(pagina.pagina, pagina)
                for pagina in paginas
            ]

    texto_completo = "\n\n".join(
        f"--- Página {pagina.pagina} ({pagina.metodo}) ---\n{pagina.texto}"
        for pagina in paginas
    )
    texto_path = pdf_path.with_suffix(".txt")
    texto_path.write_text(texto_completo, encoding="utf-8")
    result = OCRResult(
        texto_completo=texto_completo,
        paginas=paginas,
        texto_path=texto_path,
        avisos=avisos,
    )
    _salvar_cache_ocr(pdf_path, result)
    return result
