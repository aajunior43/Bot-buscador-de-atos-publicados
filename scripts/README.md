# Scripts

Utilitários de análise e reprocessamento. O fluxo normal continua em `main.py` / `webapp.py`.

## Oficiais (manutenção)

```bash
# Backup do SQLite
python scripts/backup_db.py

# Relatório CSV + auditoria das edições só-menção
python scripts/gerar_relatorios_auditoria.py
# Saída: ./relatorios/publicacoes_auditoria_latest.csv
#         ./relatorios/auditoria_mencoes_latest.md

# Redetectar a partir do cache OCR (sem re-OCR completo)
python scripts/reprocessar_cache_ocr.py --falsos-negativos
python scripts/reprocessar_cache_ocr.py --ids 35508,35517
python scripts/reprocessar_cache_ocr.py --todas-com-cache --limit 20
```

Revisão humana na UI: **Operação → Só menção** (`/revisao/so-mencao`).

## Ciclo completo

```bash
python main.py --once --force-ocr
python main.py --process-all
```

## Observações

- Com `OPENCODE_API_KEY` inválida (401), o reprocessamento mantém publicações heurísticas e tenta **preservar** `resumo_ia` já gravado.
- Configure `TELEGRAM_CHAT_ID` no `.env` (hoje o token pode existir sem chat — alertas vão para `./alertas/`).
