from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from config import SETTINGS


import os
os.environ["OMP_THREAD_LIMIT"] = "1"

logger = logging.getLogger(__name__)
_WORKERS = SETTINGS.ocr_max_workers
_TESSERACT_OEM = "--oem 1"


def _tesseract_config(psm: int) -> str:
    return f"{_TESSERACT_OEM} --psm {psm}"


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


# Configura caminho do Tesseract (necessário no Windows quando não está no PATH)
if SETTINGS.tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = SETTINGS.tesseract_path



@dataclass(frozen=True)
class TextBlock:
    pagina: int
    bloco: int
    texto: str
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class PageText:
    pagina: int
    texto: str
    metodo: str
    blocks: list[TextBlock] | None = None


@dataclass(frozen=True)
class OCRResult:
    texto_completo: str
    paginas: list[PageText]
    texto_path: Path
    avisos: list[str]


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


def _carregar_pagina_pdf(pdf_path: Path, pagina: int, dpi: int) -> Image.Image | None:
    imagens = convert_from_path(
        str(pdf_path),
        dpi=dpi,
        first_page=pagina,
        last_page=pagina,
        poppler_path=SETTINGS.poppler_path or None,
    )
    return imagens[0] if imagens else None


def _ocr_rapido_min_chars() -> int:
    return max(SETTINGS.min_text_chars_per_page, SETTINGS.ocr_fast_min_chars)


def _ocr_rapido_texto_valido(texto: str) -> bool:
    return len((texto or "").strip()) >= _ocr_rapido_min_chars()


def _binarizar(imagem: Image.Image, threshold: int = 140) -> Image.Image:
    """Binarização por threshold — eficaz em imagens de baixa qualidade."""
    cinza = imagem.convert("L")
    return cinza.point(lambda x: 0 if x < threshold else 255, "1")


def _texto_pdfplumber(pdf_path: Path, on_progress=None) -> list[PageText]:
    paginas: list[PageText] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        for idx, page in enumerate(pdf.pages, start=1):
            if on_progress:
                on_progress(f"Extraindo texto nativo: Página {idx}/{total}")
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
    import threading
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
                    on_progress(f"OCR estruturado (candidatas): Página {processadas}/{total} processada")
    return textos


