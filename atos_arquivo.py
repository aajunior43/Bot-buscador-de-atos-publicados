"""Espelho organizado de atos oficiais em disco.

Estrutura::

    atos/
      README.md
      INDICE.md
      por-data/AAAA/MM/DD/{nn}_{Tipo}_{Numero}__{Orgao}.md|.json
      por-tipo/{Tipo}/AAAA/...
      por-orgao/{Orgao}/AAAA/...

A fonte da verdade continua sendo o SQLite; esta pasta é regenerável.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from config import SETTINGS

logger = logging.getLogger(__name__)

_INVALID_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_DASH = re.compile(r"-{2,}")


def slugify(texto: str | None, *, default: str = "sem-nome", max_len: int = 60) -> str:
    """Nome seguro para pasta/arquivo no Windows."""
    if not texto or not str(texto).strip():
        return default
    s = unicodedata.normalize("NFKD", str(texto).strip())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("/", "-").replace("\\", "-")
    s = _INVALID_WIN.sub("", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^\w\-]+", "", s, flags=re.UNICODE)
    s = _MULTI_DASH.sub("-", s).strip("-._")
    if not s:
        return default
    return s[:max_len].rstrip("-")


def _parse_data_edicao(data: str | None) -> tuple[str, str, str]:
    """Retorna (ano, mes, dia) ou ('sem-data','00','00')."""
    if not data:
        return "sem-data", "00", "00"
    raw = str(data).strip()[:10]
    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(1), m.group(2), m.group(3)
    # DD/MM/YYYY
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", raw)
    if m:
        return m.group(3), m.group(2), m.group(1)
    return "sem-data", "00", "00"


def _data_iso(ano: str, mes: str, dia: str) -> str:
    if ano == "sem-data":
        return "sem-data"
    return f"{ano}-{mes}-{dia}"


def _nome_base_ato(
    seq: int,
    tipo: str | None,
    numero: str | None,
    orgao: str | None,
) -> str:
    tipo_s = slugify(tipo, default="Ato", max_len=40)
    num_s = slugify(numero, default="s-n", max_len=30) if numero else "s-n"
    org_s = slugify(orgao, default="Orgao-nao-identificado", max_len=50)
    return f"{seq:02d}_{tipo_s}_{num_s}__{org_s}"


def root_dir(base: Path | None = None) -> Path:
    return Path(base or SETTINGS.atos_dir)


def garantir_readme(root: Path | None = None) -> None:
    r = root_dir(root)
    r.mkdir(parents=True, exist_ok=True)
    readme = r / "README.md"
    if readme.exists():
        return
    readme.write_text(
        """# Atos oficiais de Inajá-PR

Pasta **gerada automaticamente** pelo monitor (espelho do banco SQLite).

## Como navegar

| Pasta | Uso |
|-------|-----|
| `por-data/AAAA/MM/DD/` | **Principal** — atos do dia da edição do jornal |
| `por-tipo/` | Atalho: todos os Decretos, Portarias, etc. |
| `por-orgao/` | Atalho por órgão (Prefeitura, Câmara…) |
| `INDICE.md` | Visão geral e totais |

Cada ato tem um arquivo `.md` (leitura) e `.json` (dados estruturados).

## Rebuild completo

```bash
python scripts/reconstruir_atos.py
```

