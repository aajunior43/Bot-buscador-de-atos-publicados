"""Camada de inteligência do monitor: score, validação, busca e resumo.

Não depende de API externa (exceto resumo diário opcional via ai_processor).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from config import CNPJ_INAJA_PREFIXES, MUNICIPIOS_VIZINHOS, SETTINGS

logger = logging.getLogger(__name__)


def _sem_acentos(texto: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(ch)
    )


def _norm(texto: str) -> str:
    return _sem_acentos(texto).casefold()


# ---------------------------------------------------------------------------
# Score de candidatura (0–100): “parece ato oficial de Inajá?”
# ---------------------------------------------------------------------------

_PESOS = {
    "inaja_explicito": 35,
    "cnpj_inaja": 25,
    "cep_inaja": 15,
    "orgao_oficial": 20,
    "tipo_ato": 12,
    "valor_monetario": 5,
    "vizinho": -40,
    "materia": -15,
}


@dataclass
class ScoreResultado:
    score: int
    motivos: list[str]
    prioridade: int  # 0=alta, 1=normal, 2=baixa (para ORDER BY)


def score_texto_candidatura(texto: str, *, titulo: str = "") -> ScoreResultado:
    """Pontua trecho/OCR/título quanto a ser publicação oficial de Inajá."""
    blob = f"{titulo}\n{texto}"
    n = _norm(blob)
    score = 0
    motivos: list[str] = []

    if re.search(r"\binaja\b", n) or "inaja-pr" in n or "inaja/pr" in n:
        score += _PESOS["inaja_explicito"]
        motivos.append("menciona Inajá")
    elif "ina ja" in n or "inaj a" in n:
        score += 20
        motivos.append("possível Inajá (OCR fraco)")

    for pref in CNPJ_INAJA_PREFIXES or ("75771400", "76970318"):
        digits = re.sub(r"\D", "", pref)
        if digits and digits in re.sub(r"\D", "", blob):
            score += _PESOS["cnpj_inaja"]
            motivos.append("CNPJ Inajá")
            break

    if "87670" in re.sub(r"\D", "", blob):
        score += _PESOS["cep_inaja"]
        motivos.append("CEP 87670")

    orgaos = (
        "prefeitura municipal",
        "camara municipal",
        "municipio de",
        "conselho municipal",
        "fundo municipal",
    )
    if any(o in n for o in orgaos):
        score += _PESOS["orgao_oficial"]
        motivos.append("órgão oficial")

    tipos = (
        "decreto",
        "portaria",
        "lei municipal",
        "edital",
        "dispensa",
        "homolog",
        "extrato de contrato",
        "termo de",
        "rgf",
        "rreo",
        "licitacao",
        "pregão",
        "pregao",
    )
    if any(t in n for t in tipos):
        score += _PESOS["tipo_ato"]
        motivos.append("tipo de ato")

    if re.search(r"r\$\s*[\d.,]+", n):
        score += _PESOS["valor_monetario"]
        motivos.append("valor monetário")

    for mun in MUNICIPIOS_VIZINHOS:
        mun_n = _norm(mun)
        if mun_n and mun_n in n and "inaja" not in n:
            # vizinho sem Inajá → forte penalidade
            score += _PESOS["vizinho"]
            motivos.append(f"vizinho:{mun}")
            break
        if mun_n and f"prefeitura municipal de {mun_n}" in n:
            score += _PESOS["vizinho"]
            motivos.append(f"prefeitura vizinha:{mun}")
            break

    materia = ("colunista", "esporte", "futebol", "obituario", "classificados", "publicidade")
    if any(m in n for m in materia) and "inaja" not in n:
        score += _PESOS["materia"]
        motivos.append("possível matéria")

    score = max(0, min(100, score))
    if score >= 55:
        prioridade = 0
    elif score >= 25:
        prioridade = 1
    else:
        prioridade = 2
    return ScoreResultado(score=score, motivos=motivos, prioridade=prioridade)


def score_titulo_edicao(titulo: str | None, data: str | None = None) -> ScoreResultado:
    """Score barato só com metadados (fila de pendentes)."""
    return score_texto_candidatura("", titulo=titulo or "")


# ---------------------------------------------------------------------------
# Anti-alucinação: campo deve “caber” no trecho
# ---------------------------------------------------------------------------

def _tokens_significativos(texto: str) -> set[str]:
    n = _norm(texto)
    toks = re.findall(r"[a-z0-9]{3,}", n)
    stop = {
        "para",
        "com",
        "por",
        "dos",
        "das",
        "uma",
        "que",
        "nao",
        "mais",
        "como",
        "este",
        "esta",
        "pelo",
        "pela",
        "seu",
        "sua",
    }
    return {t for t in toks if t not in stop}


def campo_ancorado_no_trecho(campo: str | None, trecho: str, *, min_ratio: float = 0.35) -> bool:
    """True se o valor do campo parece suportado pelo trecho OCR."""
    if not campo or not str(campo).strip():
        return True
    if not trecho:
        return False
    c = str(campo).strip()
    tn = _norm(trecho).replace(" ", "")
    cn = _norm(c).replace(" ", "")
    # substring direta (números, valores)
    if len(cn) >= 4 and cn in tn:
        return True
    # valor monetário
    if "r$" in cn or re.search(r"\d+,\d{2}", c):
        digs = re.sub(r"\D", "", c)
        if len(digs) >= 3 and digs in re.sub(r"\D", "", trecho):
            return True
    # tokens do campo presentes no trecho
    toks = _tokens_significativos(c)
    if not toks:
        return True
    trecho_toks = _tokens_significativos(trecho)
    hit = sum(1 for t in toks if t in trecho_toks)
    return (hit / len(toks)) >= min_ratio


def validar_campos_ia(pub: dict, ia: dict) -> dict:
    """Zera/ajusta campos da IA não ancorados no trecho (anti-alucinação)."""
    trecho = pub.get("trecho") or ia.get("texto_corrigido") or ""
    out = dict(ia)
    for key in ("orgao", "tipo", "numero", "valor", "data_documento"):
        val = out.get(key)
        if val and not campo_ancorado_no_trecho(str(val), trecho):
            logger.info(
                "Anti-alucinação: campo %s=%r não ancorado no trecho — removido",
                key,
                val,
            )
            out[key] = None
            out.setdefault("_campos_rejeitados", []).append(key)
    # resumo: se inventar número/valor absurdo, mantém mas marca
    return out


# ---------------------------------------------------------------------------
# Busca semântica leve (TF ranking, sem embeddings externos)
# ---------------------------------------------------------------------------

def _tokenize_query(q: str) -> list[str]:
    n = _norm(q)
    return [t for t in re.findall(r"[a-z0-9]{2,}", n) if len(t) >= 2]


def rankear_publicacoes(
    query: str,
    rows: list[dict[str, Any]],
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Ranqueia publicações por sobreposição de termos + boosts."""
    toks = _tokenize_query(query)
    if not toks:
        return rows[:limit]

    def score_row(r: dict) -> float:
        blob = " ".join(
            str(r.get(k) or "")
            for k in (
                "tipo",
                "numero",
                "orgao",
                "assunto",
                "resumo_ia",
                "trecho",
                "valor",
                "edicao_titulo",
            )
        )
        bn = _norm(blob)
        s = 0.0
        for t in toks:
            if t in bn:
                s += 3.0 + bn.count(t) * 0.3
            # prefix match em tokens do blob
            elif any(w.startswith(t) for w in re.findall(r"[a-z0-9]{3,}", bn)):
                s += 1.5
        if r.get("resumo_ia"):
            s += 0.5
        if r.get("valor"):
            s += 0.2
        return s

    scored = [(score_row(r), r) for r in rows]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], str(x[1].get("data_publicacao") or "")))
    out = []
    for s, r in scored[:limit]:
        d = dict(r)
        d["_score_busca"] = round(s, 2)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Resumo diário (regras + opcionalmente IA)
