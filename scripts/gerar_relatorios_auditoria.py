"""Gera CSV de publicações + relatório de edições só-menção.

Saída em ./relatorios/
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_processor import normalizar_tipo_ato  # noqa: E402
from database import formatar_reais, init_db, normalizar_tipos_publicacoes_existentes, somar_valores_publicacoes  # noqa: E402
from exporter import exportar_csv  # noqa: E402

# Classificação manual da auditoria (2026-07-09) das 14 edições tem_inaja sem pubs.
AUDITORIA_SO_MENCAO = [
    {
        "data": "2026-02-05",
        "classificacao": "menção_legitima",
        "motivo": "Matéria jornalística sobre reciclagem; sem ato oficial estruturado.",
    },
    {
        "data": "2026-02-24",
        "classificacao": "falso_negativo_provavel",
        "motivo": "Convocação de audiência pública da Câmara (OCR ruim, mas é publicação oficial).",
    },
    {
        "data": "2026-03-15",
        "classificacao": "menção_legitima",
        "motivo": "Matéria Dia Internacional da Mulher.",
    },
    {
        "data": "2026-03-22",
        "classificacao": "menção_legitima",
        "motivo": "Matéria sobre programa social / vereador; sem bloco de lei/portaria extraído.",
    },
    {
        "data": "2026-04-09",
        "classificacao": "menção_legitima",
        "motivo": "Menção em texto religioso / ruído.",
    },
    {
        "data": "2026-04-16",
        "classificacao": "menção_legitima",
        "motivo": "Matéria sobre entrega de ambulância.",
    },
    {
        "data": "2026-04-19",
        "classificacao": "falso_negativo_provavel",
        "motivo": "Página de atos oficiais com CNPJ de Inajá misturada a Ourizona (OCR); Portaria/atos legíveis.",
    },
    {
        "data": "2026-04-30",
        "classificacao": "menção_legitima",
        "motivo": "Matérias e indicação de vereadores; não extrato de ato formal isolado.",
    },
    {
        "data": "2026-05-12",
        "classificacao": "falso_negativo",
        "motivo": "Crédito adicional suplementar da Prefeitura (CNPJ + prefeito) — ato oficial não virou publicação.",
    },
    {
        "data": "2026-05-17",
        "classificacao": "falso_negativo_provavel",
        "motivo": "Cabeçalho Prefeito do Município + OCR muito degradado; possível decreto/portaria.",
    },
    {
        "data": "2026-05-24",
        "classificacao": "menção_legitima",
        "motivo": "OCR fraco; menções + possível ruído (farmácia); sem bloco de ato claro.",
    },
    {
        "data": "2026-05-28",
        "classificacao": "falso_negativo",
        "motivo": "Portarias, PME 2026-2036, requerimento Câmara, CEP 87670 — vários atos oficiais perdidos.",
    },
    {
        "data": "2026-06-07",
        "classificacao": "falso_negativo_provavel",
        "motivo": "Cabeçalho Prefeitura + CNPJ; texto OCR degradado, provável ato na página.",
    },
    {
        "data": "2026-06-11",
        "classificacao": "incerto",
        "motivo": "Menção mínima 'IPAL DE INAJÁ'; OCR fraco; pode ser ruído de página de atos.",
    },
]


def main() -> None:
    init_db()
    out = ROOT / "relatorios"
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Backfill tipos
    n_tipos = normalizar_tipos_publicacoes_existentes()
    print(f"Tipos normalizados no DB: {n_tipos}")

    # CSV completo
    csv_path = out / f"publicacoes_auditoria_{ts}.csv"
    csv_path.write_text(exportar_csv(), encoding="utf-8-sig")
    # também cópia estável
    (out / "publicacoes_auditoria_latest.csv").write_text(
        exportar_csv(), encoding="utf-8-sig"
    )
    print(f"CSV: {csv_path}")

    fin = somar_valores_publicacoes(deduplicar=True)
    fin_bruto = somar_valores_publicacoes(deduplicar=False)

    # Markdown de auditoria das 14
    conn = sqlite3.connect(ROOT / "jornal_monitor.db")
    conn.row_factory = sqlite3.Row
    so_mencao = conn.execute(
        """
        SELECT e.id, e.data_publicacao, e.titulo,
          (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id=e.id) AS menc
        FROM edicoes e
        WHERE e.tem_inaja=1
          AND NOT EXISTS (SELECT 1 FROM publicacoes p WHERE p.edicao_id=e.id)
        ORDER BY e.data_publicacao
        """
    ).fetchall()

    by_date = {a["data"]: a for a in AUDITORIA_SO_MENCAO}
    linhas_md = [
        "# Auditoria de resultados — Monitor Inajá",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Financeiro (indicador)",
        "",
        f"- Soma **bruta** de valores: {formatar_reais(float(fin_bruto['total']))} "
        f"({fin_bruto['n_com_valor']} linhas com valor)",
        f"- Soma **deduplicada** (órgão+tipo+número): {formatar_reais(float(fin['total']))} "
        f"({fin['n_unicos']} chaves únicas)",
        "",
        "> A deduplicação evita contar o mesmo contrato em aviso + extrato quando o número coincide.",
        "",
        "## Publicações",
        "",
        f"- Arquivo: `{csv_path.name}` e `publicacoes_auditoria_latest.csv`",
        f"- Tipos reescritos no banco nesta execução: **{n_tipos}**",
        "",
        "## Edições com Inajá e 0 publicações",
        "",
        "| Data | ID | Menções | Classificação | Motivo |",
        "|------|---:|--------:|---------------|--------|",
    ]
    for r in so_mencao:
        info = by_date.get(r["data_publicacao"] or "", {})
        cls = info.get("classificacao", "nao_classificado")
        motivo = info.get("motivo", "—")
        linhas_md.append(
            f"| {r['data_publicacao']} | {r['id']} | {r['menc']} | `{cls}` | {motivo} |"
        )

    contagem: dict[str, int] = {}
    for a in AUDITORIA_SO_MENCAO:
        contagem[a["classificacao"]] = contagem.get(a["classificacao"], 0) + 1
    linhas_md += [
        "",
        "### Resumo da classificação",
        "",
    ]
    for k, v in sorted(contagem.items()):
        linhas_md.append(f"- **{k}**: {v}")
    linhas_md += [
        "",
        "### Ação recomendada",
        "",
        "- `falso_negativo` / `falso_negativo_provavel`: reprocessar com "
        "`python scripts/reprocessar_cache_ocr.py --falsos-negativos`",
        "- `menção_legitima`: manter (matéria/ruído); não é bug do detector",
        "- Telegram: definir `TELEGRAM_CHAT_ID` no `.env` (token existe, chat não)",
        "",
    ]

    # Tipos atuais
    linhas_md += ["## Tipos após normalização", ""]
    for r in conn.execute(
        "SELECT COALESCE(tipo,'(sem)') t, COUNT(*) n FROM publicacoes GROUP BY t ORDER BY n DESC"
    ):
        linhas_md.append(f"- {r['t']}: {r['n']}")

    md_path = out / f"auditoria_mencoes_{ts}.md"
    md_path.write_text("\n".join(linhas_md) + "\n", encoding="utf-8")
    (out / "auditoria_mencoes_latest.md").write_text(
        "\n".join(linhas_md) + "\n", encoding="utf-8"
    )
    print(f"MD: {md_path}")

    # JSON machine-readable
    payload = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "financeiro": {"dedup": dict(fin), "bruto": dict(fin_bruto)},
        "tipos_normalizados": n_tipos,
        "so_mencao": [
            {
                "id": r["id"],
                "data": r["data_publicacao"],
                "mencoes": r["menc"],
                **by_date.get(r["data_publicacao"] or "", {}),
            }
            for r in so_mencao
        ],
    }
    (out / "auditoria_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    conn.close()
    print("OK:", out)


if __name__ == "__main__":
    main()
