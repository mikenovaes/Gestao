"""Linha de comando para rotinas agendadas.

Exemplo de crontab (horário de Brasília):
    CRON_TZ=America/Sao_Paulo
    0 6 * * *   cd /srv/gestao && .venv/bin/python -m gestao_obra.cli diaria
    0 7 * * 1   cd /srv/gestao && .venv/bin/python -m gestao_obra.cli semanal
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from .db import SessionLocal, init_db


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gestao_obra")
    parser.add_argument("comando", choices=["init", "seed", "diaria", "semanal", "servir"])
    parser.add_argument("--data", type=date.fromisoformat, default=date.today())
    parser.add_argument("--porta", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    init_db()
    if args.comando == "servir":
        import uvicorn

        uvicorn.run("gestao_obra.api:app", host="0.0.0.0", port=args.porta)
        return

    from . import orquestrador, seed
    from .agentes import relatorio

    with SessionLocal() as db:
        if args.comando == "seed":
            seed.popular(db, args.data)
        elif args.comando == "diaria":
            orquestrador.rotina_diaria(db, args.data)
        elif args.comando == "semanal":
            relatorio.executar_semanal(db, args.data)
        db.commit()


if __name__ == "__main__":
    main()
