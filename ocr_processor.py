from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from config import SETTINGS


logger = logging.getLogger(__name__)


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
    return cinza.point(lambda p: 255 if p > 180 else 0)


def _texto_pdfplumber(pdf_path: Path) -> list[PageText]:
    paginas: list[PageText] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
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
    )
    paginas_set = set(paginas)
    textos: dict[int, PageText] = {}
    numero_pagina = primeira
    for imagem in imagens:
        if numero_pagina not in paginas_set:
            numero_pagina += 1
            continue
        processada = _preprocessar(imagem)
        blocks = _extrair_blocos_tesseract(processada, numero_pagina, avisos)
        texto = "\n\n".join(block.texto for block in blocks)
        textos[numero_pagina] = PageText(
            pagina=numero_pagina,
            texto=texto.strip(),
            metodo="ocr",
            blocks=blocks,
        )
        numero_pagina += 1
    return textos


def _extrair_blocos_tesseract(
    imagem: Image.Image,
    pagina: int,
    avisos: list[str] | None = None,
) -> list[TextBlock]:
    try:
        data = pytesseract.image_to_data(
            imagem,
            lang=SETTINGS.ocr_language,
            config="--psm 3",
            output_type=pytesseract.Output.DICT,
            timeout=SETTINGS.ocr_timeout_seconds,
        )
    except RuntimeError:
        logger.exception("Timeout/falha de OCR estruturado na página %s", pagina)
        if avisos is not None:
            avisos.append(f"Timeout/falha de OCR na página {pagina}")
        return []

    colunas = max(1, SETTINGS.ocr_layout_columns)
    largura_coluna = max(1, imagem.width / colunas)
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
        centro_x = left + (width / 2)
        coluna = min(colunas - 1, max(0, int(centro_x // largura_coluna)))
        chave = (coluna, block_num, par_num, line_num)
        item = agrupado.setdefault(
            chave,
            {"words": [], "left": [], "top": [], "right": [], "bottom": []},
        )
        item["words"].append(palavra)  # type: ignore[index, union-attr]
        item["left"].append(left)  # type: ignore[index, union-attr]
        item["top"].append(top)  # type: ignore[index, union-attr]
        item["right"].append(left + width)  # type: ignore[index, union-attr]
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


def _texto_ocr_completo(pdf_path: Path, avisos: list[str] | None = None) -> list[PageText]:
    imagens = convert_from_path(str(pdf_path), dpi=SETTINGS.ocr_dpi)
    paginas: list[PageText] = []
    for idx, imagem in enumerate(imagens, start=1):
        processada = _preprocessar(imagem)
        blocks = _extrair_blocos_tesseract(processada, idx, avisos)
        texto = "\n\n".join(block.texto for block in blocks)
        paginas.append(
            PageText(pagina=idx, texto=texto.strip(), metodo="ocr", blocks=blocks)
        )
    return paginas


def _texto_ocr_rapido_pdf(pdf_path: Path, avisos: list[str]) -> list[PageText]:
    imagens = convert_from_path(str(pdf_path), dpi=SETTINGS.ocr_fast_dpi)
    paginas: list[PageText] = []
    for idx, imagem in enumerate(imagens, start=1):
        processada = _preprocessar(imagem)
        try:
            texto = pytesseract.image_to_string(
                processada,
                lang=SETTINGS.ocr_language,
                config="--psm 3",
                timeout=SETTINGS.ocr_fast_timeout_seconds,
            )
        except RuntimeError:
            logger.exception("Timeout/falha de OCR rápido na página %s", idx)
            avisos.append(f"Timeout/falha de OCR rápido na página {idx}")
            texto = ""
        texto = texto.strip()
        paginas.append(
            PageText(
                pagina=idx,
                texto=texto,
                metodo="ocr-rapido",
                blocks=[TextBlock(pagina=idx, bloco=1, texto=texto)] if texto else [],
            )
        )
    return paginas


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


def extrair_texto_rapido_com_estruturado_candidato(pdf_path: Path) -> OCRResult:
    logger.info("Executando OCR rápido com estruturação só em páginas candidatas: %s", pdf_path)
    avisos: list[str] = []
    paginas = _texto_ocr_rapido_pdf(pdf_path, avisos)
    paginas_candidatas = [
        pagina.pagina for pagina in paginas if _texto_tem_candidato_inaja(pagina.texto)
    ]
    if paginas_candidatas:
        logger.info("Páginas candidatas para OCR estruturado: %s", paginas_candidatas)
        estruturadas = _texto_ocr_paginas(pdf_path, paginas_candidatas, avisos)
        paginas = [estruturadas.get(pagina.pagina, pagina) for pagina in paginas]

    texto_completo = "\n\n".join(
        f"--- Página {pagina.pagina} ({pagina.metodo}) ---\n{pagina.texto}"
        for pagina in paginas
    )
    texto_path = pdf_path.with_suffix(".txt")
    texto_path.write_text(texto_completo, encoding="utf-8")
    return OCRResult(
        texto_completo=texto_completo,
        paginas=paginas,
        texto_path=texto_path,
        avisos=avisos,
    )


def extrair_texto(pdf_path: Path, force_ocr: bool | None = None) -> OCRResult:
    logger.info("Extraindo texto de %s", pdf_path)
    usar_force_ocr = SETTINGS.force_ocr if force_ocr is None else force_ocr
    avisos: list[str] = []

    if usar_force_ocr:
        logger.info("OCR forçado ativado; aplicando Tesseract em todas as páginas.")
        paginas = _texto_ocr_completo(pdf_path, avisos)
    else:
        paginas = _texto_pdfplumber(pdf_path)
        paginas_fracas = [
            pagina.pagina for pagina in paginas if _pagina_precisa_ocr(pagina, False)
        ]
        if not paginas:
            logger.info("PDF sem páginas extraídas por pdfplumber; aplicando OCR completo.")
            paginas = _texto_ocr_completo(pdf_path, avisos)
        elif paginas_fracas:
            logger.info(
                "Aplicando OCR híbrido nas páginas com pouco texto: %s",
                paginas_fracas,
            )
            ocr_por_pagina = _texto_ocr_paginas(pdf_path, paginas_fracas, avisos)
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
    return OCRResult(
        texto_completo=texto_completo,
        paginas=paginas,
        texto_path=texto_path,
        avisos=avisos,
    )