Não edite estes arquivos à mão — rode o rebuild ou reprocesse a edição.
""",
        encoding="utf-8",
    )


def _yaml_escape(val: Any) -> str:
    if val is None:
        return '""'
    s = str(val).replace("\n", " ").replace('"', "'")
    if re.search(r"[:#\[\]{}]", s) or s != s.strip():
        return f'"{s}"'
    return s if s else '""'


def _conteudo_markdown(pub: dict, edicao: dict, seq: int) -> str:
    tipo = pub.get("tipo") or "Ato"
    numero = pub.get("numero") or ""
    titulo = f"{tipo} {numero}".strip()
    resumo = pub.get("resumo_ia") or pub.get("assunto") or ""
    trecho = (pub.get("trecho") or pub.get("texto_corrigido") or "").strip()
    linhas = [
        "---",
        f"id: {pub.get('id') or ''}",
        f"edicao_id: {edicao.get('id') or pub.get('edicao_id') or ''}",
        f"seq: {seq}",
        f"tipo: {_yaml_escape(tipo)}",
        f"numero: {_yaml_escape(numero)}",
        f"orgao: {_yaml_escape(pub.get('orgao'))}",
        f"data_edicao: {_yaml_escape(edicao.get('data_publicacao'))}",
        f"data_documento: {_yaml_escape(pub.get('data_documento'))}",
        f"valor: {_yaml_escape(pub.get('valor'))}",
        f"pagina: {pub.get('pagina') or ''}",
        f"categoria: {_yaml_escape(pub.get('categoria_ia') or pub.get('categoria'))}",
        f"edicao_titulo: {_yaml_escape(edicao.get('titulo'))}",
        f"gerado_em: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        f"# {titulo}",
        "",
        f"**Órgão:** {pub.get('orgao') or '—'}",
        f"**Data da edição (jornal):** {edicao.get('data_publicacao') or '—'}",
        f"**Data do documento:** {pub.get('data_documento') or '—'}",
        f"**Página no jornal:** {pub.get('pagina') or '—'}",
        f"**Valor:** {pub.get('valor') or '—'}",
        "",
        "## Resumo",
        "",
        resumo or "_Sem resumo._",
        "",
        "## Trecho",
        "",
        "```text",
        trecho or "(vazio)",
        "```",
        "",
    ]
    return "\n".join(linhas)


def _payload_json(pub: dict, edicao: dict, seq: int) -> dict:
    return {
        "id": pub.get("id"),
        "edicao_id": edicao.get("id") or pub.get("edicao_id"),
        "seq": seq,
        "tipo": pub.get("tipo"),
        "numero": pub.get("numero"),
        "orgao": pub.get("orgao"),
        "data_edicao": edicao.get("data_publicacao"),
        "data_documento": pub.get("data_documento"),
        "valor": pub.get("valor"),
        "pagina": pub.get("pagina"),
        "assunto": pub.get("assunto"),
        "resumo_ia": pub.get("resumo_ia"),
        "categoria": pub.get("categoria_ia") or pub.get("categoria"),
        "trecho": pub.get("trecho"),
        "texto_corrigido": pub.get("texto_corrigido"),
        "feedback": pub.get("feedback"),
        "edicao_titulo": edicao.get("titulo"),
        "edicao_url": edicao.get("url"),
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }


def _escrever_par(path_base: Path, md: str, data: dict) -> None:
    path_base.parent.mkdir(parents=True, exist_ok=True)
    path_base.with_suffix(".md").write_text(md, encoding="utf-8")
    path_base.with_suffix(".json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def espelhar_edicao(
    edicao_id: int,
    publicacoes: list[dict],
    *,
    edicao_meta: dict | None = None,
    root: Path | None = None,
) -> int:
    """Grava atos de uma edição em `atos/por-data/...` (+ atalhos).

    Returns:
        Quantidade de atos escritos.
    """
    if not getattr(SETTINGS, "atos_espelhar", True):
        return 0
    if not publicacoes:
        return 0

    import database

    root = root_dir(root)
    garantir_readme(root)

    meta = dict(edicao_meta or {})
    if not meta.get("id"):
        meta["id"] = edicao_id
    if not meta.get("data_publicacao") or not meta.get("titulo"):
        with database.connect() as conn:
            row = conn.execute(
                "SELECT id, titulo, data_publicacao, url FROM edicoes WHERE id = ?",
                (edicao_id,),
            ).fetchone()
            if row:
                meta.setdefault("titulo", row["titulo"])
                meta.setdefault("data_publicacao", row["data_publicacao"])
                meta.setdefault("url", row["url"])
                meta["id"] = row["id"]

    ano, mes, dia = _parse_data_edicao(meta.get("data_publicacao"))
    data_iso = _data_iso(ano, mes, dia)
    dia_dir = root / "por-data" / ano / mes / dia

    # Limpa só o dia desta edição (evita lixo de rebuilds parciais do mesmo dia+edição)
    # Estratégia: reescreve todos os atos do dia a partir do banco no rebuild;
    # aqui, remove arquivos que batem com esta edicao_id nos json existentes.
    if dia_dir.exists():
        for jp in dia_dir.glob("*.json"):
            try:
                d = json.loads(jp.read_text(encoding="utf-8"))
                if int(d.get("edicao_id") or 0) == int(edicao_id):
                    jp.unlink(missing_ok=True)
                    jp.with_suffix(".md").unlink(missing_ok=True)
            except Exception:
                continue

    # Sequência: continua após arquivos já existentes no dia
    existentes = sorted(dia_dir.glob("*.md")) if dia_dir.exists() else []
    seq0 = len([p for p in existentes if not p.name.startswith("_")])

    escritos = 0
    entradas_dia: list[tuple[str, dict]] = []

    for i, pub in enumerate(publicacoes, start=1):
        seq = seq0 + i
        base_name = _nome_base_ato(
            seq, pub.get("tipo"), pub.get("numero"), pub.get("orgao")
        )
        path_data = dia_dir / base_name
        md = _conteudo_markdown(pub, meta, seq)
        payload = _payload_json(pub, meta, seq)
        _escrever_par(path_data, md, payload)
        escritos += 1
        entradas_dia.append((base_name, payload))

        if getattr(SETTINGS, "atos_por_tipo", True):
            tipo_slug = slugify(pub.get("tipo"), default="Outros", max_len=40)
            atalho = (
                root
                / "por-tipo"
                / tipo_slug
                / ano
                / f"{data_iso}__{base_name}"
            )
            _escrever_par(atalho, md, payload)

        if getattr(SETTINGS, "atos_por_orgao", True):
            org_slug = slugify(
                pub.get("orgao"), default="Orgao-nao-identificado", max_len=50
            )
            atalho = (
                root
                / "por-orgao"
                / org_slug
                / ano
                / f"{data_iso}__{base_name}"
            )
            _escrever_par(atalho, md, payload)

    _atualizar_indice_dia(dia_dir, data_iso, meta, entradas_dia)
    _atualizar_indice_mes(root / "por-data" / ano / mes, ano, mes)
    _atualizar_indice_ano(root / "por-data" / ano, ano)
    _atualizar_indice_geral(root)

    logger.info(
        "Atos espelhados: %s arquivo(s) em %s",
        escritos,
        dia_dir.relative_to(root) if root in dia_dir.parents or dia_dir == root else dia_dir,
    )
    return escritos


def _atualizar_indice_dia(
    dia_dir: Path,
    data_iso: str,
    meta: dict,
    entradas: list[tuple[str, dict]],
) -> None:
    dia_dir.mkdir(parents=True, exist_ok=True)
    # Lista todos os md do dia (rebuild parcial)
    todos: list[tuple[str, dict]] = []
    for jp in sorted(dia_dir.glob("*.json")):
        try:
            todos.append((jp.stem, json.loads(jp.read_text(encoding="utf-8"))))
        except Exception:
            continue
    if not todos and entradas:
        todos = entradas

    linhas = [
        f"# Atos — {data_iso}",
        "",
        f"Edição de referência: **{meta.get('titulo') or '—'}**  ",
        f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Total neste dia: **{len(todos)}** ato(s)",
        "",
        "| # | Tipo | Número | Órgão | Valor | Arquivo |",
        "|---|------|--------|-------|-------|---------|",
    ]
    for stem, p in todos:
        linhas.append(
            f"| {p.get('seq') or '—'} | {p.get('tipo') or '—'} | {p.get('numero') or '—'} | "
            f"{(p.get('orgao') or '—')[:40]} | {p.get('valor') or '—'} | "
            f"[md](./{stem}.md) · [json](./{stem}.json) |"
        )
    linhas.append("")
    (dia_dir / "_indice-dia.md").write_text("\n".join(linhas), encoding="utf-8")


def _atualizar_indice_mes(mes_dir: Path, ano: str, mes: str) -> None:
    if not mes_dir.exists():
        return
    dias = sorted(
        [d for d in mes_dir.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda p: p.name,
    )
    linhas = [
        f"# Atos — {ano}-{mes}",
        "",
        f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "| Dia | Atos | Índice |",
        "|-----|------|--------|",
    ]
    total = 0
    for d in dias:
        n = len(list(d.glob("*.md"))) - (
            1 if (d / "_indice-dia.md").exists() else 0
        )
        n = max(0, n)
        total += n
        linhas.append(
            f"| {d.name} | {n} | [_indice-dia.md](./{d.name}/_indice-dia.md) |"
        )
    linhas.insert(3, f"Total no mês: **{total}** ato(s)")
    linhas.insert(4, "")
    (mes_dir / "_indice-mes.md").write_text("\n".join(linhas), encoding="utf-8")


def _atualizar_indice_ano(ano_dir: Path, ano: str) -> None:
    if not ano_dir.exists():
        return
    meses = sorted(
        [d for d in ano_dir.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda p: p.name,
    )
    linhas = [
        f"# Atos — {ano}",
        "",
        f"Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "| Mês | Índice |",
        "|-----|--------|",
    ]
    for m in meses:
        linhas.append(f"| {m.name} | [_indice-mes.md](./{m.name}/_indice-mes.md) |")
    linhas.append("")
    (ano_dir / "_indice-ano.md").write_text("\n".join(linhas), encoding="utf-8")


def _atualizar_indice_geral(root: Path) -> None:
    por_data = root / "por-data"
    anos = []
    if por_data.exists():
        anos = sorted(
            [
                d
                for d in por_data.iterdir()
                if d.is_dir() and (d.name.isdigit() or d.name == "sem-data")
            ],
            key=lambda p: p.name,
            reverse=True,
        )
    total_md = 0
    if por_data.exists():
        total_md = sum(
            1
            for p in por_data.rglob("*.md")
            if not p.name.startswith("_") and p.name != "README.md"
        )
    linhas = [
        "# Índice geral — Atos de Inajá-PR",
        "",
        f"Atualizado: **{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}**  ",
        f"Total de arquivos de ato (por-data): **{total_md}**",
        "",
        "## Por data",
        "",
    ]
    for a in anos:
        rel = f"por-data/{a.name}/_indice-ano.md"
        if (a / "_indice-ano.md").exists():
            linhas.append(f"- [{a.name}](./{rel})")
        else:
            linhas.append(f"- {a.name}")
    linhas.extend(
        [
            "",
            "## Atalhos",
            "",
            "- [por-tipo/](./por-tipo/) — agrupado por tipo de ato",
            "- [por-orgao/](./por-orgao/) — agrupado por órgão",
            "",
            "## Como regenerar",
            "",
            "```bash",
            "python scripts/reconstruir_atos.py",
            "```",
            "",
        ]
    )
    (root / "INDICE.md").write_text("\n".join(linhas), encoding="utf-8")


def reconstruir_tudo_do_banco(
    *,
    root: Path | None = None,
    limpar: bool = True,
) -> dict[str, int]:
    """Rebuild completo a partir do SQLite."""
    import database

    root = root_dir(root)
    database.init_db()

    if limpar and root.exists():
        for sub in ("por-data", "por-tipo", "por-orgao"):
            p = root / sub
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)

    garantir_readme(root)

    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, e.titulo AS edicao_titulo, e.data_publicacao, e.url AS edicao_url
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            ORDER BY e.data_publicacao ASC, p.edicao_id ASC, p.id ASC
            """
        ).fetchall()

    from collections import defaultdict

    por_ed: dict[int, list[dict]] = defaultdict(list)
    metas: dict[int, dict] = {}
    for r in rows:
        d = dict(r)
        eid = int(d["edicao_id"])
        por_ed[eid].append(d)
        metas[eid] = {
            "id": eid,
            "titulo": d.get("edicao_titulo"),
            "data_publicacao": d.get("data_publicacao"),
            "url": d.get("edicao_url"),
        }

    total = 0
    for eid, pubs in por_ed.items():
        # Reescreve do zero por edição (sem acumular seq de runs anteriores)
        total += _espelhar_edicao_rebuild(eid, pubs, metas[eid], root)

    # Reindex completo
    por_data = root / "por-data"
    if por_data.exists():
        for ano_dir in por_data.iterdir():
            if not ano_dir.is_dir():
                continue
            _atualizar_indice_ano(ano_dir, ano_dir.name)
            for mes_dir in ano_dir.iterdir():
                if mes_dir.is_dir() and mes_dir.name.isdigit():
                    _atualizar_indice_mes(mes_dir, ano_dir.name, mes_dir.name)
                    for dia_dir in mes_dir.iterdir():
                        if dia_dir.is_dir():
                            entradas = []
                            for jp in sorted(dia_dir.glob("*.json")):
                                try:
                                    entradas.append(
                                        (
                                            jp.stem,
                                            json.loads(
                                                jp.read_text(encoding="utf-8")
                                            ),
                                        )
                                    )
                                except Exception:
                                    pass
                            data_iso = _data_iso(
                                ano_dir.name, mes_dir.name, dia_dir.name
                            )
                            _atualizar_indice_dia(
                                dia_dir, data_iso, {}, entradas
                            )
    _atualizar_indice_geral(root)
    logger.info("Rebuild atos: %s arquivo(s) em %s", total, root)
    return {"atos": total, "edicoes": len(por_ed)}


