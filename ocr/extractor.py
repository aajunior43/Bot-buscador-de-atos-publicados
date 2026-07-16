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
from pathlib import Path

import pdfplumber
from pdf2image import convert_from_path
from PIL import Image

from config import SETTINGS
from ocr.cache import _carregar_cache_ocr, _salvar_cache_ocr
from ocr.cpu_workers import escolher_workers, map_parallel_indexed
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
    _avisos_append,
    _extrair_blocos_tesseract,
    _ocr_completo_pagina,
    _tentar_image_to_string,
)

_BATCH_SIZE = 10
_progress_lock = threading.Lock()

logger = logging.getLogger(__name__)



def _progress_safe(on_progress, msg) -> None:
    """Chama on_progress com lock para evitar race condition no DB."""
    if on_progress:
        with _progress_lock:
            on_progress(msg)


def _carregar_paginas_em_lotes(
    pdf_path: Path, paginas: list[int], dpi: int,
) -> list[tuple[int, Image.Image]]:
    """Carrega paginas em lotes de _BATCH_SIZE para evitar estouro de memoria."""
    if not paginas:
        return []
    resultado: list[tuple[int, Image.Image]] = []
    paginas_ord = sorted(set(paginas))
    for i in range(0, len(paginas_ord), _BATCH_SIZE):
        lote = paginas_ord[i:i + _BATCH_SIZE]
        imagens = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            first_page=lote[0],
            last_page=lote[-1],
            poppler_path=SETTINGS.poppler_path or None,
            thread_count=min(2, os.cpu_count() or 1),
        )
        for offset, img in enumerate(imagens):
            resultado.append((lote[0] + offset, img))
    return resultado


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

    avisos_ref = avisos or []
    imagens_filtradas = _carregar_paginas_em_lotes(pdf_path, paginas, SETTINGS.ocr_dpi)

    total = len(imagens_filtradas)

    _progress_safe(
        on_progress,
        {
            "step": "ocr_structured",
            "current": 0,
            "total": total,
            "msg": f"[ocr-estruturado] Iniciando {total} pagina(s)",
        },
    )

    def _job(pair: tuple[int, Image.Image]) -> PageText:
        num, img = pair
        return _ocr_completo_pagina(num, img, avisos_ref)

    def _done(done: int, tot: int) -> None:
        _progress_safe(
            on_progress,
            {
                "step": "ocr_structured",
                "current": done,
                "total": tot,
                "msg": f"[ocr-estruturado] Pagina {done}/{tot} processada",
            },
        )

    indexed = map_parallel_indexed(
        imagens_filtradas,
        _job,
        label="ocr-estruturado",
        on_done=_done if on_progress else None,
    )
    return {imagens_filtradas[i][0]: indexed[i] for i in indexed}


