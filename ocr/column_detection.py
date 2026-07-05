"""
Automatic column detection for multi-column newspaper layouts.

Uses vertical projection analysis (with numpy fallback) to find gutters
between columns. This allows better block extraction with Tesseract.
"""

from __future__ import annotations

import logging

from PIL import Image

logger = logging.getLogger(__name__)


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
        # Qualquer falha na detecção de colunas → fallback para página inteira
        logger.debug("Falha na detecção de colunas, usando página inteira")
        return []
