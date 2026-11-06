#!/usr/bin/env python3
"""Explicit technical registration, never invoked by installer or Observer.

Actor cripta/DB cripta. Reads current and retained origin release, source state,
canonical candles and active immutable plans. Only inserts compatibility evidence.
No filesystem writes, Exchange calls, provenance edits or financial authorization.
"""
from __future__ import annotations

import argparse
import os
import pwd
import sys
from pathlib import Path

ROOT = Path("/srv/cripta/runtime/current")
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID  # noqa: E402
from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (  # noqa: E402
    inspect_real_source_identity,
    register_real_source_compatibility,
)
from bybit_workbench.universal_entry.runtime_loader import (  # noqa: E402
    load_active_strategy_bundles,
)


def perform(*, release_commit: str, epoch_id: str, register: bool) -> None:
    if (os.geteuid() != pwd.getpwnam("cripta").pw_uid
            or ROOT.resolve().name != release_commit
            or Path(sys.prefix).resolve() != (ROOT / ".venv").resolve()
            or Path(__file__).resolve() != (ROOT / "operations/implementation/"
                                           "register_p90_real_source_compatibility.py").resolve()):
        raise RuntimeError("P90_REAL_COMPATIBILITY_OPERATOR_CONTEXT_INVALID")
    with (psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as c,
          c.transaction()):
        c.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
                  + ("READ WRITE" if register else "READ ONLY"))
        bundles = tuple(b for b in load_active_strategy_bundles(c)
                        if b.card.strategy_id in (MAIN_ID, SUB_ID))
        if register:
            row = c.execute(
                "SELECT source_commit FROM strategy_entry.p90l90_real_source_epochs "
                "WHERE epoch_id=%s", (epoch_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("P90_REAL_COMPATIBILITY_EPOCH_MISSING")
            register_real_source_compatibility(
                c, epoch_id=epoch_id, loaded_commit=release_commit, bundles=bundles,
                current_root=ROOT.resolve(),
                origin_root=ROOT.resolve().parent / row[0],
            )
        identity = inspect_real_source_identity(
            c, epoch_id=epoch_id, loaded_commit=release_commit, bundles=bundles,
        )
    print(f"P90_REAL_SOURCE_COMPATIBILITY=PASS EPOCH={identity.epoch_id}"
          f" ORIGIN={identity.origin_commit} LOADED={identity.loaded_commit}"
          " PROVENANCE_WRITES=0 EXCHANGE_REQUESTS=0 ARM_WRITES=0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--epoch-id", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--register", action="store_true")
    modes.add_argument("--check", action="store_true")
    args = parser.parse_args()
    perform(release_commit=args.release_commit, epoch_id=args.epoch_id, register=args.register)
