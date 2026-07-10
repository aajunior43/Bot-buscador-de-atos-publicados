# -*- coding: utf-8 -*-
"""Menu interativo do Monitor de Atos (substitui lógica frágil do .bat).

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


def run_py(*args: str, check: bool = False) -> int:
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


def show_menu() -> None:
    clear()
    print()
    print(f"  {C1}{CB}============================================================{C0}")
    print(f"  {C1}{CB}        MONITOR DE ATOS - Inaja / O Regional{C0}")
    print(f"  {C1}{CB}============================================================{C0}")
    print()
    header_status()
    print()
    print(f"  {C5}{CB}  SERVICOS{C0}")
    print(f"  {C2}  [1]{C0} Iniciar TUDO            {CD}Web + BOT  http://localhost:8001{C0}")
    print(f"  {C2}  [2]{C0} So interface WEB")
    print(f"  {C2}  [3]{C0} So BOT continuo")
    print(f"  {C2}  [4]{C0} Um ciclo BOT e encerra  {CD}--once{C0}")
    print(f"  {C2}  [5]{C0} Abrir navegador         {CD}http://localhost:8001{C0}")
    print()
    print(f"  {C5}{CB}  PROCESSAMENTO{C0}")
    print(f"  {C2}  [6]{C0} Processar um MES         {CD}AAAA-MM, cache+IA{C0}")
    print(f"  {C2}  [7]{C0} Processar JULHO/2026     {CD}atalho{C0}")
    print(f"  {C2}  [8]{C0} Processar N pendentes    {CD}OCR fila{C0}")
    print(f"  {C2}  [9]{C0} Reprocessar subdetectados")
    print(f"  {C2}  [F]{C0} Ciclo com force-rescan   {CD}cuidado{C0}")
    print(f"  {C2}  [O]{C0} Um ciclo com force-OCR   {CD}Tesseract todas as paginas{C0}")
    print()
    print(f"  {C5}{CB}  CONSULTA{C0}")
    print(f"  {C2}  [S]{C0} Status da fila           {CD}completo{C0}")
    print(f"  {C2}  [U]{C0} Ultimas publicacoes")
    print(f"  {C2}  [P]{C0} Buscar publicacao        {CD}termo{C0}")
    print(f"  {C2}  [M]{C0} Mencoes de um mes")
    print(f"  {C2}  [Y]{C0} Resumo mensal            {CD}edicoes/pubs{C0}")
    print(f"  {C2}  [J]{C0} Status julho/2026")
    print(f"  {C2}  [I]{C0} Status da IA / chaves")
    print()
    print(f"  {C5}{CB}  FERRAMENTAS{C0}")
    print(f"  {C2}  [A]{C0} Teste de notificacao")
    print(f"  {C2}  [B]{C0} Backup do banco")
    print(f"  {C2}  [R]{C0} Reconstruir pasta atos/")
    print(f"  {C2}  [E]{C0} Exportar CSV do mes")
    print(f"  {C2}  [T]{C0} Rodar pytest")
    print(f"  {C2}  [L]{C0} Remover lock travado")
    print(f"  {C2}  [Q]{C0} Limpar jobs travados")
    print(f"  {C2}  [G]{C0} Ver final do log")
    print(f"  {C2}  [D]{C0} Espaco em disco")
    print(f"  {C2}  [K]{C0} Bot Telegram interativo")
    print(f"  {C2}  [W]{C0} Abrir pasta do projeto")
    print(f"  {C2}  [H]{C0} Ajuda rapida")
    print()
    print(f"  {C4}  [C]{C0} Limpar dados processados  {CD}pede SIM{C0}")
    print(f"  {C2}  [0]{C0} Sair")
    print()
    print(f"  {C1}============================================================{C0}")


def ajuda() -> None:
    clear()
    print(f"\n  {C1}{CB}  AJUDA RAPIDA{C0}\n")
    print(f"  {C5}Dia a dia{C0}")
    print("    [1] sobe Web + BOT e deixa rodando")
    print("    [S] ve fila, lock, jobs e ultimas edicoes")
    print("    [U] [P] consulta publicacoes\n")
    print(f"  {C5}Validar um mes{C0}")
    print("    [6] processa AAAA-MM via cache OCR + IA")
    print("    [7] atalho julho/2026")
    print("    [M] lista mencoes  -  [Y] resumo mensal  -  [E] exporta CSV\n")
    print(f"  {C5}Fila grande{C0}")
    print("    [8] processa N pendentes OCR real")
    print("    [3] BOT continuo esvazia a fila sozinho")
    print("    [L] [Q] se travar lock / jobs rodando\n")
    print(f"  {C5}Qualidade{C0}")
    print("    [9] reprocessa subdetectados com IA")
    print("    [I] checa chave/modelo da IA")
    print("    [A] teste Telegram/arquivo")
    print("    [O] force-OCR se texto embutido for ruim\n")
    print(f"  {C5}Cuidado{C0}")
    print("    [F] force-rescan  -  [C] apaga pubs/mencoes pede SIM\n")
    print(f"  {C5}Pastas{C0}")
    print("    edicoes\\   PDFs + .ocr.json")
    print("    atos\\      saidas por publicacao")
    print("    logs\\      monitor.log + backups")
    print("    exportacoes\\  CSV/JSON do menu [E]")
    print("    alertas\\   fallback se Telegram falhar")
    print()
    pause()


def ver_log() -> None:
    clear()
    log = ROOT / "logs" / "monitor.log"
    print(f"\n  {C2}Ultimas 50 linhas de logs\\monitor.log{C0}\n")
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-50:]:
            print(line)
    else:
        print("  Log nao encontrado.")
    print(f"\n  {CD}Outros logs em logs\\{C0}")
    logs_dir = ROOT / "logs"
    if logs_dir.is_dir():
        for p in sorted(logs_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            print(f"    {p.name}")
    print()
    pause()


def dispatch(op: str) -> bool:
    """Retorna False para sair do menu."""
    op = (op or "").strip().upper()
    if op == "0":
        print(f"\n  {C2}Ate logo.{C0}")
        return False

    if op == "1":
        clear()
        print(f"\n  {C2}Iniciando Web + BOT...{C0}")
        print(f"  {CD}Ctrl+C encerra tudo.{C0}\n")
        code = run_py("iniciar_tudo.py")
        if code:
            print(f"\n  {C4}Falha. Verifique o Python no PATH e o .env{C0}")
            pause()
        return True

    if op == "2":
        clear()
        print(f"\n  {C2}WEB{C0} em http://localhost:8001")
        print(f"  {CD}Ctrl+C encerra.{C0}\n")
        code = run_py("run_interface.py")
        if code:
            pause()
        return True

    if op == "3":
        clear()
        print(f"\n  {C2}BOT{C0} continuo...")
        print(f"  {CD}Ctrl+C encerra.{C0}\n")
        code = run_py("main.py")
        if code:
            pause()
        return True

    if op == "4":
        clear()
        print(f"\n  {C2}Um ciclo{C0} do BOT --once...\n")
        run_py("main.py", "--once")
        pause()
        return True

    if op == "5":
        webbrowser.open("http://localhost:8001")
        print(f"\n  {C2}Navegador aberto.{C0} Se a web nao subiu, use a opcao [1] ou [2].")
        pause()
        return True

    if op == "6":
        clear()
        print(f"\n  {C2}Processar um mes{C0} - cache OCR + IA")
        print("  Exemplo: 2026-07   2026-06   2025-12\n")
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

    if op == "7":
        clear()
        print(f"\n  {C2}Processando JULHO/2026...{C0}\n")
        run_py("scripts/_processar_mes.py", "2026-07")
        pause()
        return True

    if op == "8":
        clear()
        print(f"\n  {C2}Processar pendentes{C0} - OCR + deteccao + IA\n")
        n = ask("Quantas edicoes", "5") or "5"
        run_py("scripts/_processar_pendentes.py", "--limite", n)
        pause()
        return True

    if op == "9":
        clear()
        print(f"\n  {C2}Reprocessar subdetectados{C0} - IA\n")
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

    if op == "F":
        clear()
        print(f"\n  {C3}Force-rescan: reprocessa conhecidas + um ciclo.{C0}\n")
        conf = ask("Digite SIM para continuar")
        if conf.upper() != "SIM":
            print("  Cancelado.")
            pause()
            return True
        run_py("main.py", "--once", "--force-rescan")
        pause()
        return True

    if op == "O":
        clear()
        print(f"\n  {C3}Force-OCR: Tesseract em todas as paginas + um ciclo.{C0}")
        print(f"  {CD}Mais lento; use se o texto embutido estiver ruim.{C0}\n")
        conf = ask("Digite SIM para continuar")
        if conf.upper() != "SIM":
            print("  Cancelado.")
            pause()
            return True
        run_py("main.py", "--once", "--force-ocr")
        pause()
        return True

    if op == "S":
        clear()
        run_py("scripts/_status_fila.py")
        pause()
        return True

    if op == "U":
        clear()
        n = ask("Quantas publicacoes", "15") or "15"
        mesf = ask("Filtrar mes AAAA-MM (Enter=todos)")
        args = ["scripts/_ultimas_publicacoes.py", "-n", n]
        if mesf:
            args += ["--mes", mesf]
        run_py(*args)
        pause()
        return True

    if op == "P":
        clear()
        print(f"\n  {C2}Buscar publicacoes{C0}")
        print("  Exemplos: aditivo  prefeitura  04/2026  contrato\n")
        termo = ask("Termo")
        if not termo:
            print("  Cancelado.")
            pause()
            return True
        run_py("scripts/_buscar_publicacoes.py", termo)
        pause()
        return True

    if op == "M":
        clear()
        print(f"\n  {C2}Mencoes de um mes{C0}")
        mes = ask("Mes", "2026-07") or "2026-07"
        run_py("scripts/_listar_mencoes.py", mes)
        pause()
        return True

    if op == "Y":
        clear()
        run_py("scripts/_resumo_mensal.py")
        pause()
        return True

    if op == "J":
        clear()
        run_py("scripts/_status_julho.py")
        pause()
        return True

    if op == "I":
        clear()
        run_py("scripts/_ia_status.py")
        pause()
        return True

    if op == "A":
        clear()
        print(f"\n  {C2}Teste de notificacao...{C0}\n")
        run_py("main.py", "--notify-test")
        pause()
        return True

    if op == "B":
        clear()
        print(f"\n  {C2}Backup do banco...{C0}\n")
        run_py("scripts/backup_db.py")
        pause()
        return True

    if op == "R":
        clear()
        print(f"\n  {C2}Reconstruindo pasta atos/ a partir do banco...{C0}\n")
        run_py("scripts/reconstruir_atos.py")
        pause()
        return True

    if op == "E":
        clear()
        print(f"\n  {C2}Exportar publicacoes CSV{C0}  {CD}pasta exportacoes/{C0}")
        mes = ask("Mes", "2026-07") or "2026-07"
        run_py("scripts/_exportar_mes.py", mes, "--json")
        pause()
        return True

    if op == "T":
        clear()
        print(f"\n  {C2}pytest...{C0}\n")
        run_py("-m", "pytest", "tests/", "-q", "--tb=line")
        pause()
        return True

    if op == "L":
        clear()
        print(f"\n  {C2}Removendo lock...{C0}\n")
        run_py("scripts/_remover_lock.py")
        pause()
        return True

    if op == "Q":
        clear()
        print(f"\n  {C2}Limpar jobs travados status=rodando...{C0}\n")
        run_py("scripts/_limpar_jobs.py")
        apagar = ask("Apagar tambem jobs de erro? [s/N]", "N")
        if apagar.upper() == "S":
            run_py("scripts/_limpar_jobs.py", "--apagar-erros")
        pause()
        return True

    if op == "G":
        ver_log()
        return True

    if op == "D":
        clear()
        run_py("scripts/_espaco_disco.py")
        pause()
        return True

    if op == "K":
        clear()
        print(f"\n  {C2}Bot Telegram interativo{C0}")
        print(f"  {CD}Ctrl+C encerra.{C0}\n")
        code = run_py("telegram_bot.py")
        if code:
            pause()
        return True

    if op == "W":
        if sys.platform == "win32":
            os.startfile(str(ROOT))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(ROOT)], check=False)
        print(f"\n  {C2}Pasta do projeto aberta.{C0}")
        pause()
        return True

    if op == "H":
        ajuda()
        return True

    if op == "C":
        clear()
        print(f"\n  {C4}ATENCAO: apaga publicacoes, mencoes, jobs e zera flags OCR.{C0}")
        print(f"  {CD}Mantem: edicoes, PDFs e caches .ocr.json{C0}\n")
        conf = ask("Digite SIM para confirmar")
        if conf.upper() != "SIM":
            print("  Cancelado.")
            pause()
            return True
        run_py("scripts/_limpar_processados.py")
        pause()
        return True

    print(f"\n  {C4}Opcao invalida. Digite H para ajuda.{C0}")
    pause()
    return True


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
