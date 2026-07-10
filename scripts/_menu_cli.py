# -*- coding: utf-8 -*-
"""Menu interativo completo do Monitor de Atos.

Uso:
  python scripts/_menu_cli.py
  python scripts/_menu_cli.py --run S
  python scripts/_menu_cli.py --run 6 --mes 2026-07
  python scripts/_menu_cli.py --run 8 --limite 5
  python scripts/_menu_cli.py --compact
  iniciar.bat
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from datetime import date
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

_extra = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Poppler\poppler-24.02.0\Library\bin",
    r"C:\poppler\Library\bin",
]
os.environ["PATH"] = os.pathsep.join(_extra + [os.environ.get("PATH", "")])
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("DEV_RELOAD", "0")

C0, C1, C2, C3, C4, C5 = "\033[0m", "\033[96m", "\033[92m", "\033[93m", "\033[91m", "\033[95m"
CD, CB = "\033[90m", "\033[1m"

# Histórico da sessão
_HIST: list[str] = []
_COMPACT = False
_NO_CLS = False


FUNCOES: dict[str, dict[str, str]] = {
    "1": {"grupo": "SERVICOS", "titulo": "Iniciar TUDO",
          "curta": "Web + BOT juntos",
          "detalhe": "Sobe painel web (:8001) e bot de monitoramento. Ctrl+C encerra tudo."},
    "2": {"grupo": "SERVICOS", "titulo": "So interface WEB",
          "curta": "Painel FastAPI :8001",
          "detalhe": "Só dashboard/admin. Não roda OCR. Ctrl+C encerra."},
    "3": {"grupo": "SERVICOS", "titulo": "So BOT continuo",
          "curta": "Agendador + fila OCR",
          "detalhe": "main.py em loop: scrape, OCR, detecção, notificações."},
    "4": {"grupo": "SERVICOS", "titulo": "Um ciclo BOT",
          "curta": "--once e volta",
          "detalhe": "Um ciclo completo e encerra. Bom para teste."},
    "5": {"grupo": "SERVICOS", "titulo": "Abrir navegador",
          "curta": "http://localhost:8001",
          "detalhe": "Abre o painel. Suba [1] ou [2] se a página não carregar."},
    "6": {"grupo": "PROCESSAMENTO", "titulo": "Processar um MES",
          "curta": "AAAA-MM cache+IA",
          "detalhe": "Reprocessa mês via .ocr.json + detecção + IA. Opção OCR real disponível."},
    "7": {"grupo": "PROCESSAMENTO", "titulo": "Mes atual / ultimos dias",
          "curta": "Atalhos dinâmicos",
          "detalhe": "Processa mês civil atual, últimos 7 ou 30 dias (cache ou OCR real)."},
    "8": {"grupo": "PROCESSAMENTO", "titulo": "Processar N pendentes",
          "curta": "OCR real da fila",
          "detalhe": "OCR das N edições mais recentes pendentes. Mostra estimativa de tempo."},
    "9": {"grupo": "PROCESSAMENTO", "titulo": "Reprocessar subdetectados",
          "curta": "Lote IA em casos fracos",
          "detalhe": "Edições com muitos hits Inajá e poucas pubs — reprocessa do cache."},
    "F": {"grupo": "PROCESSAMENTO", "titulo": "Force-rescan",
          "curta": "Cuidado · pede SIM",
          "detalhe": "Revarre site e reprocessa conhecidas. Backup automático antes."},
    "O": {"grupo": "PROCESSAMENTO", "titulo": "Force-OCR ciclo",
          "curta": "Tesseract em tudo",
          "detalhe": "Um ciclo com OCR forçado em todas as páginas. Lento."},
    "X": {"grupo": "PROCESSAMENTO", "titulo": "Processar por ID",
          "curta": "Uma edição específica",
          "detalhe": "OCR real ou só cache de uma edição (id do banco)."},
    "V": {"grupo": "PROCESSAMENTO", "titulo": "Invalidar cache OCR",
          "curta": "Apaga .ocr.json mês/id",
          "detalhe": "Remove cache OCR para forçar re-OCR depois. Dry-run disponível."},
    "N": {"grupo": "PROCESSAMENTO", "titulo": "Scrape so",
          "curta": "Cadastra edições novas",
          "detalhe": "Varre o site e cadastra/baixa PDFs sem rodar OCR."},
    "S": {"grupo": "CONSULTA", "titulo": "Status da fila",
          "curta": "Pendentes, pubs, BOT",
          "detalhe": "Painel completo do banco, automação e últimas edições."},
    "U": {"grupo": "CONSULTA", "titulo": "Ultimas publicacoes",
          "curta": "Lista atos detectados",
          "detalhe": "Pubs recentes com tipo, valor, resumo. Filtro por mês."},
    "P": {"grupo": "CONSULTA", "titulo": "Buscar publicacao",
          "curta": "Busca por termo",
          "detalhe": "Pesquisa em tipo, órgão, número, resumo e valor."},
    "M": {"grupo": "CONSULTA", "titulo": "Mencoes de um mes",
          "curta": "Trechos OCR Inajá",
          "detalhe": "Menções do mês mesmo sem publicação completa."},
    "Y": {"grupo": "CONSULTA", "titulo": "Resumo mensal",
          "curta": "Tabela por mês",
          "detalhe": "Edições, OK, pendentes, Inajá, pubs e menções por mês."},
    "J": {"grupo": "CONSULTA", "titulo": "Status julho/2026",
          "curta": "Detalhe jul/2026",
          "detalhe": "Relatório focado nas edições de julho/2026."},
    "I": {"grupo": "CONSULTA", "titulo": "Status da IA",
          "curta": "Chave, modelo, contagens",
          "detalhe": "Disponibilidade da IA e qualidade dos campos no banco."},
    "Z": {"grupo": "QUALIDADE", "titulo": "Diagnostico",
          "curta": "Tesseract, IA, lock, disco",
          "detalhe": "Healthcheck: PATH, Poppler, chave IA, Telegram, banco, lock, site."},
    "Q1": {"grupo": "QUALIDADE", "titulo": "Painel qualidade",
          "curta": "FN, só-menção, quarentena",
          "detalhe": "Falsos negativos, só-menção, quarentena, auditoria e relatório de pubs."},
    "Q2": {"grupo": "QUALIDADE", "titulo": "Re-rodar IA fraca",
          "curta": "Pubs sem resumo/valor",
          "detalhe": "Chama a IA só em publicações com campos fracos (barato vs OCR)."},
    "A": {"grupo": "FERRAMENTAS", "titulo": "Teste notificacao",
          "curta": "Telegram/e-mail/arquivo",
          "detalhe": "Envia alerta de teste pela cadeia configurada."},
    "B": {"grupo": "FERRAMENTAS", "titulo": "Backup do banco",
          "curta": "Cópia em logs/backups",
          "detalhe": "Backup SQLite com data/hora."},
    "R": {"grupo": "FERRAMENTAS", "titulo": "Reconstruir atos/",
          "curta": "Espelho do banco",
          "detalhe": "Regera pasta atos/ a partir das publicações."},
    "E": {"grupo": "FERRAMENTAS", "titulo": "Exportar CSV mes",
          "curta": "CSV+JSON exportacoes/",
          "detalhe": "Exporta publicações do mês para planilha/JSON."},
    "T": {"grupo": "FERRAMENTAS", "titulo": "pytest",
          "curta": "Testes automatizados",
          "detalhe": "Roda a suite pytest do projeto."},
    "L": {"grupo": "FERRAMENTAS", "titulo": "Remover lock",
          "curta": "processamento.lock",
          "detalhe": "Remove lock se processo morreu travado."},
    "Q": {"grupo": "FERRAMENTAS", "titulo": "Limpar jobs travados",
          "curta": "rodando → erro",
          "detalhe": "Marca jobs rodando como erro; opcional apagar erros."},
    "G": {"grupo": "FERRAMENTAS", "titulo": "Ver log",
          "curta": "monitor.log tail",
          "detalhe": "Últimas 50 linhas do log principal."},
    "D": {"grupo": "FERRAMENTAS", "titulo": "Espaco em disco",
          "curta": "edicoes/ atos/ logs/",
          "detalhe": "Tamanho das pastas e do banco."},
    "K": {"grupo": "FERRAMENTAS", "titulo": "Telegram interativo",
          "curta": "Bot de chat",
          "detalhe": "Sessão interativa do telegram_bot.py (não é o notificador)."},
    "W": {"grupo": "FERRAMENTAS", "titulo": "Abrir pasta",
          "curta": "Explorer no projeto",
          "detalhe": "Abre a pasta do projeto no Explorer."},
    "CFG": {"grupo": "FERRAMENTAS", "titulo": "Settings / toggle IA",
          "curta": "Ver e alterar flags",
          "detalhe": "Mostra settings do .env e do banco; pode ligar/desligar refine IA."},
    "H": {"grupo": "FERRAMENTAS", "titulo": "Ajuda completa",
          "curta": "Explica cada função",
          "detalhe": "Lista todas as opções com texto completo. Use ?TECLA para uma só."},
    "HS": {"grupo": "FERRAMENTAS", "titulo": "Historico sessao",
          "curta": "Últimas ações",
          "detalhe": "Mostra o histórico de comandos desta sessão do menu."},
    "C": {"grupo": "PERIGO", "titulo": "Limpar processados",
          "curta": "Apaga pubs · pede SIM",
          "detalhe": "Apaga pubs/menções/jobs e zera OCR. Mantém PDFs e .ocr.json. Backup + dry-run."},
    "0": {"grupo": "SAIR", "titulo": "Sair",
          "curta": "Fecha o menu",
          "detalhe": "Encerra o menu."},
}

# Teclas curtas no menu (Q1/Q2/CFG/HS mapeadas)
ORDEM: list[tuple[str, list[str]]] = [
    ("SERVICOS", ["1", "2", "3", "4", "5"]),
    ("PROCESSAMENTO", ["6", "7", "8", "9", "X", "V", "N", "F", "O"]),
    ("CONSULTA", ["S", "U", "P", "M", "Y", "J", "I"]),
    ("QUALIDADE", ["Z", "Q1", "Q2"]),
    ("FERRAMENTAS", ["A", "B", "R", "E", "T", "L", "Q", "G", "D", "K", "W", "CFG", "HS", "H"]),
    ("PERIGO", ["C"]),
    ("SAIR", ["0"]),
]

# Alias digitáveis
ALIASES = {
    "Q1": "Q1", "QUALIDADE": "Q1", "FN": "Q1",
    "Q2": "Q2", "REIA": "Q2", "RE-IA": "Q2",
    "CFG": "CFG", "CONFIG": "CFG", "SETTINGS": "CFG",
    "HS": "HS", "HIST": "HS", "HISTORICO": "HS",
    "DIAG": "Z", "DIAGNOSTICO": "Z",
}


def _enable_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


def clear() -> None:
    if _NO_CLS:
        print("\n" + "-" * 60 + "\n")
        return
    os.system("cls" if sys.platform == "win32" else "clear")


def pause(msg: str = "Pressione Enter...") -> None:
    try:
        input(f"  {msg}")
    except EOFError:
        pass


def ask(prompt: str, default: str | None = None) -> str:
    suf = f" [{default}]" if default is not None else ""
    try:
        val = input(f"  {prompt}{suf}: ").strip()
    except EOFError:
        return default if default is not None else ""
    if not val and default is not None:
        return default
    return val


def log_hist(msg: str) -> None:
    _HIST.append(msg)
    if len(_HIST) > 40:
        del _HIST[:-40]


def run_py(*args: str) -> int:
    print()
    try:
        r = subprocess.run([sys.executable, *args], cwd=str(ROOT))
        code = int(r.returncode or 0)
        log_hist(f"{' '.join(args)} → exit {code}")
        return code
    except KeyboardInterrupt:
        print(f"\n  {CD}Interrompido.{C0}")
        log_hist(f"{' '.join(args)} → ^C")
        return 130
    except Exception as exc:
        print(f"  {C4}Erro: {exc}{C0}")
        return 1


def header_status() -> None:
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "_header_status.py")],
            cwd=str(ROOT), check=False,
        )
    except Exception:
        print(f"  {CD}(status indisponivel){C0}")


def explicar(op: str) -> None:
    info = FUNCOES.get(op.upper()) or FUNCOES.get(op)
    if not info:
        return
    cor = C4 if info["grupo"] == "PERIGO" else C2
    print(f"\n  {cor}{CB}[{op}] {info['titulo']}{C0}")
    print(f"  {CD}{info['detalhe']}{C0}\n")


def backup_auto(motivo: str) -> None:
    print(f"  {CD}Backup automático antes de: {motivo}…{C0}")
    run_py("scripts/backup_db.py")


def show_menu() -> None:
    clear()
    print()
    print(f"  {C1}{CB}============================================================{C0}")
    print(f"  {C1}{CB}        MONITOR DE ATOS - Inaja / O Regional{C0}")
    print(f"  {C1}{CB}============================================================{C0}")
    print()
    header_status()
    print(f"  {CD}[H] ajuda  ·  ?TECLA explica uma opção  ·  favoritos: 1 8 S U 6 Z{C0}")
    print()

    grupos = ORDEM
    if _COMPACT:
        # favoritos + perigo + sair
        print(f"  {C5}{CB}  FAVORITOS (modo compacto){C0}")
        for k in ["1", "8", "S", "U", "6", "Z", "Q1", "B", "H", "C", "0"]:
            info = FUNCOES[k]
            print(f"  {C2}  [{k}]{C0} {info['titulo']:<26} {CD}{info['curta']}{C0}")
        print(f"\n  {CD}Digite qualquer tecla do menu completo ou H para lista total.{C0}")
    else:
        for grupo, chaves in grupos:
            cor_g = C4 if grupo == "PERIGO" else C5
            print(f"  {cor_g}{CB}  {grupo}{C0}")
            for k in chaves:
                info = FUNCOES[k]
                tc = C4 if grupo == "PERIGO" else C2
                key_show = k if len(k) <= 2 else k
                print(
                    f"  {tc}  [{key_show}]{C0} {info['titulo']:<26} "
                    f"{CD}{info['curta']}{C0}"
                )
            print()
    print(f"  {C1}============================================================{C0}")


def ajuda(filtro: str | None = None) -> None:
    clear()
    print(f"\n  {C1}{CB}  AJUDA{C0}\n")
    for grupo, chaves in ORDEM:
        print(f"  {C5}{CB}{grupo}{C0}")
        for k in chaves:
            if filtro and k.upper() != filtro.upper() and filtro.upper() not in FUNCOES[k]["titulo"].upper():
                continue
            info = FUNCOES[k]
            print(f"  {C2}[{k}]{C0} {CB}{info['titulo']}{C0}")
            print(f"      {info['detalhe']}")
        print()
    print(f"  {C5}{CB}PASTAS{C0}")
    print("  edicoes\\  PDFs + .ocr.json   |  atos\\  saidas")
    print("  logs\\     monitor + backups |  exportacoes\\  CSV")
    print("  alertas\\  fallback notificação")
    print()
    pause()


# --- actions ---

def _act_1() -> bool:
    print(f"  {CD}Ctrl+C encerra.{C0}")
    code = run_py("iniciar_tudo.py")
    if code:
        pause()
    return True


def _act_2() -> bool:
    code = run_py("run_interface.py")
    if code:
        pause()
    return True


def _act_3() -> bool:
    code = run_py("main.py")
    if code:
        pause()
    return True


def _act_4() -> bool:
    run_py("main.py", "--once")
    pause()
    return True


def _act_5() -> bool:
    webbrowser.open("http://localhost:8001")
    print(f"  {C2}Navegador aberto.{C0}")
    pause()
    return True


def _act_6() -> bool:
    mes = ask("Mes AAAA-MM", date.today().strftime("%Y-%m"))
    if not mes:
        print("  Cancelado.")
        pause()
        return True
    lim = ask("Limite edicoes (Enter=todas)")
    ocr = ask("OCR real? [s/N]", "N")
    args = ["scripts/_processar_mes.py", mes]
    if lim:
        args += ["--limite", lim]
    if ocr.upper() == "S":
        args.append("--ocr-real")
        print(f"  {C3}OCR real pode demorar bastante.{C0}")
    run_py(*args)
    pause()
    return True


def _act_7() -> bool:
    print("  [1] Mes civil atual (cache)")
    print("  [2] Ultimos 7 dias (cache)")
    print("  [3] Ultimos 30 dias (cache)")
    print("  [4] Mes atual com OCR real")
    print("  [5] Julho/2026 (atalho legado)")
    op = ask("Opcao", "1")
    if op == "1":
        run_py("scripts/_processar_mes.py", "--mes-atual")
    elif op == "2":
        run_py("scripts/_processar_mes.py", "--dias", "7")
    elif op == "3":
        run_py("scripts/_processar_mes.py", "--dias", "30")
    elif op == "4":
        run_py("scripts/_processar_mes.py", "--mes-atual", "--ocr-real")
    elif op == "5":
        run_py("scripts/_processar_mes.py", "2026-07")
    else:
        print("  Cancelado.")
    pause()
    return True


def _act_8() -> bool:
    n = ask("Quantas edicoes", "5") or "5"
    run_py("scripts/_processar_pendentes.py", "--limite", n, "--estimar")
    conf = ask("Continuar? [S/n]", "S")
    if conf.upper() == "N":
        print("  Cancelado.")
        pause()
        return True
    run_py("scripts/_processar_pendentes.py", "--limite", n)
    pause()
    return True


def _act_9() -> bool:
    desde = ask("Desde", "2026-01-01") or "2026-01-01"
    limite = ask("Limite", "20") or "20"
    run_py("scripts/reprocessar_subdetectados.py", "--desde", desde, "--limit", limite)
    pause()
    return True


def _act_x() -> bool:
    eid = ask("ID da edicao")
    if not eid.isdigit():
        print("  ID invalido.")
        pause()
        return True
    modo = ask("Modo: [1] OCR real  [2] so cache", "1")
    args = ["scripts/_processar_id.py", eid]
    if modo == "2":
        args.append("--cache")
    run_py(*args)
    pause()
    return True


def _act_v() -> bool:
    print("  [1] Por mes AAAA-MM")
    print("  [2] Por ID")
    op = ask("Opcao", "1")
    dry = ask("Dry-run (so listar)? [S/n]", "S")
    zerar = ask("Zerar flag ocr_processado? [s/N]", "N")
    args = ["scripts/_invalidar_ocr.py"]
    if op == "2":
        eid = ask("ID")
        if not eid.isdigit():
            print("  Cancelado.")
            pause()
            return True
        args += ["--id", eid]
    else:
        mes = ask("Mes", date.today().strftime("%Y-%m"))
        args += ["--mes", mes]
    if dry.upper() != "N":
        args.append("--dry-run")
    if zerar.upper() == "S":
        args.append("--zerar-flag")
    if dry.upper() == "N":
        backup_auto("invalidar OCR")
    run_py(*args)
    pause()
    return True


def _act_n() -> bool:
    baixar = ask("Baixar PDFs tambem? [s/N]", "N")
    args = ["scripts/_scrape_only.py"]
    if baixar.upper() == "S":
        args.append("--baixar")
    run_py(*args)
    pause()
    return True


def _act_f() -> bool:
    conf = ask("Digite SIM para force-rescan")
    if conf.upper() != "SIM":
        print("  Cancelado.")
        pause()
        return True
    backup_auto("force-rescan")
    run_py("main.py", "--once", "--force-rescan")
    pause()
    return True


def _act_o() -> bool:
    conf = ask("Digite SIM para force-OCR")
    if conf.upper() != "SIM":
        print("  Cancelado.")
        pause()
        return True
    run_py("main.py", "--once", "--force-ocr")
    pause()
    return True


def _act_s() -> bool:
    run_py("scripts/_status_fila.py")
    pause()
    return True


def _act_u() -> bool:
    n = ask("Quantas", "15") or "15"
    mes = ask("Mes AAAA-MM (Enter=todos)")
    args = ["scripts/_ultimas_publicacoes.py", "-n", n]
    if mes:
        args += ["--mes", mes]
    run_py(*args)
    pause()
    return True


def _act_p() -> bool:
    termo = ask("Termo")
    if not termo:
        print("  Cancelado.")
        pause()
        return True
    run_py("scripts/_buscar_publicacoes.py", termo)
    pause()
    return True


def _act_m() -> bool:
    mes = ask("Mes", date.today().strftime("%Y-%m"))
    run_py("scripts/_listar_mencoes.py", mes)
    pause()
    return True


def _act_y() -> bool:
    run_py("scripts/_resumo_mensal.py")
    pause()
    return True


def _act_j() -> bool:
    run_py("scripts/_status_julho.py")
    pause()
    return True


def _act_i() -> bool:
    run_py("scripts/_ia_status.py")
    pause()
    return True


def _act_z() -> bool:
    run_py("scripts/_diagnostico.py")
    pause()
    return True


def _act_q1() -> bool:
    print("  [1] Tudo  [2] FN  [3] So-mencao  [4] Quarentena")
    print("  [5] Auditoria  [6] Relatorio pubs  [7] Anomalias")
    op = ask("Opcao", "1")
    mapa = {
        "1": "tudo", "2": "fn", "3": "so-mencao", "4": "quarentena",
        "5": "auditoria", "6": "relatorio", "7": "anomalias",
    }
    modo = mapa.get(op, "tudo")
    args = ["scripts/_qualidade.py", "--modo", modo]
    if modo == "relatorio":
        mes = ask("Mes (Enter=todos)")
        if mes:
            args += ["--mes", mes]
    run_py(*args)
    pause()
    return True


def _act_q2() -> bool:
    mes = ask("Mes (Enter=todos)")
    lim = ask("Limite", "20") or "20"
    args = ["scripts/_re_ia.py", "--limite", lim]
    if mes:
        args += ["--mes", mes]
    run_py(*args)
    pause()
    return True


def _act_a() -> bool:
    run_py("main.py", "--notify-test")
    pause()
    return True


def _act_b() -> bool:
    run_py("scripts/backup_db.py")
    pause()
    return True


def _act_r() -> bool:
    run_py("scripts/reconstruir_atos.py")
    pause()
    return True


def _act_e() -> bool:
    mes = ask("Mes", date.today().strftime("%Y-%m"))
    run_py("scripts/_exportar_mes.py", mes, "--json")
    pause()
    return True


def _act_t() -> bool:
    run_py("-m", "pytest", "tests/", "-q", "--tb=line")
    pause()
    return True


def _act_l() -> bool:
    run_py("scripts/_remover_lock.py")
    pause()
    return True


def _act_q() -> bool:
    run_py("scripts/_limpar_jobs.py")
    if ask("Apagar jobs de erro? [s/N]", "N").upper() == "S":
        run_py("scripts/_limpar_jobs.py", "--apagar-erros")
    pause()
    return True


def _act_g() -> bool:
    log = ROOT / "logs" / "monitor.log"
    print(f"  {C2}Ultimas 50 linhas{C0}\n")
    if log.exists():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]:
            print(line)
    else:
        print("  Log nao encontrado.")
    print()
    pause()
    return True


def _act_d() -> bool:
    run_py("scripts/_espaco_disco.py")
    pause()
    return True


def _act_k() -> bool:
    code = run_py("telegram_bot.py")
    if code:
        pause()
    return True


def _act_w() -> bool:
    if sys.platform == "win32":
        os.startfile(str(ROOT))  # type: ignore[attr-defined]
    print(f"  {C2}Pasta aberta.{C0}")
    pause()
    return True


def _act_cfg() -> bool:
    run_py("scripts/_settings_cli.py")
    if ask("Toggle AI refine? [s/N]", "N").upper() == "S":
        run_py("scripts/_settings_cli.py", "--toggle-ia")
    pause()
    return True


def _act_hs() -> bool:
    print(f"\n  {C2}Historico da sessao ({len(_HIST)}){C0}\n")
    if not _HIST:
        print("  (vazio)")
    for i, h in enumerate(_HIST[-20:], 1):
        print(f"  {i:2}. {h}")
    print()
    pause()
    return True


def _act_h() -> bool:
    ajuda()
    return True


def _act_c() -> bool:
    print(f"  {C4}Dry-run primeiro recomendado.{C0}")
    if ask("Ver dry-run? [S/n]", "S").upper() != "N":
        run_py("scripts/_limpar_processados.py", "--dry-run")
    conf = ask("Digite SIM para APAGAR de verdade")
    if conf.upper() != "SIM":
        print("  Cancelado.")
        pause()
        return True
    backup_auto("limpar processados")
    run_py("scripts/_limpar_processados.py")
    pause()
    return True


ACOES: dict[str, Callable[[], bool]] = {
    "1": _act_1, "2": _act_2, "3": _act_3, "4": _act_4, "5": _act_5,
    "6": _act_6, "7": _act_7, "8": _act_8, "9": _act_9,
    "X": _act_x, "V": _act_v, "N": _act_n, "F": _act_f, "O": _act_o,
    "S": _act_s, "U": _act_u, "P": _act_p, "M": _act_m, "Y": _act_y,
    "J": _act_j, "I": _act_i,
    "Z": _act_z, "Q1": _act_q1, "Q2": _act_q2,
    "A": _act_a, "B": _act_b, "R": _act_r, "E": _act_e, "T": _act_t,
    "L": _act_l, "Q": _act_q, "G": _act_g, "D": _act_d, "K": _act_k,
    "W": _act_w, "CFG": _act_cfg, "HS": _act_hs, "H": _act_h, "C": _act_c,
}


def normalize_op(raw: str) -> str:
    op = (raw or "").strip().upper()
    if op in ALIASES:
        return ALIASES[op]
    return op


def dispatch(op: str) -> bool:
    op = normalize_op(op)
    if op.startswith("?") and len(op) > 1:
        key = normalize_op(op[1:])
        clear()
        if key in FUNCOES:
            explicar(key)
        else:
            print(f"  Tecla desconhecida: {key}")
        pause()
        return True

    if op == "0":
        explicar("0")
        print(f"  {C2}Ate logo.{C0}")
        return False

    acao = ACOES.get(op)
    if not acao:
        print(f"\n  {C4}Opcao invalida. H = ajuda · ?S = explicar Status.{C0}")
        pause()
        return True

    clear()
    explicar(op)
    return acao()


def run_noninteractive(op: str, extras: argparse.Namespace) -> int:
    """Executa uma ação e sai (para scripts/agendamento)."""
    op = normalize_op(op)
    if op == "S":
        return run_py("scripts/_status_fila.py")
    if op == "Z":
        return run_py("scripts/_diagnostico.py")
    if op == "I":
        return run_py("scripts/_ia_status.py")
    if op == "Y":
        return run_py("scripts/_resumo_mensal.py")
    if op == "D":
        return run_py("scripts/_espaco_disco.py")
    if op == "B":
        return run_py("scripts/backup_db.py")
    if op == "A":
        return run_py("main.py", "--notify-test")
    if op == "4":
        return run_py("main.py", "--once")
    if op == "L":
        return run_py("scripts/_remover_lock.py")
    if op == "Q":
        return run_py("scripts/_limpar_jobs.py")
    if op == "Q1":
        return run_py("scripts/_qualidade.py", "--modo", "tudo")
    if op == "6":
        mes = extras.mes or date.today().strftime("%Y-%m")
        args = ["scripts/_processar_mes.py", mes]
        if extras.limite:
            args += ["--limite", str(extras.limite)]
        if extras.ocr_real:
            args.append("--ocr-real")
        return run_py(*args)
    if op == "7":
        return run_py("scripts/_processar_mes.py", "--mes-atual")
    if op == "8":
        lim = str(extras.limite or 5)
        return run_py("scripts/_processar_pendentes.py", "--limite", lim)
    if op == "U":
        args = ["scripts/_ultimas_publicacoes.py", "-n", str(extras.limite or 15)]
        if extras.mes:
            args += ["--mes", extras.mes]
        return run_py(*args)
    if op == "E":
        mes = extras.mes or date.today().strftime("%Y-%m")
        return run_py("scripts/_exportar_mes.py", mes, "--json")
    if op == "X" and extras.edicao_id:
        return run_py("scripts/_processar_id.py", str(extras.edicao_id))
    if op == "Q2":
        args = ["scripts/_re_ia.py", "--limite", str(extras.limite or 20)]
        if extras.mes:
            args += ["--mes", extras.mes]
        return run_py(*args)
    if op == "N":
        return run_py("scripts/_scrape_only.py")
    print(f"Opcao --run nao suportada de forma nao-interativa: {op}")
    print("Suportadas: S Z I Y D B A 4 L Q Q1 Q2 6 7 8 U E X N")
    return 2


def main(argv: list[str] | None = None) -> int:
    global _COMPACT, _NO_CLS
    ap = argparse.ArgumentParser(description="Menu Monitor de Atos")
    ap.add_argument("--run", help="Executa tecla e sai (nao interativo)")
    ap.add_argument("--mes", default="")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--edicao-id", type=int, default=0)
    ap.add_argument("--ocr-real", action="store_true")
    ap.add_argument("--compact", action="store_true")
    ap.add_argument("--no-cls", action="store_true")
    args = ap.parse_args(argv)

    _enable_ansi()
    _COMPACT = bool(args.compact)
    _NO_CLS = bool(args.no_cls)

    if args.run:
        return run_noninteractive(args.run, args)

    if sys.platform == "win32":
        try:
            os.system("title Monitor de Atos - Menu")
        except Exception:
            pass

    while True:
        show_menu()
        try:
            raw = input(f"  {CB}Escolha:{C0} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C2}Ate logo.{C0}")
            return 0
        if not raw:
            continue
        if raw.lower() in {"compact", "modo compacto"}:
            _COMPACT = not _COMPACT
            continue
        if not dispatch(raw):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
