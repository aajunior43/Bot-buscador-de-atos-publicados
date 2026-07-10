"""
OCR result caching to .ocr.json files next to the original PDF.

Allows skipping expensive OCR on repeated runs.
Cache contains full text, per-page data and blocks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ocr.models import OCRResult, PageText, TextBlock

logger = logging.getLogger(__name__)

# Versão gravada nos caches novos. Leitura aceita v1 (sem campo) e v2.
_CACHE_VERSION = 2
_CACHE_VERSIONS_LEGIVEIS = {1, 2}


def _salvar_cache_ocr(pdf_path: Path, result: OCRResult) -> None:
    try:
        cache_path = pdf_path.with_suffix(".ocr.json")
        data = {
            "cache_version": _CACHE_VERSION,
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
        cache_path = pdf_path.with_suffix(".ocr.json")
        if not cache_path.exists():
            return None
        data = json.loads(cache_path.read_text(encoding="utf-8"))

        # Caches antigos não têm cache_version → tratar como v1 (ainda legível)
        versao = int(data.get("cache_version") or 1)
        if versao not in _CACHE_VERSIONS_LEGIVEIS:
            logger.info(
                "Cache OCR incompatível (versão %s), ignorando %s",
                versao,
                cache_path.name,
            )
            return None
        if "paginas" not in data or not isinstance(data["paginas"], list):
            logger.warning("Cache OCR sem lista de páginas: %s", cache_path)
            return None

        paginas = []
        for p in data["paginas"]:
            blocks = []
            for b in p.get("blocks", []) or []:
                bbox = tuple(b["bbox"]) if b.get("bbox") else None
                blocks.append(
                    TextBlock(
                        pagina=b.get("pagina", p.get("pagina", 0)),
                        bloco=b.get("bloco", 0),
                        texto=b.get("texto") or "",
                        bbox=bbox,
                    )
                )
            paginas.append(
                PageText(
                    pagina=p.get("pagina", 0),
                    texto=p.get("texto") or "",
                    metodo=p.get("metodo") or "ocr",
                    blocks=blocks,
                )
            )

        if not paginas:
            return None

        return OCRResult(
            texto_completo=data.get("texto_completo", ""),
            paginas=paginas,
            texto_path=pdf_path.with_suffix(".txt"),
            avisos=data.get("avisos", []),
        )
    except Exception:
        logger.exception("Falha ao carregar cache OCR para %s", pdf_path)
        return None