# ---------------------------------------------------------------------------

def montar_resumo_diario_texto(
    *,
    dia: str,
    n_pubs: int,
    n_edicoes_inaja: int,
    tipos: list[tuple[str, int]],
    valores_txt: str,
    destaques: list[str],
) -> str:
    linhas = [
        f"Resumo do dia {dia}",
        f"• {n_pubs} publicação(ões) estruturada(s) em {n_edicoes_inaja} edição(ões) com Inajá",
    ]
    if tipos:
        top = ", ".join(f"{t} ({c})" for t, c in tipos[:5])
        linhas.append(f"• Tipos: {top}")
    if valores_txt:
        linhas.append(f"• Valores (indicador): {valores_txt}")
    if destaques:
        linhas.append("• Destaques:")
        for d in destaques[:5]:
            linhas.append(f"  – {d}")
    if n_pubs == 0:
        linhas.append("• Nenhum ato novo indexado neste recorte.")
    return "\n".join(linhas)


def gerar_resumo_diario_from_db(conn_factory=None) -> dict[str, Any]:
    """Agrega estatísticas do dia e grava em settings."""
    import database

    hoje = date.today().isoformat()
    with database.connect() as conn:
        n_pubs = conn.execute(
            """
            SELECT COUNT(*) FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            WHERE date(p.criado_em) = date('now', 'localtime')
               OR (p.criado_em IS NULL AND e.data_publicacao = ?)
            """,
            (hoje,),
        ).fetchone()[0]
        # fallback: pubs de edições com data = hoje
        n_pubs_data = conn.execute(
            """
            SELECT COUNT(*) FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            WHERE e.data_publicacao = ?
            """,
            (hoje,),
        ).fetchone()[0]
        n_pubs = max(int(n_pubs or 0), int(n_pubs_data or 0))

        n_ed = conn.execute(
            """
            SELECT COUNT(DISTINCT e.id) FROM edicoes e
            WHERE e.tem_inaja = 1 AND e.data_publicacao = ?
            """,
            (hoje,),
        ).fetchone()[0]

        tipos_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(p.tipo), ''), 'Outro') AS tipo, COUNT(*) AS n
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            WHERE e.data_publicacao = ? OR date(p.criado_em) = date('now', 'localtime')
            GROUP BY 1 ORDER BY n DESC LIMIT 8
            """,
            (hoje,),
        ).fetchall()
        tipos = [(r[0], int(r[1])) for r in tipos_rows]

        destaques_rows = conn.execute(
            """
            SELECT p.tipo, p.numero, p.orgao, p.valor, p.resumo_ia, p.assunto, e.data_publicacao
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            WHERE e.data_publicacao = ? OR date(p.criado_em) = date('now', 'localtime')
            ORDER BY
              CASE WHEN p.valor IS NOT NULL AND p.valor != '' THEN 0 ELSE 1 END,
              p.id DESC
            LIMIT 8
            """,
            (hoje,),
        ).fetchall()

    destaques = []
    for r in destaques_rows:
        tipo, num, orgao, valor, resumo, assunto, _dt = r
        head = " ".join(x for x in [(tipo or "Ato"), num or ""] if x).strip()
        tail = (resumo or assunto or orgao or "")[:80]
        extra = f" — {valor}" if valor else ""
        destaques.append(f"{head}{extra}: {tail}".strip(": "))

    try:
        fin = database.somar_valores_publicacoes(deduplicar=True)
        valores_txt = database.formatar_reais(float(fin.get("total") or 0))
    except Exception:
        valores_txt = ""

    texto = montar_resumo_diario_texto(
        dia=hoje,
        n_pubs=int(n_pubs or 0),
        n_edicoes_inaja=int(n_ed or 0),
        tipos=tipos,
        valores_txt=valores_txt,
        destaques=destaques,
    )
    database.set_setting("resumo_diario_data", hoje)
    database.set_setting("resumo_diario_texto", texto)
    database.set_setting(
        "resumo_diario_json",
        __import__("json").dumps(
            {
                "dia": hoje,
                "n_pubs": n_pubs,
                "n_edicoes_inaja": n_ed,
                "tipos": tipos,
                "gerado_em": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ),
    )
    logger.info("Resumo diário gerado (%s): %s pubs", hoje, n_pubs)
    return {"dia": hoje, "texto": texto, "n_pubs": n_pubs}
