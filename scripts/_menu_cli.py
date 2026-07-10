# -*- coding: utf-8 -*-
"""Menu interativo do Monitor de Atos (substitui lógica frágil do .bat).

Cada opção tem título + explicação curta (lista) e detalhada (ao executar / ajuda).

Uso:
  python scripts/_menu_cli.py
  iniciar.bat   (wrapper)
"""
from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# PATH extras Windows
_extra = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Poppler\poppler-24.02.0\Library\bin",
    r"C:\poppler\Library\bin",
]
os.environ["PATH"] = os.pathsep.join(_extra + [os.environ.get("PATH", "")])
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("DEV_RELOAD", "0")

# Cores ANSI
C0 = "\033[0m"
C1 = "\033[96m"
C2 = "\033[92m"
C3 = "\033[93m"
C4 = "\033[91m"
C5 = "\033[95m"
CD = "\033[90m"
CB = "\033[1m"


# ---------------------------------------------------------------------------
# Catálogo: cada função com título + explicações
# ---------------------------------------------------------------------------

FUNCOES: dict[str, dict[str, str]] = {
    "1": {
        "grupo": "SERVICOS",
        "titulo": "Iniciar TUDO",
        "curta": "Sobe painel Web + BOT no mesmo terminal",
        "detalhe": (
            "Liga a interface web (http://localhost:8001) e o bot de monitoramento "
            "juntos. Use no dia a dia para deixar o sistema rodando. "
            "Ctrl+C encerra os dois processos."
        ),
    },
    "2": {
        "grupo": "SERVICOS",
        "titulo": "So interface WEB",
        "curta": "Apenas o painel FastAPI em :8001",
        "detalhe": (
            "Abre so a interface web (dashboard, admin, publicacoes). "
            "Nao roda o bot de varredura/OCR. Util para consultar o banco "
            "sem processar edicoes. Ctrl+C encerra."
        ),
    },
    "3": {
        "grupo": "SERVICOS",
        "titulo": "So BOT continuo",
        "curta": "Agendador + fila OCR em loop",
        "detalhe": (
            "Roda so o bot (main.py): varre o site, baixa PDFs, processa a fila "
            "de OCR e detecta atos de Inaja. Roda em ciclos (padrao a cada 6h) "
            "e esvazia a fila entre ciclos. Ctrl+C encerra."
        ),
    },
    "4": {
        "grupo": "SERVICOS",
        "titulo": "Um ciclo BOT e encerra",
        "curta": "Executa --once e volta ao menu",
        "detalhe": (
            "Roda um unico ciclo completo (scrape + download + OCR pendente + "
            "deteccao + notificacao) e encerra. Bom para testar sem deixar o "
            "processo aberto."
        ),
    },
    "5": {
        "grupo": "SERVICOS",
        "titulo": "Abrir navegador",
        "curta": "Abre http://localhost:8001 no browser",
        "detalhe": (
            "Abre o painel no navegador padrao. Se a pagina nao carregar, "
            "inicie antes a web com [1] ou [2]."
        ),
    },
    "6": {
        "grupo": "PROCESSAMENTO",
        "titulo": "Processar um MES",
        "curta": "AAAA-MM via cache OCR + IA",
        "detalhe": (
            "Reprocessa todas as edicoes de um mes (ex.: 2026-07) usando o "
            "cache .ocr.json quando existir, roda deteccao e refinamento por IA. "
            "Nao refaz OCR do zero. Ideal para validar um mes especifico. "
            "Pode limitar quantas edicoes processar."
        ),
    },
    "7": {
        "grupo": "PROCESSAMENTO",
        "titulo": "Processar JULHO/2026",
        "curta": "Atalho do mes de validacao",
        "detalhe": (
            "Mesmo que [6], ja fixado em 2026-07. Processa as 4 edicoes de "
            "julho via cache + IA e mostra relatorio (Inaja, pubs, mencoes)."
        ),
    },
    "8": {
        "grupo": "PROCESSAMENTO",
        "titulo": "Processar N pendentes",
        "curta": "OCR real das edicoes da fila",
        "detalhe": (
            "Pega as N edicoes mais recentes com ocr_processado=0 e roda OCR "
            "(Tesseract/hibrido) + deteccao + IA. Use para esvaziar a fila "
            "aos poucos (ex.: 5 por vez). Mais lento que [6] porque faz OCR."
        ),
    },
    "9": {
        "grupo": "PROCESSAMENTO",
        "titulo": "Reprocessar subdetectados",
        "curta": "Lote com IA em casos fracos",
        "detalhe": (
            "Revisa edicoes que parecem subdetectadas (so mencao, sem publicacao "
            "completa, etc.) e tenta melhorar com IA. Voce define a data inicial "
            "e o limite de itens. Nao e OCR completo."
        ),
    },
    "F": {
        "grupo": "PROCESSAMENTO",
        "titulo": "Ciclo com force-rescan",
        "curta": "Revarre site e reprocessa — cuidado",
        "detalhe": (
            "Um ciclo --once --force-rescan: forca nova varredura do site e "
            "reprocessa edicoes ja conhecidas. Pode demorar e gerar carga. "
            "Pede confirmacao SIM."
        ),
    },
    "O": {
        "grupo": "PROCESSAMENTO",
        "titulo": "Um ciclo com force-OCR",
        "curta": "Tesseract em todas as paginas",
        "detalhe": (
            "Um ciclo --once --force-ocr: ignora texto embutido e passa "
            "Tesseract em todas as paginas. Mais lento e pesado. Use so se o "
            "texto do PDF estiver ruim ou incompleto. Pede SIM."
        ),
    },
    "S": {
        "grupo": "CONSULTA",
        "titulo": "Status da fila",
        "curta": "Pendentes, pubs, jobs, lock, BOT",
        "detalhe": (
            "Painel completo do banco: total de edicoes, processadas, pendentes, "
            "Inaja, publicacoes, mencoes, jobs rodando/erro, arquivo de lock, "
            "se o BOT esta vivo, pendencias por mes e ultimas edicoes."
        ),
    },
    "U": {
        "grupo": "CONSULTA",
        "titulo": "Ultimas publicacoes",
        "curta": "Lista atos detectados (tipo, valor, resumo)",
        "detalhe": (
            "Mostra as publicacoes mais recentes com tipo, numero, orgao, "
            "valor, importancia e resumo da IA. Pode filtrar por mes AAAA-MM "
            "e escolher quantas listar."
        ),
    },
    "P": {
        "grupo": "CONSULTA",
        "titulo": "Buscar publicacao",
        "curta": "Busca por termo em tipo/orgao/resumo",
        "detalhe": (
            "Pesquisa no banco por texto livre (aditivo, prefeitura, 04/2026, "
            "contrato...). Procura em tipo, numero, orgao, assunto, resumo e valor."
        ),
    },
    "M": {
        "grupo": "CONSULTA",
        "titulo": "Mencoes de um mes",
        "curta": "Trechos onde Inaja (ou termos) apareceu",
        "detalhe": (
            "Lista mencoes (trechos OCR) de um mes, mesmo quando nao virou "
            "publicacao completa. Util para auditar falsos negativos e ver "
            "o que o detector capturou na pagina."
        ),
    },
    "Y": {
        "grupo": "CONSULTA",
        "titulo": "Resumo mensal",
        "curta": "Tabela edicoes / OK / pend / pubs / men",
        "detalhe": (
            "Tabela por mes: quantas edicoes, quantas processadas, pendentes, "
            "com Inaja, publicacoes e mencoes. Visao rapida do historico "
            "(ultimos ~2 anos)."
        ),
    },
    "J": {
        "grupo": "CONSULTA",
        "titulo": "Status julho/2026",
        "curta": "Detalhe das edicoes de jul/2026",
        "detalhe": (
            "Relatorio focado em julho/2026: cada edicao com flags OCR/Inaja, "
            "contagem de pubs/mencoes e se o PDF e o .ocr.json existem em disco."
        ),
    },
    "I": {
        "grupo": "CONSULTA",
        "titulo": "Status da IA / chaves",
        "curta": "Modelo, chave, flags e contagem no banco",
        "detalhe": (
            "Mostra se a IA esta disponivel, se ha chave configurada, modelo, "
            "URL, flags de refine/importancia/chat, quantas publicacoes tem "
            "resumo_ia e valor, e se Telegram/SMTP estao configurados."
        ),
    },
    "A": {
        "grupo": "FERRAMENTAS",
        "titulo": "Teste de notificacao",
        "curta": "Telegram / e-mail / arquivo em alertas/",
        "detalhe": (
            "Envia uma notificacao de teste pela cadeia Telegram → e-mail → "
            "arquivo. Se Telegram/SMTP nao estiverem no .env, grava em "
            "alertas/AAAA-MM-DD.log. Serve para validar o canal de alerta."
        ),
    },
    "B": {
        "grupo": "FERRAMENTAS",
        "titulo": "Backup do banco",
        "curta": "Copia SQLite para logs/backups/",
        "detalhe": (
            "Cria uma copia de seguranca do jornal_monitor.db em logs/backups/ "
            "com data/hora no nome. Faca antes de limpar dados ou reprocessar "
            "em massa."
        ),
    },
    "R": {
        "grupo": "FERRAMENTAS",
        "titulo": "Reconstruir pasta atos/",
        "curta": "Gera arquivos de atos a partir do banco",
        "detalhe": (
            "Reconstroi a pasta atos/ (por data, indice) a partir das "
            "publicacoes salvas no banco. Use se a pasta ficou incompleta "
            "ou apos reprocessar deteccao."
        ),
    },
    "E": {
        "grupo": "FERRAMENTAS",
        "titulo": "Exportar CSV do mes",
        "curta": "Gera CSV+JSON em exportacoes/",
        "detalhe": (
            "Exporta as publicacoes de um mes para exportacoes/ em CSV "
            "(separador ;) e JSON, com carimbo de data/hora no nome do arquivo. "
            "Bom para planilha ou auditoria externa."
        ),
    },
    "T": {
        "grupo": "FERRAMENTAS",
        "titulo": "Rodar pytest",
        "curta": "Suite de testes automatizados",
        "detalhe": (
            "Executa pytest tests/ em modo quieto. Valida detector, OCR helpers, "
            "webapp, etc. Nao precisa de site/Telegram. Use apos alterar codigo."
        ),
    },
    "L": {
        "grupo": "FERRAMENTAS",
        "titulo": "Remover lock travado",
        "curta": "Apaga processamento.lock se existir",
        "detalhe": (
            "Remove o arquivo de lock que impede OCR/processamento paralelo. "
            "Use so se um processo morreu e o sistema ficou 'travado' dizendo "
            "que outro processamento esta em andamento."
        ),
    },
    "Q": {
        "grupo": "FERRAMENTAS",
        "titulo": "Limpar jobs travados",
        "curta": "Jobs 'rodando' viram erro",
        "detalhe": (
            "Marca jobs com status=rodando (provavel crash) como erro. "
            "Opcionalmente apaga jobs de erro antigos. Nao apaga publicacoes."
        ),
    },
    "G": {
        "grupo": "FERRAMENTAS",
        "titulo": "Ver final do log",
        "curta": "Ultimas 50 linhas de monitor.log",
        "detalhe": (
            "Mostra as ultimas 50 linhas de logs/monitor.log e lista outros "
            "arquivos .log recentes. Util para depurar falhas de OCR, IA ou "
            "download."
        ),
    },
    "D": {
        "grupo": "FERRAMENTAS",
        "titulo": "Espaco em disco",
        "curta": "Tamanho de edicoes/, atos/, logs/, banco",
        "detalhe": (
            "Calcula quanto disco ocupam edicoes/ (PDFs e .ocr.json), atos/, "
            "logs/, alertas/, exportacoes/ e o arquivo do banco SQLite."
        ),
    },
    "K": {
        "grupo": "FERRAMENTAS",
        "titulo": "Bot Telegram interativo",
        "curta": "Sessao de chat com o bot (nao e o notificador)",
        "detalhe": (
            "Abre o bot Telegram interativo (telegram_bot.py), separado do "
            "notificador de alertas. Requer token configurado. Ctrl+C encerra."
        ),
    },
    "W": {
        "grupo": "FERRAMENTAS",
        "titulo": "Abrir pasta do projeto",
        "curta": "Abre o Explorer nesta pasta",
        "detalhe": (
            "Abre o Windows Explorer na pasta do projeto para ver PDFs, logs, "
            "exportacoes e o codigo."
        ),
    },
    "H": {
        "grupo": "FERRAMENTAS",
        "titulo": "Ajuda completa",
        "curta": "Explica cada funcao do menu",
        "detalhe": (
            "Lista todas as opcoes com explicacao detalhada e o mapa de pastas "
            "do projeto (edicoes, atos, logs, exportacoes, alertas)."
        ),
    },
    "C": {
        "grupo": "PERIGO",
        "titulo": "Limpar dados processados",
        "curta": "Apaga pubs/mencoes e zera OCR — pede SIM",
        "detalhe": (
            "APAGA publicacoes, mencoes, jobs e notificacoes; zera flags OCR "
            "das edicoes. MANTEM cadastro de edicoes, PDFs e caches .ocr.json. "
            "Use para reprocessar do zero a partir do cache. Irreversivel sem "
            "backup — faca [B] antes. Pede a palavra SIM."
        ),
    },
    "0": {
        "grupo": "SAIR",
        "titulo": "Sair",
        "curta": "Fecha o menu",
        "detalhe": "Encerra o menu e volta ao prompt do Windows.",
    },
}

