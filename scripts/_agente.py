# -*- coding: utf-8 -*-
"""CLI do agente de vigilância.

Uso:
  python scripts/_agente.py                  # daemon (loop)
  python scripts/_agente.py --once           # um pulse + um cérebro
  python scripts/_agente.py --pulse          # só pulse
  python scripts/_agente.py --cerebro        # só cérebro
  python scripts/_agente.py --status
  python scripts/_agente.py --log 20
  python scripts/_agente.py --on
  python scripts/_agente.py --off
  python scripts/_agente.py --modo formiga
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from agente import (
    MODOS,
    agente_esta_ativo,
    format_ciclo,
    listar_log,
    loop_daemon,
    modo_efetivo,
    resolver_modo_auto,
    run_cerebro,
    run_pulse,
    set_agente_ativo,
    set_agente_modo,
    status_agente,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Agente de vigilância")
    ap.add_argument("--once", action="store_true", help="Um ciclo pulse+cérebro e sai")
    ap.add_argument("--pulse", action="store_true")
    ap.add_argument("--cerebro", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--log", type=int, nargs="?", const=20, default=0)
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--modo", choices=list(MODOS), help="Define modo permanente")
    ap.add_argument("--daemon", action="store_true", help="Loop contínuo (padrão se sem flags)")
    args = ap.parse_args()

    database.init_db()

    if args.on:
        set_agente_ativo(True)
        print("Agente LIGADO")
    if args.off:
        set_agente_ativo(False)
        print("Agente DESLIGADO")
    if args.modo:
        set_agente_modo(args.modo)
        print(f"Modo: {args.modo}")

    if args.status or args.on or args.off or args.modo:
        st = status_agente()
        print("\n  === Status do Agente ===")
        print(f"  Ativo:          {st['ativo']}")
        print(f"  Modo config:    {st['modo_config']}")
        print(f"  Modo efetivo:   {st['modo_efetivo']}")
        print(f"  Ult. pulse:     {st['ultimo_pulse'] or '—'}")
        print(f"  Ult. cerebro:   {st['ultimo_cerebro'] or '—'}")
        print(f"  IA nesta hora:  {st['ia_hora'] or '0'}")
        print(f"  Pulse a cada:   {st['pulse_s']}s")
        print(f"  Cerebro a cada: {st['cerebro_min']} min")
        print(f"  Max OCR/ciclo:  {st['max_ocr']}")
        print(f"  Max IA/hora:    {st['max_ia_hora']}")
        print(f"  No BOT idle:    {st['no_bot']}")
        if st["recentes"]:
            print("\n  Ultimas acoes:")
            for r in st["recentes"]:
                print(
                    f"    [{r['criado_em']}] {r['ciclo']}/{r['modo']} "
                    f"{r['acao']}: {(r['detalhe'] or '')[:70]}"
                )
        print()
        if not (args.once or args.pulse or args.cerebro or args.daemon or args.log):
            return 0

    if args.log:
        rows = listar_log(args.log)
        print(f"\n  === Log do agente ({len(rows)}) ===\n")
        for r in rows:
            mark = "OK" if r["ok"] else "ER"
            print(
                f"  [{mark}] {r['criado_em']} {r['ciclo']}/{r['modo']} "
                f"{r['acao']}: {(r['detalhe'] or '')[:90]}"
            )
        print()
        if not (args.once or args.pulse or args.cerebro or args.daemon):
            return 0

    if args.pulse:
        print(format_ciclo(run_pulse(force=True)))
        return 0
    if args.cerebro:
        print(format_ciclo(run_cerebro(force=True)))
        return 0
    if args.once:
        print(format_ciclo(run_pulse(force=True)))
        print()
        print(format_ciclo(run_cerebro(force=True)))
        return 0

    # daemon padrão
    print(
        f"Agente daemon · ativo={agente_esta_ativo()} · "
        f"modo={modo_efetivo()} (efetivo={resolver_modo_auto() if modo_efetivo()=='auto' else modo_efetivo()})"
    )
    print("Ctrl+C para encerrar.\n")
    loop_daemon(once=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
