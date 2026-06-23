from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from config import SETTINGS
from ocr_processor import PageText, TextBlock


BASE_TERMS = [
    "Inajá",
    "Inaja",
    "Prefeitura de Inajá",
    "Prefeitura Municipal de Inajá",
    "Câmara de Inajá",
    "Câmara Municipal de Inajá",
    "Município de Inajá",
    "Municipio de Inaja",
    "75.771.400/0001-48",
]

GENERIC_TERMS = {"inajá", "inaja"}
CEP_RE = re.compile(r"\b87\d{3}-\d{3}\b")
VALOR_RE = re.compile(r"R\s*[$S]\s*[\d\.\,]+", re.IGNORECASE)
VALOR_DE_RE = re.compile(
    r"valor\s+de\s+(R\s*[$S]\s*[\d\.\,]+)", re.IGNORECASE
)
DATA_EXTENSO_RE = re.compile(
    r"\b\d{1,2}\s+de\s+[A-Za-zçÇãÃéÉ]+\s+de\s+\d{4}\b",
    re.IGNORECASE,
)
DATA_NUMERICA_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
TIPOS_ATO = [
    "DECRETO",
    "PORTARIA",
    "LEI",
    "EDITAL",
    "AVISO",
    "EXTRATO",
    "DISPENSA",
    "INEXIGIBILIDADE",
    "HOMOLOGAÇÃO",
    "ADJUDICAÇÃO",
    "TERMO",
    "RESOLUÇÃO",
]
SINAIS_OFICIAIS = [
    "prefeitura municipal",
    "camara municipal",
    "municipio de",
    "estado do parana",
    "cnpj",
    "lei organica",
    "licitacao",
    "processo",
    "decreto",
    "portaria",
    "edital",
]


@dataclass(frozen=True)
class DetectionResult:
    encontrado: bool
    edicao_id: int
    edicao_titulo: str
    paginas_com_mencao: list[int]
    trechos: list[dict]
    termos_encontrados: list[str]
    mencoes_db: list[dict]
    publicacoes: list[dict]


def _sem_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in normalizado if not unicodedata.combining(ch))


def _snippet(texto: str, inicio: int, fim: int, margem: int = 120) -> str:
    ini = max(0, inicio - margem)
    end = min(len(texto), fim + margem)
    trecho = " ".join(texto[ini:end].split())
    return f"...{trecho}..."


def _limpar(texto: str) -> str:
    return " ".join((texto or "").split())


def _contexto(texto: str, inicio: int, fim: int, margem: int = 180) -> str:
    ini = max(0, inicio - margem)
    end = min(len(texto), fim + margem)
    return texto[ini:end]


def _termos() -> list[str]:
    termos = BASE_TERMS + SETTINGS.extra_terms
    vistos: set[str] = set()
    resultado: list[str] = []
    for termo in termos:
        chave = _sem_acentos(termo).casefold()
        if termo and chave not in vistos:
            vistos.add(chave)
            resultado.append(termo)
    return resultado


def _cep_de_inaja(cep: str) -> bool:
    numeros = cep.replace("-", "")
    prefixos = SETTINGS.inaja_cep_prefixes or ["87670"]
    return any(numeros.startswith(prefixo.replace("-", "")) for prefixo in prefixos)


def _contexto_ignorado_para_mencao_generica(
    texto: str,
    inicio: int,
    fim: int,
) -> bool:
    contexto_norm = _sem_acentos(_contexto(texto, inicio, fim)).casefold()
    termos = [
        _sem_acentos(termo).casefold()
        for termo in SETTINGS.ignore_context_terms
        if termo.strip()
    ]
    encontrados = [termo for termo in termos if termo in contexto_norm]
    return "distribuicao avulsa" in contexto_norm or len(encontrados) >= 2


def _segmentos(pagina: PageText) -> list[TextBlock]:
    if pagina.blocks:
        return [block for block in pagina.blocks if block.texto.strip()]
    return [TextBlock(pagina=pagina.pagina, bloco=1, texto=pagina.texto)]


def _categoria(texto: str) -> str:
    texto_norm = _sem_acentos(texto).casefold()
    if _contexto_ignorado_para_mencao_generica(texto, 0, min(len(texto), 1)):
        return "patrocinador_distribuicao"
    if any(sinal in texto_norm for sinal in SINAIS_OFICIAIS):
        return "publicacao_oficial"
    return "materia_jornalistica"


def _extrair_orgao(texto: str) -> str | None:
    texto_norm = _sem_acentos(texto).casefold()
    if "prefeitura municipal" in texto_norm and "inaja" in texto_norm:
        return "Prefeitura Municipal de Inajá"
    if "camara municipal" in texto_norm and "inaja" in texto_norm:
        return "Câmara Municipal de Inajá"
    if "municipio" in texto_norm and "inaja" in texto_norm:
        return "Município de Inajá"
    if "75.771.400/0001-48" in texto:
        return "Prefeitura Municipal de Inajá"
    return None


def _extrair_tipo_numero(texto: str) -> tuple[str | None, str | None]:
    texto_norm = _sem_acentos(texto).upper()
    texto_original = texto.upper()
    for tipo in TIPOS_ATO:
        idx = texto_norm.find(_sem_acentos(tipo).upper())
        if idx == -1:
            continue
        janela = texto_original[idx : idx + 80]
        numero = None
        match = re.search(r"(?:N[º°O.]?\s*)?(\d{1,6}[./-]?\d{0,4})", janela)
        if match:
            numero = _normalizar_numero_ato(match.group(1))
        return tipo.title(), numero
    return None, None