ORDEM_MENU: list[tuple[str, list[str]]] = [
    ("SERVICOS", ["1", "2", "3", "4", "5"]),
    ("PROCESSAMENTO", ["6", "7", "8", "9", "F", "O"]),
    ("CONSULTA", ["S", "U", "P", "M", "Y", "J", "I"]),
    ("FERRAMENTAS", ["A", "B", "R", "E", "T", "L", "Q", "G", "D", "K", "W", "H"]),
    ("PERIGO", ["C"]),
    ("SAIR", ["0"]),
]


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


def run_py(*args: str) -> int:
    cmd = [sys.executable, *args]
    print()
    try:
        r = subprocess.run(cmd, cwd=str(ROOT))
        return int(r.returncode or 0)
    except KeyboardInterrupt:
        print(f"\n  {CD}Interrompido.{C0}")
        return 130
    except Exception as exc:
        print(f"  {C4}Erro: {exc}{C0}")
        return 1


def header_status() -> None:
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "_header_status.py")],
            cwd=str(ROOT),
            check=False,
        )
    except Exception:
        print(f"  {CD}(status indisponivel){C0}")


def explicar(op: str, *, destaque: bool = True) -> None:
    """Imprime título + explicação detalhada da opção."""
    info = FUNCOES.get(op.upper())
    if not info:
        return
    cor = C3 if info["grupo"] == "PERIGO" else C2
    print()
    if destaque:
        print(f"  {cor}{CB}[{op.upper()}] {info['titulo']}{C0}")
    print(f"  {CD}{info['detalhe']}{C0}")
    print()


