"""
OCR Processing Package (refactored from monolithic ocr_processor.py)

Responsibilities split for maintainability:
- models.py: dataclasses for results (TextBlock, PageText, OCRResult)
- preprocessing.py: image enhancement, resizing, binarization
- column_detection.py: automatic multi-column layout detection
- tesseract.py: Tesseract interaction + block extraction heuristics
- cache.py: .ocr.json persistence
- extractor.py: high-level strategies (pdfplumber, hybrid, fast+structured, forced OCR)

Public API:
    from ocr import extrair_texto, extrair_texto_rapido_com_estruturado_candidato
    from ocr.models import PageText, TextBlock, OCRResult

Backward compatible via ocr_processor shim.
"""

from __future__ import annotations

import os

import pytesseract

from config import SETTINGS

# ─────────────────────────────────────────────────────────────
# One-time initialization (side effects)
# ─────────────────────────────────────────────────────────────

# Limit OpenMP threads to avoid oversubscription when using Tesseract in parallel
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Configure Tesseract executable path (critical on Windows)
if SETTINGS.tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = SETTINGS.tesseract_path


# Re-export clean public interface
from ocr.extractor import (
    extrair_texto,
    extrair_texto_rapido_com_estruturado_candidato,
)
from ocr.models import OCRResult, PageText, TextBlock

__all__ = [
    "extrair_texto",
    "extrair_texto_rapido_com_estruturado_candidato",
    "PageText",
    "TextBlock",
    "OCRResult",
]

