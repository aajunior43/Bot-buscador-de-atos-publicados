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
    flags: list[dict] = []
    for key in ("orgao", "tipo", "numero", "valor", "data_documento"):
        val = out.get(key)
        if val and not campo_ancorado_no_trecho(str(val), trecho):
            logger.info(
                "Anti-alucinação: campo %s=%r não ancorado no trecho — removido",
                key,
                val,
            )
            flags.append({"campo": key, "motivo": f"não encontrado no trecho: {val!r}"})
            out[key] = None
            out.setdefault("_campos_rejeitados", []).append(key)
    out["_validacao"] = {
        "ok": len(flags) == 0,
        "flags": flags,
    }
    return out


# Vocabulário controlado de temas (#12)
TEMAS_CANONICOS = (
    "licitacao",
    "contrato",
    "obra",
    "saude",
    "educacao",
    "rh",
    "fiscal",
    "assistencia",
    "infraestrutura",
    "meio_ambiente",
    "cultura",
    "transporte",
    "outros",
)

_TEMA_ALIASES = {
    "licitação": "licitacao",
    "licitacoes": "licitacao",
    "pregão": "licitacao",
    "pregao": "licitacao",
    "dispensa": "licitacao",
    "contratos": "contrato",
    "aditivo": "contrato",
    "obras": "obra",
    "asfalto": "obra",
    "paviment": "obra",
    "saúde": "saude",
    "saude": "saude",
    "educação": "educacao",
    "escola": "educacao",
    "pessoal": "rh",
    "nomeacao": "rh",
    "nomeação": "rh",
    "exoneracao": "rh",
    "portaria": "rh",
    "rgf": "fiscal",
    "rreo": "fiscal",
    "lrf": "fiscal",
    "balanco": "fiscal",
    "balanço": "fiscal",
    "assistência": "assistencia",
    "social": "assistencia",
}


def normalizar_tema(tag: str | None) -> str | None:
    if not tag or not str(tag).strip():
        return None
    t = _norm(str(tag).strip())
    t = t.replace(" ", "_").replace("-", "_")
    if t in TEMAS_CANONICOS:
        return t
    if t in _TEMA_ALIASES:
        return _TEMA_ALIASES[t]
    for alias, canon in _TEMA_ALIASES.items():
        if alias in t or t in alias:
            return canon
    for canon in TEMAS_CANONICOS:
        if canon in t:
            return canon
    return "outros"


def normalizar_lista_temas(raw) -> list[str]:
    items: list[str] = []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",") if x.strip()]
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    for x in raw:
        n = normalizar_tema(str(x))
        if n and n not in seen:
            seen.add(n)
            items.append(n)
        if len(items) >= 5:
            break
    return items


def montar_checklist_local(pub: dict, ia: dict | None = None) -> dict:
    """Checklist de transparência (regras + dicas da IA)."""
    ia = ia or {}
    numero = pub.get("numero") or ia.get("numero")
    data = pub.get("data_documento") or ia.get("data_documento")
    valor = pub.get("valor") or ia.get("valor")
    orgao = pub.get("orgao") or ia.get("orgao")
    assunto = pub.get("assunto") or ia.get("assunto") or pub.get("resumo_ia")
    trecho = (pub.get("trecho") or ia.get("texto_corrigido") or "").casefold()
    fundamentacao = bool(
        re.search(r"fundament|base\s+legal|nos termos|lei\s+n", trecho)
    )
    chk_ia = ia.get("checklist") if isinstance(ia.get("checklist"), dict) else {}
    itens = {
        "tem_numero": bool(numero) or bool(chk_ia.get("tem_numero")),
        "tem_data": bool(data) or bool(chk_ia.get("tem_data")),
        "tem_valor": bool(valor) or bool(chk_ia.get("tem_valor")),
        "tem_orgao": bool(orgao) or bool(chk_ia.get("tem_orgao")),
        "tem_objeto": bool(assunto) or bool(chk_ia.get("tem_objeto")),
        "tem_fundamentacao": fundamentacao
        or bool(chk_ia.get("tem_fundamentacao")),
    }
    # Tipos em que valor costuma ser obrigatório
    tipo_n = _norm(str(pub.get("tipo") or ia.get("tipo") or ""))
    exige_valor = any(
        k in tipo_n
        for k in ("contrato", "dispensa", "homolog", "extrato", "aditivo", "pregao")
    )
    faltando = [k.replace("tem_", "") for k, ok in itens.items() if not ok]
    if not exige_valor and "valor" in faltando:
        faltando = [f for f in faltando if f != "valor"]
        itens["tem_valor"] = True  # N/A tratado como ok
    n_ok = sum(1 for v in itens.values() if v)
    score = int(round(100 * n_ok / max(1, len(itens))))
    return {
        **itens,
        "faltando": faltando,
        "score": score,
        "exige_valor": exige_valor,
    }