def show_menu() -> None:
    clear()
    print()
    print(f"  {C1}{CB}============================================================{C0}")
    print(f"  {C1}{CB}        MONITOR DE ATOS - Inaja / O Regional{C0}")
    print(f"  {C1}{CB}============================================================{C0}")
    print()
    header_status()
    print(f"  {CD}Cada tecla: o que faz. [H] = explicacoes completas.{C0}")
    print()

    for grupo, chaves in ORDEM_MENU:
        if grupo == "PERIGO":
            print(f"  {C4}{CB}  {grupo}{C0}")
        elif grupo == "SAIR":
            print(f"  {C5}{CB}  {grupo}{C0}")
        else:
            print(f"  {C5}{CB}  {grupo}{C0}")
        for k in chaves:
            info = FUNCOES[k]
            tecla_cor = C4 if grupo == "PERIGO" else C2
            print(
                f"  {tecla_cor}  [{k}]{C0} {info['titulo']:<28} "
                f"{CD}{info['curta']}{C0}"
            )
        print()

    print(f"  {C1}============================================================{C0}")


def ajuda() -> None:
    clear()
    print(f"\n  {C1}{CB}  AJUDA — explicacao de cada funcao{C0}\n")
    for grupo, chaves in ORDEM_MENU:
        print(f"  {C5}{CB}{grupo}{C0}")
        print(f"  {CD}{'-' * 56}{C0}")
        for k in chaves:
            info = FUNCOES[k]
            print(f"  {C2}[{k}]{C0} {CB}{info['titulo']}{C0}")
            print(f"      {info['detalhe']}")
            print()
    print(f"  {C5}{CB}PASTAS DO PROJETO{C0}")
    print(f"  {CD}{'-' * 56}{C0}")
    print("  edicoes\\      PDFs baixados + caches .ocr.json")
    print("  atos\\         Saidas por publicacao (espelho do banco)")
    print("  logs\\         monitor.log, backups do SQLite")
    print("  exportacoes\\  CSV/JSON gerados pela opcao [E]")
    print("  alertas\\      Fallback se Telegram/e-mail falharem")
    print()
    print(f"  {CD}Dica: ao escolher uma tecla, a explicacao detalhada aparece antes de rodar.{C0}")
    print()
    pause()