def _espelhar_edicao_rebuild(
    edicao_id: int,
    publicacoes: list[dict],
    meta: dict,
    root: Path,
) -> int:
    """Como espelhar_edicao, mas sempre começa seq em 1 (rebuild limpo do dia parcial)."""
    # Temporariamente limpa só desta edicao no dia — já limpamos árvores no rebuild
    ano, mes, dia = _parse_data_edicao(meta.get("data_publicacao"))
    data_iso = _data_iso(ano, mes, dia)
    dia_dir = root / "por-data" / ano / mes / dia
    escritos = 0
    entradas: list[tuple[str, dict]] = []
    for i, pub in enumerate(publicacoes, start=1):
        base_name = _nome_base_ato(
            i, pub.get("tipo"), pub.get("numero"), pub.get("orgao")
        )
        path_data = dia_dir / base_name
        md = _conteudo_markdown(pub, meta, i)
        payload = _payload_json(pub, meta, i)
        _escrever_par(path_data, md, payload)
        escritos += 1
        entradas.append((base_name, payload))
        if getattr(SETTINGS, "atos_por_tipo", True):
            tipo_slug = slugify(pub.get("tipo"), default="Outros", max_len=40)
            _escrever_par(
                root / "por-tipo" / tipo_slug / ano / f"{data_iso}__{base_name}",
                md,
                payload,
            )
        if getattr(SETTINGS, "atos_por_orgao", True):
            org_slug = slugify(
                pub.get("orgao"), default="Orgao-nao-identificado", max_len=50
            )
            _escrever_par(
                root / "por-orgao" / org_slug / ano / f"{data_iso}__{base_name}",
                md,
                payload,
            )
    _atualizar_indice_dia(dia_dir, data_iso, meta, entradas)
    return escritos
