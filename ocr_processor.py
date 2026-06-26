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
_WORKERS = min(8, max(2, os.cpu_count() or 2))

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
    equalizada = ImageOps.equalize(nitido, mask=None)
    return equalizada


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


def _detectar_numero_colunas(imagem: Image.Image) -> int:
    try:
        img_gray = imagem.convert("L").resize((400, 200))
        largura, altura = img_gray.size

        def pct_whiteness(pct: float) -> float:
            x_center = int(largura * pct)
            total_val = 0
            count = 0
            for dx in range(-2, 3):
                x = x_center + dx
                if 0 <= x < largura:
                    total_val += sum(img_gray.getpixel((x, y)) for y in range(altura))
                    count += altura
            return total_val / count if count > 0 else 0

        w3 = (pct_whiteness(0.33) + pct_whiteness(0.66)) / 2
        w4 = (pct_whiteness(0.25) + pct_whiteness(0.50) + pct_whiteness(0.75)) / 3

        return 4 if w4 > w3 else 3
    except Exception:
        return 3


def _extrair_blocos_tesseract(
    imagem: Image.Image,
    pagina: int,
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    colunas = max(1, SETTINGS.ocr_layout_columns)
    if colunas > 1:
        colunas_detectadas = _detectar_numero_colunas(imagem)
        logger.info("Página %s: colunas detectadas = %s", pagina, colunas_detectadas)
        return _extrair_blocos_tesseract_por_coluna(imagem, pagina, colunas_detectadas, avisos)

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
    colunas: int,
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    largura = imagem.width
    recortes: list[tuple[int, Image.Image, int]] = []
    for coluna in range(colunas):
        x0 = round((largura * coluna) / colunas)
        x1 = round((largura * (coluna + 1)) / colunas)
        if x1 <= x0:
            continue
        recorte = imagem.crop((x0, 0, x1, imagem.height))
        recortes.append((coluna, recorte, x0))

    blocos: list[TextBlock] = []
    with ThreadPoolExecutor(max_workers=len(recortes)) as pool:
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
    try:
        data = pytesseract.image_to_data(
            imagem,
            lang=SETTINGS.ocr_language,
            config="--psm 4",
            output_type=pytesseract.Output.DICT,
            timeout=timeout,
        )
    except RuntimeError:
        logger.exception(
            "Timeout/falha de OCR estruturado na página %s coluna %s",
            pagina,
            coluna + 1,
        )
        if avisos is not None:
            avisos.append(f"Timeout/falha de OCR na página {pagina}, coluna {coluna + 1}")
        return []

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
    try:
        texto = pytesseract.image_to_string(
            processada,
            lang=SETTINGS.ocr_language,
            config="--psm 3",
            timeout=SETTINGS.ocr_fast_timeout_seconds,
        )
    except RuntimeError:
        logger.warning(
            "Timeout de OCR rápido na página %s — retentando com resolução reduzida", idx
        )
        avisos.append(f"Timeout/falha de OCR rápido na página {idx}")
        # Retry com imagem na metade da resolução para páginas muito densas
        try:
            reduzida = processada.resize(
                (processada.width // 2, processada.height // 2),
                Image.LANCZOS,
            )
            texto = pytesseract.image_to_string(
                reduzida,
                lang=SETTINGS.ocr_language,
                config="--psm 3",
                timeout=60,
            )
            logger.info("OCR rápido na página %s recuperado com resolução reduzida", idx)
        except RuntimeError:
            logger.exception(
                "Timeout persistente de OCR rápido na página %s mesmo com resolução reduzida", idx
            )
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