def ver_log() -> None:
    log = ROOT / "logs" / "monitor.log"
    print(f"  {C2}Ultimas 50 linhas de logs\\monitor.log{C0}\n")
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-50:]:
            print(line)
    else:
        print("  Log nao encontrado.")
    print(f"\n  {CD}Outros logs em logs\\{C0}")
    logs_dir = ROOT / "logs"
    if logs_dir.is_dir():
        for p in sorted(
            logs_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True
        )[:10]:
            print(f"    {p.name}")
    print()
    pause()


def _act_1() -> bool:
    print(f"  {CD}Ctrl+C encerra Web e BOT.{C0}")
    code = run_py("iniciar_tudo.py")
    if code:
        print(f"\n  {C4}Falha. Verifique o Python no PATH e o .env{C0}")
        pause()
    return True


def _act_2() -> bool:
    print(f"  {CD}Web em http://localhost:8001 — Ctrl+C encerra.{C0}")
    code = run_py("run_interface.py")
    if code:
        pause()
    return True


def _act_3() -> bool:
    print(f"  {CD}Ctrl+C encerra o BOT.{C0}")
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
    print(f"  {C2}Navegador aberto.{C0} Se a web nao subiu, use [1] ou [2].")
    pause()
    return True


def _act_6() -> bool:
    print("  Exemplo de mes: 2026-07   2026-06   2025-12")
    mes = ask("Mes AAAA-MM")
    if not mes:
        print("  Cancelado.")
        pause()
        return True
    lim = ask("Limite de edicoes (Enter=todas)")
    args = ["scripts/_processar_mes.py", mes]
    if lim:
        args += ["--limite", lim]
    run_py(*args)
    pause()
    return True


