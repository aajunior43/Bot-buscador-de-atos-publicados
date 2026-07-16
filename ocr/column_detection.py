from __future__ import annotations

import logging

from PIL import Image

logger = logging.getLogger(__name__)

# numpy e uma dependencia do pdf2image, entao deve estar sempre disponivel.
# O import encapsulado evita crash em ambientes sem numpy.
try:
    import numpy as np
    _TEM_NUMPY = True
except ImportError:
    _TEM_NUMPY = False


def _detectar_faixas_colunas(imagem: Image.Image) -> list[tuple[int, int]]:
    """Detecta colunas reais na pagina usando analise de projecao vertical.

    Retorna lista de tuplas (x0, x1) em coordenadas da imagem original,
    ou lista vazia se nao conseguir detectar (fallback para pagina inteira).
    """
    if not _TEM_NUMPY:
        logger.debug("numpy nao disponivel; pulando deteccao de colunas")
        return []
    try:
        largura_original = imagem.width
        altura_original = imagem.height

        escala = 800 / largura_original
        img_analise = imagem.convert("L").resize((800, max(200, int(altura_original * escala))))
        largura, altura = img_analise.size

        arr = np.array(img_analise)
        profile = arr.mean(axis=0)

        kernel = np.ones(7) / 7
        suave_arr = np.convolve(profile, kernel, mode='same')
        suave = suave_arr.tolist()

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
        logger.debug("Falha na deteccao de colunas, usando pagina inteira")
        return []