def _detectar_faixas_colunas(imagem: Image.Image) -> list[tuple[int, int]]:
    """Detecta colunas reais na página usando análise de projeção vertical.

    Retorna lista de tuplas (x0, x1) em coordenadas da imagem original,
    ou lista vazia se não conseguir detectar (fallback para página inteira).
    """
    try:
        largura_original = imagem.width
        altura_original = imagem.height

        escala = 800 / largura_original
        img_analise = imagem.convert("L").resize((800, max(200, int(altura_original * escala))))
        largura, altura = img_analise.size

        try:
            import numpy as np
            arr = np.array(img_analise)          # shape: (altura, largura)
            profile = arr.mean(axis=0)           # média por coluna
            
            # Convolução 1D simples para suavização rápida
            kernel = np.ones(7) / 7
            # padding para manter o mesmo tamanho
            suave_arr = np.convolve(profile, kernel, mode='same')
            suave = suave_arr.tolist()
        except ImportError:
            profile = [sum(img_analise.getpixel((x, y)) for y in range(altura)) / altura for x in range(largura)]
            janela = 3
            suave = []
            for i in range(largura):
                start = max(0, i - janela)
                end = min(largura, i + janela + 1)
                suave.append(sum(profile[start:end]) / (end - start))

        threshold_branco = 220
        largura_min_gutter = max(4, largura // 50)

        gutters = []
        i = 0
        while i < largura:
            if suave[i] > threshold_branco:
                inicio = i
                while i < largura and suave[i] > threshold_branco:
                    i += 1
                if i - inicio >= largura_min_gutter:
                    gutters.append((inicio + i) // 2)
            else:
                i += 1

        boundaries = [0] + gutters + [largura]
        faixas_escaladas = []
        largura_min_coluna = largura // 14

        for i in range(len(boundaries) - 1):
            x0 = boundaries[i]
            x1 = boundaries[i + 1]
            if x1 - x0 >= largura_min_coluna:
                faixas_escaladas.append((x0, x1))

        if len(faixas_escaladas) <= 1:
            return []

        fator = largura_original / largura
        return [(round(x0 * fator), round(x1 * fator)) for x0, x1 in faixas_escaladas]
    except Exception:
        return []


def _extrair_blocos_tesseract(
    imagem: Image.Image,
    pagina: int,
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    faixas = _detectar_faixas_colunas(imagem)
    if faixas:
        logger.info("Página %s: %s coluna(s) detectada(s)", pagina, len(faixas))
        return _extrair_blocos_tesseract_por_coluna(imagem, pagina, faixas, avisos)

    return _extrair_blocos_tesseract_imagem(
        imagem,
        pagina=pagina,
        coluna=0,
        x_offset=0,
        timeout=SETTINGS.ocr_timeout_seconds,
        avisos=avisos,
    )


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
                logger.info(
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
                    logger.info(
                        "OCR na página %s coluna %s recuperado (%s)",
                        pagina,
                        coluna + 1,
                        estrategia,
                    )
                return blocos

    logger.debug("OCR sem texto na página %s coluna %s após todas as estratégias", pagina, coluna + 1)
    if avisos is not None:
        avisos.append(f"Falha de OCR na página {pagina}, coluna {coluna + 1}")
    return []


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
    import threading
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
                    on_progress(f"OCR estruturado completo: Página {processadas}/{total} processada")

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
                    f"OCR alta resolução ({SETTINGS.ocr_retry_dpi} DPI) em {len(fracas)} página(s)"
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
    timeout_curto = timeout_base  # usa o mesmo timeout na 1ª tentativa para evitar falhas prematuras em páginas densas
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
    import threading
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
                    on_progress(f"OCR rápido: Página {processadas}/{total} processada")
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
    return "inaja" in texto_norm or any(
        termo in texto_norm and "inaja" in texto_norm for termo in termos
    )


def _salvar_cache_ocr(pdf_path: Path, result: OCRResult) -> None:
    try:
        import json
        cache_path = pdf_path.with_suffix(".ocr.json")
        data = {
            "texto_completo": result.texto_completo,
            "avisos": result.avisos,
            "paginas": [
                {
                    "pagina": p.pagina,
                    "texto": p.texto,
                    "metodo": p.metodo,
                    "blocks": [
                        {
                            "pagina": b.pagina,
                            "bloco": b.bloco,
                            "texto": b.texto,
                            "bbox": list(b.bbox) if b.bbox else None
                        }
                        for b in (p.blocks or [])
                    ]
                }
                for p in result.paginas
            ]
        }
        cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Falha ao salvar cache OCR para %s", pdf_path)


def _carregar_cache_ocr(pdf_path: Path) -> OCRResult | None:
    try:
        import json
        cache_path = pdf_path.with_suffix(".ocr.json")
        if not cache_path.exists():
            return None
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        
        paginas = []
        for p in data["paginas"]:
            blocks = []
            for b in p.get("blocks", []):
                bbox = tuple(b["bbox"]) if b.get("bbox") else None
                blocks.append(
                    TextBlock(
                        pagina=b["pagina"],
                        bloco=b["bloco"],
                        texto=b["texto"],
                        bbox=bbox
                    )
                )
            paginas.append(
                PageText(
                    pagina=p["pagina"],
                    texto=p["texto"],
                    metodo=p["metodo"],
                    blocks=blocks
                )
            )
        
        return OCRResult(
            texto_completo=data.get("texto_completo", ""),
            paginas=paginas,
            texto_path=pdf_path.with_suffix(".txt"),
            avisos=data.get("avisos", [])
        )
    except Exception:
        logger.exception("Falha ao carregar cache OCR para %s", pdf_path)
        return None


def extrair_texto_rapido_com_estruturado_candidato(pdf_path: Path, on_progress=None) -> OCRResult:
    cache = _carregar_cache_ocr(pdf_path)
    if cache and not SETTINGS.force_ocr:
        logger.info("OCR recuperado do cache com sucesso para %s", pdf_path)
        return cache

    logger.info("Executando OCR rápido com estruturação só em páginas candidatas: %s", pdf_path)
    avisos: list[str] = []
    
    # Se houver um cache parcial (ex: com parte das páginas feitas e salvas no .ocr.json), podemos reaproveitar
    paginas_prontas = {}
    if cache:
        paginas_prontas = {p.pagina: p for p in cache.paginas if p.texto.strip()}

    # Carrega imagens
    imagens = convert_from_path(
        str(pdf_path),
        dpi=SETTINGS.ocr_fast_dpi,
        poppler_path=SETTINGS.poppler_path or None,
        thread_count=min(4, os.cpu_count() or 1),
    )
    
    paginas: list[PageText | None] = [None] * len(imagens)
    total = len(imagens)
    processadas = 0
    import threading
    lock = threading.Lock()

    # Filtra quais imagens realmente precisam de processamento rápido
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
    
    # Filtra páginas candidatas a OCR estruturado
    paginas_candidatas = [
        pagina.pagina for pagina in paginas if pagina is not None and _texto_tem_candidato_inaja(pagina.texto)
    ]
    
    # Se a página já estava estruturada (ex: no cache como ocr-hq ou ocr), não precisa rodar de novo
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
                on_progress(f"Aplicando OCR híbrido em {len(paginas_fracas)} páginas com pouco texto nativo")
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

