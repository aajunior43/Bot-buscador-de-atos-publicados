"""
Data models for OCR results.

TextBlock: a coherent block of text (often a paragraph or column section).
PageText:  extracted text for one page + metadata.
OCRResult: full result for a PDF (all pages + combined text + cache info).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