def parse_valor_float(valor: str | None) -> float | None:
    try:
        from database import parse_valor_monetario

        return parse_valor_monetario(valor)
    except Exception:
        return None


def _mediana(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def detectar_anomalia(
    pub: dict,
    historico_valores: list[float] | None = None,
) -> tuple[bool, str]:
    """Heurística de anomalia (#3). Retorna (é_anomalia, motivo)."""
    if not getattr(SETTINGS, "ai_anomalia", True):
        return False, ""
    valor = parse_valor_float(pub.get("valor"))
    tipo_n = _norm(str(pub.get("tipo") or ""))
    motivos: list[str] = []

    if valor is not None and valor >= 100_000:
        if any(k in tipo_n for k in ("dispensa", "contrato", "homolog", "aditivo")):
            motivos.append(f"valor alto R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    hist = [v for v in (historico_valores or []) if v and v > 0]
    # não incluir o próprio valor se for o único
    if valor is not None and len(hist) >= 5:
        med = _mediana(hist)
        if med and med > 0 and valor >= 3.0 * med:
            motivos.append(
                f"valor {valor / med:.1f}× a mediana do tipo ({med:,.0f})".replace(",", ".")
            )

    # importância 5 + valor
    try:
        imp = int(pub.get("importancia") or 0)
    except (TypeError, ValueError):
        imp = 0
    if imp >= 5 and valor is not None and valor >= 50_000:
        motivos.append("importância máxima com valor elevado")

    if not motivos:
        return False, ""
    return True, "; ".join(motivos[:3])


def eh_radar_lrf(pub: dict) -> bool:
    blob = _norm(
        " ".join(
            str(pub.get(k) or "")
            for k in ("tipo", "temas", "assunto", "resumo_ia", "trecho")
        )
    )
    keys = (
        "rgf",
        "rreo",
        "lrf",
        "balanco",
        "gestao fiscal",
        "relatorio de gestao",
        "demonstrativo",
        "responsabilidade fiscal",
    )
    return any(k in blob for k in keys)


def query_similares_de_pub(pub: dict) -> str:
    parts = [
        pub.get("tipo"),
        pub.get("orgao"),
        pub.get("assunto") or pub.get("resumo_ia"),
        pub.get("temas"),
    ]
    return " ".join(str(p) for p in parts if p)

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
    """Agrega estatísticas do dia e grava em settings (opcionalmente reescrito pela IA)."""
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

    texto_base = montar_resumo_diario_texto(
        dia=hoje,
        n_pubs=int(n_pubs or 0),
        n_edicoes_inaja=int(n_ed or 0),
        tipos=tipos,
        valores_txt=valores_txt,
        destaques=destaques,
    )
    texto = texto_base
    fonte = "regras"
    if getattr(SETTINGS, "ai_resumo_diario", True) and int(n_pubs or 0) > 0:
        try:
            from ai_processor import gerar_resumo_periodo_ia

            ia_txt = gerar_resumo_periodo_ia(texto_base, dia=hoje)
            if ia_txt:
                texto = ia_txt
                fonte = "ia"
        except Exception:
            logger.debug("resumo diário IA falhou — mantendo regras", exc_info=True)

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
                "fonte": fonte,
                "gerado_em": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ),
    )
    logger.info("Resumo diário gerado (%s): %s pubs (fonte=%s)", hoje, n_pubs, fonte)
    return {"dia": hoje, "texto": texto, "n_pubs": n_pubs, "fonte": fonte}
