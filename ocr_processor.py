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
_WORKERS = 1

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


def _binarizar(imagem: Image.Image) -> Image.Image:
    """Binarização por threshold de Otsu — eficaz em imagens de baixa qualidade."""
    cinza = imagem.convert("L")
    return cinza.point(lambda x: 0 if x < 140 else 255, "1")


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
    with ThreadPoolExecutor(max_workers=1) as pool:
        future_to_coluna = {
            pool.submit(
                _extrair_blocos_tesseract_imagem,
                recorte,
                pagina=pagina,
                coluna=coluna,
                x_offset=x0,
                timeout=SETTINGS.ocr_timeout_seconds,
                avisos=avisos,
            ): coluna
            for coluna, recorte, x0 in recortes
        }
        for future in as_completed(future_to_coluna):
            blocos.extend(future.result())

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
    data = _tentar_image_to_data(imagem, pagina, coluna, timeout, "--psm 4")
    if data is not None:
        return _agrupar_data_tesseract(data, pagina, coluna, x_offset)

    binarizada = _binarizar(imagem)
    data = _tentar_image_to_data(binarizada, pagina, coluna, timeout, "--psm 6")
    if data is not None:
        logger.info("OCR na página %s coluna %s recuperado (binarização + psm 6)", pagina, coluna + 1)
        return _agrupar_data_tesseract(data, pagina, coluna, x_offset)

    data = _tentar_image_to_data(imagem, pagina, coluna, timeout, "--psm 3")
    if data is not None:
        logger.info("OCR na página %s coluna %s recuperado (psm 3 automático)", pagina, coluna + 1)
        return _agrupar_data_tesseract(data, pagina, coluna, x_offset)

    logger.warning("Falha de OCR na página %s coluna %s após todas as estratégias", pagina, coluna + 1)
    if avisos is not None:
        avisos.append(f"Falha de OCR na página {pagina}, coluna {coluna + 1}")
    return []


def _tentar_image_to_data(
    imagem: Image.Image,
    pagina: int,
    coluna: int,
    timeout: int,
    psm: str,
) -> dict | None:
    """Tenta executar image_to_data; retorna None se falhar."""
    try:
        return pytesseract.image_to_data(
            imagem,
            lang=SETTINGS.ocr_language,
            config=psm,
            output_type=pytesseract.Output.DICT,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("OCR estruturado falhou (página %s coluna %s psm=%s): %s", pagina, coluna + 1, psm, exc)
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


def _ocr_completo_pagina(idx: int, imagem: Image.Image, avisos: list[str]) -> PageText:
    processada = _preprocessar(imagem)
    blocks = _extrair_blocos_tesseract(processada, idx, avisos)
    texto = "\n\n".join(block.texto for block in blocks)
    return PageText(pagina=idx, texto=texto.strip(), metodo="ocr", blocks=blocks)


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
    return [p for p in paginas if p is not None]


def _ocr_rapido_pagina(idx: int, imagem: Image.Image, avisos: list[str]) -> PageText:
    processada = _preprocessar(imagem)
    timeout_base = SETTINGS.ocr_fast_timeout_seconds
    timeout_retry = max(60, timeout_base * 2)

    texto = None
    # Estratégia 1: OCR rápido com psm 3 (padrão)
    try:
        texto = pytesseract.image_to_string(
            processada,
            lang=SETTINGS.ocr_language,
            config="--psm 3",
            timeout=timeout_base,
        )
    except (RuntimeError, Exception) as exc:
        logger.warning("OCR rápido falhou na página %s (psm 3): %s", idx, exc)

    # Estratégia 2: resolução reduzida + timeout maior
    if not texto or not texto.strip():
        try:
            reduzida = processada.resize(
                (processada.width // 2, processada.height // 2),
                Image.LANCZOS,
            )
            texto = pytesseract.image_to_string(
                reduzida,
                lang=SETTINGS.ocr_language,
                config="--psm 3",
                timeout=timeout_retry,
            )
            logger.info("OCR rápido na página %s recuperado (resolução reduzida)", idx)
        except (RuntimeError, Exception) as exc:
            logger.warning("OCR rápido falhou na página %s (resolução reduzida): %s", idx, exc)

    # Estratégia 3: binarização + psm 6 (bloco de texto uniforme)
    if not texto or not texto.strip():
        try:
            binarizada = _binarizar(processada)
            texto = pytesseract.image_to_string(
                binarizada,
                lang=SETTINGS.ocr_language,
                config="--psm 6",
                timeout=timeout_retry,
            )
            logger.info("OCR rápido na página %s recuperado (binarização + psm 6)", idx)
        except (RuntimeError, Exception) as exc:
            logger.warning("OCR rápido falhou na página %s (binarização): %s", idx, exc)

    if not texto or not texto.strip():
        avisos.append(f"Timeout/falha de OCR rápido na página {idx} após 3 tentativas")
        texto = ""

    texto = texto.strip()
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
            texto_completo=data["texto_completo"],
            paginas=paginas,
            texto_path=pdf_path.with_suffix(".txt"),
            avisos=data.get("avisos", [])
        )
    except Exception:
        logger.exception("Falha ao carregar cache OCR para %s", pdf_path)
        return None


def extrair_texto_rapido_com_estruturado_candidato(pdf_path: Path, on_progress=None) -> OCRResult:
    if not SETTINGS.force_ocr:
        cache = _carregar_cache_ocr(pdf_path)
        if cache:
            logger.info("OCR recuperado do cache com sucesso para %s", pdf_path)
            return cache

    logger.info("Executando OCR rápido com estruturação só em páginas candidatas: %s", pdf_path)
    avisos: list[str] = []
    paginas = _texto_ocr_rapido_pdf(pdf_path, avisos, on_progress=on_progress)
    paginas_candidatas = [
        pagina.pagina for pagina in paginas if _texto_tem_candidato_inaja(pagina.texto)
    ]
    if paginas_candidatas:
        logger.info("Páginas candidatas para OCR estruturado: %s", paginas_candidatas)
        if on_progress:
            on_progress(f"Encontradas {len(paginas_candidatas)} páginas candidatas a OCR estruturado")
        estruturadas = _texto_ocr_paginas(pdf_path, paginas_candidatas, avisos, on_progress=on_progress)
        paginas = [estruturadas.get(pagina.pagina, pagina) for pagina in paginas]

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