def _act_7() -> bool:
    run_py("scripts/_processar_mes.py", "2026-07")
    pause()
    return True


def _act_8() -> bool:
    n = ask("Quantas edicoes", "5") or "5"
    run_py("scripts/_processar_pendentes.py", "--limite", n)
    pause()
    return True


def _act_9() -> bool:
    desde = ask("Desde", "2026-01-01") or "2026-01-01"
    limite = ask("Limite", "20") or "20"
    run_py(
        "scripts/reprocessar_subdetectados.py",
        "--desde",
        desde,
        "--limit",
        limite,
    )
    pause()
    return True


def _act_f() -> bool:
    conf = ask("Digite SIM para continuar")
    if conf.upper() != "SIM":
        print("  Cancelado.")
        pause()
        return True
    run_py("main.py", "--once", "--force-rescan")
    pause()
    return True


def _act_o() -> bool:
    conf = ask("Digite SIM para continuar")
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
    n = ask("Quantas publicacoes", "15") or "15"
    mesf = ask("Filtrar mes AAAA-MM (Enter=todos)")
    args = ["scripts/_ultimas_publicacoes.py", "-n", n]
    if mesf:
        args += ["--mes", mesf]
    run_py(*args)
    pause()
    return True


def _act_p() -> bool:
    print("  Exemplos: aditivo  prefeitura  04/2026  contrato")
    termo = ask("Termo")
    if not termo:
        print("  Cancelado.")
        pause()
        return True
    run_py("scripts/_buscar_publicacoes.py", termo)
    pause()
    return True


