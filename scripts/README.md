# Scripts

Utilitários de manutenção. O fluxo normal continua em `main.py` / `webapp.py` / `iniciar.bat`.

## Menu interativo

```bash
python scripts/_menu_cli.py
# ou
iniciar.bat
```

## Oficiais (manutenção)

| Script | Uso |
|--------|-----|
| `_menu_cli.py` | Menu completo (compacto + busca + wizard HOJE) |
| `_diagnostico.py` | Healthcheck PATH / IA / Telegram / lock |
| `_status_fila.py` | Pendentes, pubs, automação |
| `_header_status.py` | Uma linha de status (fallback) |
| `_processar_pendentes.py` | OCR real da fila |
| `_processar_mes.py` | Reprocessar mês (cache ou OCR) |
| `_processar_id.py` | Uma edição por ID |
| `_qualidade.py` | FN, só-menção, quarentena, anomalias |
| `_re_ia.py` | Re-rodar IA em pubs fracas |
| `_agente.py` | Controle do agente vigilante |
| `_scrape_only.py` | Só cadastrar edições |
| `_invalidar_ocr.py` | Apagar cache `.ocr.json` |
| `_limpar_jobs.py` / `_remover_lock.py` | Destravar |
| `_limpar_processados.py` | Reset de pubs (perigoso, dry-run) |
| `_limpeza_disco.py` | Dry-run / limpeza de espaço |
| `_settings_cli.py` | Ver/toggle settings |
| `_ultimas_publicacoes.py` / `_buscar_publicacoes.py` | Consulta |
| `_listar_mencoes.py` / `_resumo_mensal.py` | Menções e tabela mensal |
| `_exportar_mes.py` / `_ia_status.py` / `_espaco_disco.py` | Utilitários |
| `backup_db.py` | Backup SQLite |
| `gerar_relatorios_auditoria.py` | CSV + MD de auditoria |
| `reconstruir_atos.py` | Regenera pasta `atos/` |
| `reprocessar_cache_ocr.py` | Redetectar sem re-OCR |
| `reprocessar_subdetectados.py` | Lote em casos fracos |

```bash
python scripts/backup_db.py
python scripts/gerar_relatorios_auditoria.py
python scripts/reprocessar_cache_ocr.py --falsos-negativos
python scripts/reprocessar_cache_ocr.py --ids 35508,35517
```

Revisão humana na UI: **Operação → Só menção** (`/revisao/so-mencao`).

## Ciclo completo

```bash
python main.py --once
python main.py --once --force-ocr
python main.py --process-all
```

## Observações

- Com `OPENCODE_API_KEY` inválida (401), o reprocessamento mantém publicações heurísticas e tenta **preservar** `resumo_ia` já gravado.
- Configure `TELEGRAM_CHAT_ID` no `.env` ou no Admin (token sem chat → alertas em `./alertas/`).