def _normalizar_numero_ato(numero: str) -> str:
    valor = numero.strip().strip(".,;:")
    if "/" in valor or "-" in valor or "." in valor:
        return valor
    if len(valor) in {6, 7, 8}:
        return f"{valor[:-4]}/{valor[-4:]}"
    return valor


def _extrair_data(texto: str) -> str | None:
    match = DATA_EXTENSO_RE.search(texto)
    if match:
        return match.group(0)
    match = DATA_NUMERICA_RE.search(texto)
    if match:
        return match.group(0)
    return None


def _extrair_assunto(texto: str) -> str | None:
    linhas = [_limpar(linha) for linha in texto.splitlines() if _limpar(linha)]
    palavras_chave = [
        "abertura",
        "credito",
        "crédito",
        "licitacao",
        "licitação",
        "homologar",
        "adjudicar",
        "objeto",
        "contratação",
        "contratacao",
    ]
    for linha in linhas:
        linha_norm = _sem_acentos(linha).casefold()
        if any(_sem_acentos(palavra).casefold() in linha_norm for palavra in palavras_chave):
            return linha[:300]
    return linhas[0][:300] if linhas else None


def _extrair_valor(texto: str) -> str | None:
    match = VALOR_DE_RE.search(texto)
    if match:
        return _normalizar_valor(match.group(1))

    valores = [_normalizar_valor(match.group(0)) for match in VALOR_RE.finditer(texto)]
    if not valores:
        return None
    return max(valores, key=_valor_float)


def _normalizar_valor(valor: str) -> str:
    normalizado = re.sub(r"^R\s*[$S]", "R$", valor.strip(), flags=re.IGNORECASE)
    return normalizado.strip(".,;:")


def _valor_float(valor: str) -> float:
    numeros = re.sub(r"[^\d,\.]", "", valor)
    if "," in numeros:
        numeros = numeros.replace(".", "").replace(",", ".")
    try:
        return float(numeros)
    except ValueError:
        return 0.0


def _publicacao_do_segmento(segmento: TextBlock, termos: set[str]) -> dict | None:
    categoria = _categoria(segmento.texto)
    if categoria == "patrocinador_distribuicao":
        return None

    orgao = _extrair_orgao(segmento.texto)
    if not orgao and not termos:
        return None

    tipo, numero = _extrair_tipo_numero(segmento.texto)
    if categoria == "publicacao_oficial" and not (orgao or tipo):
        return None

    return {
        "pagina": segmento.pagina,
        "bloco": segmento.bloco,
        "categoria": categoria,
        "orgao": orgao,
        "tipo": tipo,
        "numero": numero,
        "data_documento": _extrair_data(segmento.texto),
        "assunto": _extrair_assunto(segmento.texto),
        "valor": _extrair_valor(segmento.texto),
        "trecho": _limpar(segmento.texto)[:2000],
    }


def detectar(
    edicao_id: int,
    edicao_titulo: str,
    paginas: list[PageText],
) -> DetectionResult:
    trechos: list[dict] = []
    termos_encontrados: set[str] = set()
    paginas_com_mencao: set[int] = set()
    mencoes_db: list[dict] = []
    publicacoes: list[dict] = []

    termos = _termos()
    for pagina in paginas:
        for segmento in _segmentos(pagina):
            texto = segmento.texto or ""
            texto_norm = _sem_acentos(texto).casefold()
            termos_segmento: set[str] = set()

            for termo in termos:
                termo_norm = _sem_acentos(termo).casefold()
                start = 0
                while termo_norm and (idx := texto_norm.find(termo_norm, start)) != -1:
                    fim = idx + len(termo_norm)
                    if (
                        termo_norm in GENERIC_TERMS
                        and _contexto_ignorado_para_mencao_generica(texto, idx, fim)
                    ):
                        start = fim
                        continue
                    trecho = _snippet(texto, idx, fim)
                    paginas_com_mencao.add(pagina.pagina)
                    termos_encontrados.add(termo)
                    termos_segmento.add(termo)
                    trechos.append(
                        {
                            "pagina": pagina.pagina,
                            "bloco": segmento.bloco,
                            "trecho": trecho,
                        }
                    )
                    mencoes_db.append(
                        {"pagina": pagina.pagina, "trecho": trecho, "termo": termo}
                    )
                    start = fim

            for match in CEP_RE.finditer(texto):
                cep = match.group(0)
                if not _cep_de_inaja(cep):
                    continue
                if _contexto_ignorado_para_mencao_generica(
                    texto, match.start(), match.end()
                ):
                    continue
                trecho = _snippet(texto, match.start(), match.end())
                paginas_com_mencao.add(pagina.pagina)
                termos_encontrados.add(f"CEP {cep}")
                termos_segmento.add(f"CEP {cep}")
                trechos.append(
                    {
                        "pagina": pagina.pagina,
                        "bloco": segmento.bloco,
                        "trecho": trecho,
                    }
                )
                mencoes_db.append(
                    {"pagina": pagina.pagina, "trecho": trecho, "termo": f"CEP {cep}"}
                )

            publicacao = _publicacao_do_segmento(segmento, termos_segmento)
            if publicacao:
                publicacoes.append(publicacao)

    return DetectionResult(
        encontrado=bool(trechos or publicacoes),
        edicao_id=edicao_id,
        edicao_titulo=edicao_titulo,
        paginas_com_mencao=sorted(paginas_com_mencao),
        trechos=trechos,
        termos_encontrados=sorted(termos_encontrados),
        mencoes_db=mencoes_db,
        publicacoes=publicacoes,
    )