def _texto_ocr_completo(pdf_path: Path, avisos: list[str] | None = None, on_progress=None) -> list[PageText]:
    avisos = avisos if avisos is not None else []
    total_paginas = 0
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total_paginas = len(pdf.pages)
    except Exception:
        pass
    if total_paginas == 0:
        total_paginas = 1

    imagens = _carregar_paginas_em_lotes(
        pdf_path, list(range(1, total_paginas + 1)), SETTINGS.ocr_dpi
    )
    paginas: list[PageText | None] = [None] * total_paginas
    total = len(imagens)
    itens = list(enumerate(imagens, start=1))

    def _job(pair: tuple[int, Image.Image]) -> tuple[int, PageText]:
        idx, imagem = pair
        return idx, _ocr_completo_pagina(idx, imagem, avisos)

    def _done(done: int, tot: int) -> None:
        _progress_safe(
            on_progress,
            {
                "step": "ocr_completo",
                "current": done,
                "total": tot,
                "msg": f"[ocr-completo] Pagina {done}/{tot} processada",
            },
        )

    indexed = map_parallel_indexed(
        itens,
        _job,
        label="ocr-completo",
        on_done=_done if on_progress else None,
    )
    for i, (idx, page) in indexed.items():
        paginas[idx - 1] = page

    if SETTINGS.ocr_retry_dpi > SETTINGS.ocr_dpi:
        fracas = [
            pagina.pagina
            for pagina in paginas
            if pagina is not None and not _ocr_rapido_texto_valido(pagina.texto)
        ]
        if fracas:
            logger.info(
                "Reprocessando %s pagina(s) em alta resolucao (%s DPI): %s",
                len(fracas), SETTINGS.ocr_retry_dpi, fracas,
            )
            _progress_safe(
                on_progress,
                f"[ocr-alta-res] Alta resolucao ({SETTINGS.ocr_retry_dpi} DPI) em {len(fracas)} pagina(s)",
            )
            # Carrega paginas fracas em lote para evitar chamadas individuais
            fracas_imagens = _carregar_paginas_em_lotes(pdf_path, fracas, SETTINGS.ocr_retry_dpi)
            for num, imagem_hq in fracas_imagens:
                anterior = paginas[num - 1]
                nova = _ocr_completo_pagina(num, imagem_hq, avisos, alta_qualidade=True)
                if len(nova.texto) > len(anterior.texto if anterior else ""):
                    logger.info(
                        "Pagina %s melhorada com OCR alta resolucao (%s -> %s chars)",
                        num, len(anterior.texto if anterior else ""), len(nova.texto),
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
    melhor_curto = ""
    for estrategia, candidata, psm, timeout in tentativas:
        candidato = _tentar_image_to_string(candidata, idx, estrategia, psm, timeout)
        if candidato and _ocr_rapido_texto_valido(candidato):
            texto = candidato
            estrategia_ok = estrategia
            break
        if candidato:
            if len(candidato) > len(melhor_curto):
                melhor_curto = candidato
            logger.debug(
                "OCR rápido na página %s (%s) com pouco texto (%s chars); tentando próxima estratégia",
                idx,
                estrategia,
                len(candidato),
            )

    if texto and estrategia_ok != "psm 6":
        logger.info("OCR rápido na página %s recuperado (%s)", idx, estrategia_ok)
    elif not texto:
        if len(melhor_curto) >= 80:
            logger.info(
                "OCR rapido na pagina %s ficou curto (%s chars); mantem resultado sem estruturado",
                idx, len(melhor_curto),
            )
            return PageText(
                pagina=idx,
                texto=melhor_curto,
                metodo="ocr-rapido",
                blocks=[TextBlock(pagina=idx, bloco=1, texto=melhor_curto)],
            )
        logger.info("OCR rapido insuficiente na pagina %s; aplicando OCR estruturado", idx)
        pagina = _ocr_completo_pagina(idx, imagem, avisos, alta_qualidade=True)
        if _ocr_rapido_texto_valido(pagina.texto):
            return pagina
        ampliada = _preprocessar_forte(_ampliar_imagem(imagem, 2.0))
        texto_hq = _tentar_image_to_string(
            ampliada,
            idx,
            "fallback ampliacao 2x + psm 6",
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
        _avisos_append(
            avisos,
            f"OCR insuficiente na pagina {idx} apos {len(tentativas)} tentativas rapidas "
            f"e OCR estruturado ({len(pagina.texto)} caracteres)",
        )
        return pagina

    return PageText(
        pagina=idx,
        texto=texto,
        metodo="ocr-rapido",
        blocks=[TextBlock(pagina=idx, bloco=1, texto=texto)] if texto else [],
    )


def _texto_ocr_rapido_pdf(pdf_path: Path, avisos: list[str], on_progress=None) -> list[PageText]:
    total_paginas = 0
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total_paginas = len(pdf.pages)
    except Exception:
        pass
    if total_paginas == 0:
        total_paginas = 1

    imagens = _carregar_paginas_em_lotes(
        pdf_path, list(range(1, total_paginas + 1)), SETTINGS.ocr_fast_dpi
    )
    paginas: list[PageText | None] = [None] * total_paginas
    total = len(imagens)
    itens = list(enumerate(imagens, start=1))

    def _job(pair: tuple[int, Image.Image]) -> tuple[int, PageText]:
        idx, imagem = pair
        return idx, _ocr_rapido_pagina(idx, imagem, avisos)

    def _done(done: int, tot: int) -> None:
        _progress_safe(
            on_progress,
            {
                "step": "ocr_fast",
                "current": done,
                "total": tot,
                "msg": f"[ocr-rapido] Pagina {done}/{tot} processada",
            },
        )

    indexed = map_parallel_indexed(
        itens,
        _job,
        label="ocr-rapido",
        on_done=_done if on_progress else None,
    )
    for _i, (idx, page) in indexed.items():
        paginas[idx - 1] = page
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

    logger.info("Executando OCR rapido com estruturacao so em paginas candidatas: %s", pdf_path)
    avisos: list[str] = []

    paginas_prontas = {}
    if cache:
        paginas_prontas = {p.pagina: p for p in cache.paginas if p.texto.strip()}

    total_paginas = 0
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total_paginas = len(pdf.pages)
    except Exception:
        pass
    if total_paginas == 0:
        total_paginas = 1

    paginas_ids = list(range(1, total_paginas + 1))
    paginas_set = set(paginas_ids)
    paginas: list[PageText | None] = [None] * total_paginas
    processadas = 0

    # Carrega so as paginas que nao estao no cache
    ids_para_carregar = [i for i in paginas_ids if i not in paginas_prontas]
    imagens = _carregar_paginas_em_lotes(pdf_path, ids_para_carregar, SETTINGS.ocr_fast_dpi)

    # Mapa: idx -> imagem
    mapa_imagens = dict(imagens)

    candidatas_ocr = []
    for idx in paginas_ids:
        if idx in paginas_prontas:
            paginas[idx - 1] = paginas_prontas[idx]
            processadas += 1
        elif idx in mapa_imagens:
            candidatas_ocr.append((idx, mapa_imagens[idx]))

    if candidatas_ocr:
        escolher_workers(
            n_tarefas=len(candidatas_ocr),
            forcar_amostra=True,
            label="ocr-rapido-inicio",
        )

        def _job(pair: tuple[int, Image.Image]) -> tuple[int, PageText]:
            idx, imagem = pair
            return idx, _ocr_rapido_pagina(idx, imagem, avisos)

        base_cache = processadas

        def _done(done: int, tot: int) -> None:
            cur = base_cache + done
            _progress_safe(
                on_progress,
                {
                    "step": "ocr_fast",
                    "current": min(cur, total),
                    "total": total,
                    "msg": f"OCR rapido: Pagina {min(cur, total)}/{total} processada",
                },
            )

        indexed = map_parallel_indexed(
            candidatas_ocr,
            _job,
            label="ocr-rapido",
            on_done=_done if on_progress else None,
        )
        for _i, (idx, page) in indexed.items():
            paginas[idx - 1] = page
            processadas += 1

    # Páginas que precisam de OCR estruturado
    paginas_candidatas = [
        pagina.pagina for pagina in paginas if pagina is not None and _texto_tem_candidato_inaja(pagina.texto)
    ]

    paginas_candidatas = [
        p for p in paginas_candidatas
        if paginas[p - 1] is None or paginas[p - 1].metodo not in ("ocr", "ocr-hq", "ocr-hq-string")
    ]

    if paginas_candidatas:
        logger.info("Paginas candidatas para OCR estruturado: %s", paginas_candidatas)
        _progress_safe(
            on_progress,
            f"Encontradas {len(paginas_candidatas)} paginas candidatas a OCR estruturado",
        )
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