def _act_m() -> bool:
    mes = ask("Mes", "2026-07") or "2026-07"
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
    mes = ask("Mes", "2026-07") or "2026-07"
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
    apagar = ask("Apagar tambem jobs de erro? [s/N]", "N")
    if apagar.upper() == "S":
        run_py("scripts/_limpar_jobs.py", "--apagar-erros")
    pause()
    return True


def _act_g() -> bool:
    ver_log()
    return True


def _act_d() -> bool:
    run_py("scripts/_espaco_disco.py")
    pause()
    return True


def _act_k() -> bool:
    print(f"  {CD}Ctrl+C encerra.{C0}")
    code = run_py("telegram_bot.py")
    if code:
        pause()
    return True


def _act_w() -> bool:
    if sys.platform == "win32":
        os.startfile(str(ROOT))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(ROOT)], check=False)
    print(f"  {C2}Pasta do projeto aberta.{C0}")
    pause()
    return True


def _act_h() -> bool:
    ajuda()
    return True


def _act_c() -> bool:
    print(f"  {C4}Mantem: edicoes cadastradas, PDFs e caches .ocr.json{C0}")
    print(f"  {C4}Apaga: publicacoes, mencoes, jobs, flags OCR{C0}")
    conf = ask("Digite SIM para confirmar")
    if conf.upper() != "SIM":
        print("  Cancelado.")
        pause()
        return True
    run_py("scripts/_limpar_processados.py")
    pause()
    return True


ACOES: dict[str, Callable[[], bool]] = {
    "1": _act_1,
    "2": _act_2,
    "3": _act_3,
    "4": _act_4,
    "5": _act_5,
    "6": _act_6,
    "7": _act_7,
    "8": _act_8,
    "9": _act_9,
    "F": _act_f,
    "O": _act_o,
    "S": _act_s,
    "U": _act_u,
    "P": _act_p,
    "M": _act_m,
    "Y": _act_y,
    "J": _act_j,
    "I": _act_i,
    "A": _act_a,
    "B": _act_b,
    "R": _act_r,
    "E": _act_e,
    "T": _act_t,
    "L": _act_l,
    "Q": _act_q,
    "G": _act_g,
    "D": _act_d,
    "K": _act_k,
    "W": _act_w,
    "H": _act_h,
    "C": _act_c,
}


def dispatch(op: str) -> bool:
    """Retorna False para sair do menu."""
    op = (op or "").strip().upper()
    if op == "0":
        explicar("0")
        print(f"  {C2}Ate logo.{C0}")
        return False

    acao = ACOES.get(op)
    if not acao:
        print(f"\n  {C4}Opcao invalida. Digite H para ver todas as funcoes explicadas.{C0}")
        pause()
        return True

    clear()
    explicar(op)
    return acao()


def main() -> int:
    _enable_ansi()
    if sys.platform == "win32":
        try:
            os.system("title Monitor de Atos - Menu")
        except Exception:
            pass

    while True:
        show_menu()
        try:
            op = input(f"  {CB}Escolha:{C0} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C2}Ate logo.{C0}")
            return 0
        if not op:
            continue
        if not dispatch(op):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
