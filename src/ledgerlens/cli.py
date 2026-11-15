"""Operator commands.

uv run python -m ledgerlens.cli seed                     # vendors, POs, goods receipts
uv run python -m ledgerlens.cli create-key --name me --role clerk
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.adapters.db.api_keys import SqlApiKeyRepo
from app.adapters.db.engine import create_engine, create_sessionmaker
from app.domain.roles import ROLES, Role
from ledgerlens.adapters.db import repo
from ledgerlens.config import get_ledger_settings
from ledgerlens.evals.dataset import DEFAULT_DIR, load_reference


async def _seed(root: Path) -> None:
    engine = create_engine(get_ledger_settings())
    try:
        async with create_sessionmaker(engine)() as s:
            added = await repo.seed_reference(s, load_reference(root))
    finally:
        await engine.dispose()
    print(f"seeded: {added or 'nothing new'}")


async def _create_key(name: str, role: Role) -> None:
    engine = create_engine(get_ledger_settings())
    try:
        async with create_sessionmaker(engine)() as s:
            key_id, raw = await SqlApiKeyRepo(s).create(name, role)
    finally:
        await engine.dispose()
    print(f"key_id: {key_id}\nrole:   {role}\napi_key: {raw}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ledgerlens.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sd = sub.add_parser("seed", help="load reference data (idempotent)")
    sd.add_argument("--data", type=Path, default=DEFAULT_DIR)
    ck = sub.add_parser("create-key")
    ck.add_argument("--name", required=True)
    ck.add_argument("--role", required=True, choices=ROLES)
    args = p.parse_args(argv)
    if args.cmd == "seed":
        asyncio.run(_seed(args.data))
    else:
        asyncio.run(_create_key(args.name, args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
