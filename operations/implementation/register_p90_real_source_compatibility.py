#!/usr/bin/env python3
"""Explicit technical registration for owner preparation and verified releases.

Actor cripta/DB cripta. Reads current release, active-source provenance and exact
local Git object bytes for a pruned origin when needed. Only inserts compatibility
evidence. No filesystem writes, network fetch, Exchange calls, provenance edits or
financial authorization.
"""
from __future__ import annotations

import argparse
import os
import pwd
import sys
from pathlib import Path

ROOT = Path("/srv/cripta/runtime/current")
SOURCE_CHECKOUT = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID  # noqa: E402
from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (  # noqa: E402
    P90RealSourceCompatibilityUnverified,
    inspect_real_source_identity,
    register_real_source_compatibility,
)
from bybit_workbench.universal_entry.runtime_loader import (  # noqa: E402
    load_active_strategy_bundles,
)


def perform(
    *,
    release_commit: str,
    epoch_id: str | None,
    active_epoch: bool,
    register: bool,
    release_preparation: bool = False,
) -> None:
    if release_preparation and not (active_epoch and register):
        raise RuntimeError("P90_REAL_COMPATIBILITY_RELEASE_PREPARATION_MODE_INVALID")
    if (os.geteuid() != pwd.getpwnam("cripta").pw_uid
            or ROOT.resolve().name != release_commit
            or Path(sys.prefix).resolve() != (ROOT / ".venv").resolve()
            or Path(__file__).resolve() != (ROOT / "operations/implementation/"
                                           "register_p90_real_source_compatibility.py").resolve()):
        raise RuntimeError("P90_REAL_COMPATIBILITY_OPERATOR_CONTEXT_INVALID")
    with (
        psycopg.connect(
            "dbname=cripta user=cripta host=/var/run/postgresql",
            connect_timeout=5,
            options="-c statement_timeout=15000 -c lock_timeout=3000",
        ) as c,
        c.transaction(),
    ):
        c.execute(
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
            + ("READ WRITE" if register else "READ ONLY")
        )
        if active_epoch:
            rows = c.execute(
                "SELECT epoch_id FROM strategy_entry.p90l90_real_source_epochs "
                "WHERE status='ACTIVE' ORDER BY opened_at,epoch_id"
            ).fetchall()
            if release_preparation and not rows:
                print("P90_REAL_SOURCE_COMPATIBILITY=NOT_APPLICABLE_NO_ACTIVE_EPOCH")
                return
            if len(rows) != 1 or not isinstance(rows[0][0], str):
                raise RuntimeError(
                    "P90_REAL_COMPATIBILITY_ACTIVE_EPOCH_UNVERIFIED"
                )
            epoch_id = rows[0][0]
        if not epoch_id:
            raise RuntimeError("P90_REAL_COMPATIBILITY_EPOCH_REQUIRED")
        bundles = tuple(
            b for b in load_active_strategy_bundles(c)
            if b.card.strategy_id in (MAIN_ID, SUB_ID)
        )
        if release_preparation:
            # A repeat install validates existing evidence without new writes.
            # Only the exact missing-release condition permits registration;
            # corrupt/incompatible evidence must retain its original failure.
            try:
                inspect_real_source_identity(
                    c, epoch_id=epoch_id, loaded_commit=release_commit, bundles=bundles,
                )
            except P90RealSourceCompatibilityUnverified as exc:
                if str(exc) != "P90_REAL_SOURCE_COMPATIBILITY_RELEASE_NOT_REGISTERED":
                    raise
                if c.execute(
                    "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'",
                ).fetchone() != (0,):
                    print("P90_REAL_SOURCE_COMPATIBILITY=DEFERRED_ENTRY_GATE_NOT_OFF")
                    return
            else:
                print("P90_REAL_SOURCE_COMPATIBILITY=PASS ALREADY_VERIFIED=1")
                return
        if register:
            row = c.execute(
                "SELECT source_commit FROM strategy_entry.p90l90_real_source_epochs "
                "WHERE epoch_id=%s", (epoch_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("P90_REAL_COMPATIBILITY_EPOCH_MISSING")
            retained_origin = ROOT.resolve().parent / row[0]
            kwargs = (
                {"origin_root": retained_origin}
                if (retained_origin / "INSTALLED_COMMIT").is_file()
                else {"origin_git_checkout": SOURCE_CHECKOUT}
            )
            register_real_source_compatibility(
                c,
                epoch_id=epoch_id,
                loaded_commit=release_commit,
                bundles=bundles,
                current_root=ROOT.resolve(),
                **kwargs,
            )
        identity = inspect_real_source_identity(
            c, epoch_id=epoch_id, loaded_commit=release_commit, bundles=bundles,
        )
    print(f"P90_REAL_SOURCE_COMPATIBILITY=PASS EPOCH={identity.epoch_id}"
          f" ORIGIN={identity.origin_commit} LOADED={identity.loaded_commit}"
          " PROVENANCE_WRITES=0 EXCHANGE_REQUESTS=0 ARM_WRITES=0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    release = parser.add_mutually_exclusive_group(required=True)
    release.add_argument("--release-commit")
    release.add_argument("--current-release", action="store_true")
    epoch = parser.add_mutually_exclusive_group(required=True)
    epoch.add_argument("--epoch-id")
    epoch.add_argument("--active-epoch", action="store_true")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--register", action="store_true")
    modes.add_argument("--check", action="store_true")
    parser.add_argument("--release-preparation", action="store_true")
    args = parser.parse_args()
    release_commit = (
        ROOT.resolve().name if args.current_release else str(args.release_commit)
    )
    perform(
        release_commit=release_commit,
        epoch_id=args.epoch_id,
        active_epoch=args.active_epoch,
        register=args.register,
        release_preparation=args.release_preparation,
    )
