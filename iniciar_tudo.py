"""Inicia web + rastreador em um único terminal.

Uso:
    python iniciar_tudo.py
    iniciar.bat
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_PORT = int(os.getenv("WEB_PORT", "8001"))

_CORES = {
    "WEB": "\033[96m",
    "BOT": "\033[92m",
    "SYS": "\033[95m",
}
_RESET = "\033[0m"


def _sys(msg: str) -> None:
    print(f"{_CORES['SYS']}[SYS]{_RESET} {msg}", flush=True)


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _porta_livre(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pids_na_porta(port: int) -> list[int]:
    """Lista PIDs escutando na porta (Windows netstat / Linux ss|lsof simplificado)."""
    pids: set[int] = set()
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            return []
        needle = f":{port}"
        for line in out.splitlines():
            if "LISTENING" not in line.upper() and "OUVINDO" not in line.upper():
                # netstat PT-BR may say LISTENING still in many systems
                if "LISTENING" not in line and "LISTEN" not in line:
                    continue
            if needle not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid > 0:
                pids.add(pid)
    else:
        try:
            out = subprocess.check_output(
                ["ss", "-lptn", f"sport = :{port}"],
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            import re

            for m in re.finditer(r"pid=(\d+)", out):
                pids.add(int(m.group(1)))
        except Exception:
            pass
    return sorted(pids)


def _liberar_porta(port: int) -> None:
    """Encerra processos que ocupam a porta (ex.: uvicorn antigo)."""
    if _porta_livre(port):
        return
    pids = _pids_na_porta(port)
    me = os.getpid()
    pids = [p for p in pids if p != me]
    if not pids:
        _sys(f"Porta {port} ocupada, mas não foi possível identificar o PID.")
        return
    for pid in pids:
        _sys(f"Porta {port} em uso pelo PID {pid} — encerrando processo antigo…")
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            _sys(f"Falha ao encerrar PID {pid}: {exc}")
    # Aguarda a porta liberar
    for _ in range(20):
        if _porta_livre(port):
            _sys(f"Porta {port} liberada.")
            return
        time.sleep(0.25)
    _sys(f"AVISO: porta {port} ainda pode estar ocupada.")


def _pipe_output(proc: subprocess.Popen, label: str) -> None:
    cor = _CORES.get(label, "")
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip("\r\n")
        if not text:
            continue
        print(f"{cor}[{label}]{_RESET} {text}", flush=True)
    proc.wait()


def _start(label: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    proc = subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    t = threading.Thread(target=_pipe_output, args=(proc, label), daemon=True)
    t.start()
    return proc


def main() -> int:
    _enable_windows_ansi()
    os.chdir(ROOT)

    path_extra = [
        r"C:\Program Files\Tesseract-OCR",
        r"C:\Poppler\poppler-24.02.0\Library\bin",
        r"C:\poppler\Library\bin",
    ]
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(path_extra + [env.get("PATH", "")])
    env["PYTHONUNBUFFERED"] = "1"
    env["DEV_RELOAD"] = env.get("DEV_RELOAD", "0")
    env["PYTHONIOENCODING"] = "utf-8"

    py = sys.executable
    # (label, args, required, restart_on_fail)
    # Sem bot Telegram interativo — alertas opcionais ficam só no notifier (arquivo/e-mail).
    servicos: list[tuple[str, list[str], bool, bool]] = [
        ("WEB", [py, "run_interface.py"], True, True),
        ("BOT", [py, "main.py"], True, True),
    ]

    print("=" * 60, flush=True)
    print("  Monitor de Atos — um terminal", flush=True)
    print(f"  Web:  http://localhost:{WEB_PORT}", flush=True)
    print("  Serviços: interface web + rastreador", flush=True)
    print("  Ctrl+C encerra tudo", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)

    # Evita WinError 10048: mata uvicorn/python antigo na porta
    _liberar_porta(WEB_PORT)

    procs: list[subprocess.Popen | None] = []
    restart_count: dict[str, int] = {s[0]: 0 for s in servicos}
    # Labels já tratados após morte (sem reinício ou reinício agendado)
    mortos_finalizados: set[str] = set()
    max_restarts = 3

    for label, args, _req, _rst in servicos:
        _sys(f"Iniciando {label}: {' '.join(args)}")
        procs.append(_start(label, args, env))
        time.sleep(0.5)

    stopping = False

    def _shutdown(*_args) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        _sys("Encerrando serviços…")
        for p in procs:
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        deadline = time.time() + 3
        while time.time() < deadline and any(
            p is not None and p.poll() is None for p in procs
        ):
            time.sleep(0.2)
        for p in procs:
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while not stopping:
            vivos = [p for p in procs if p is not None and p.poll() is None]
            # Continua se ainda há processo vivo OU algum ainda pode reiniciar
            if not vivos and all(
                label in mortos_finalizados for label, *_ in servicos
            ):
                _sys("Todos os processos encerraram.")
                break

            for i, (label, args, required, can_restart) in enumerate(servicos):
                if label in mortos_finalizados:
                    continue
                p = procs[i]
                if p is None:
                    mortos_finalizados.add(label)
                    continue
                code = p.poll()
                if code is None:
                    continue

                # Processo morreu — tratar UMA vez
                if code != 0:
                    _sys(f"{label} saiu com código {code}")
                else:
                    _sys(f"{label} encerrou normalmente (código 0)")
                    mortos_finalizados.add(label)
                    continue

                if (
                    not stopping
                    and can_restart
                    and restart_count[label] < max_restarts
                ):
                    restart_count[label] += 1
                    if label == "WEB":
                        _liberar_porta(WEB_PORT)
                    delay = min(3 * restart_count[label], 10)
                    _sys(
                        f"Reiniciando {label} em {delay}s "
                        f"(tentativa {restart_count[label]}/{max_restarts})…"
                    )
                    time.sleep(delay)
                    if stopping:
                        break
                    procs[i] = _start(label, args, env)
                    # novo processo — não marcar como finalizado
                else:
                    if required:
                        _sys(
                            f"{label} não será reiniciado de novo "
                            f"(limite {max_restarts} tentativas). "
                            f"Se for porta {WEB_PORT}, feche a instância antiga "
                            f"ou rode: netstat -ano | findstr :{WEB_PORT}"
                        )
                    else:
                        _sys(f"{label} opcional encerrado — seguindo sem ele.")
                    mortos_finalizados.add(label)

            time.sleep(1.0)
    except KeyboardInterrupt:
        _shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
