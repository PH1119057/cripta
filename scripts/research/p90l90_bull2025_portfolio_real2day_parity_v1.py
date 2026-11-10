#!/usr/bin/env python3
"""Read-only independent real-Bybit-day financial parity for chunked P90L90 2025.

Run original v1 24h and 48h native five-coin portfolio replay untouched,
capture its 16 in-memory Book states, and compare with separately committed
daily checkpoint states from resumable v2. No research output/DB/trading writes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as chunk  # noqa: E402
from scripts.research import p90l90_bull2025_portfolio_v1 as old  # noqa: E402

SIDE = "LONG_ONLY"
DAYS = (1, 2)


def assert_finances(book: old.Book, label: str) -> None:
    """Independent closed-ledger/fee/cash/slot invariants even mid-position."""
    gross = D(0)
    closed_entry = D(0)
    closed_exit = D(0)
    net = D(0)
    for trade in book.trades:
        g = D(trade["gross_usdt"])
        ef = D(trade["entry_fee_usdt"])
        xf = D(trade["exit_fee_usdt"])
        n = D(trade["net_usdt"])
        if g - ef - xf != n:
            raise ValueError("v1 closed trade row fee mismatch: " + label)
        if trade["side"] != "LONG":
            raise ValueError("wrong portfolio side in real 2025 trade: " + label)
        gross += g
        closed_entry += ef
        closed_exit += xf
        net += n
    m = book.m
    if (
        gross != m.get("gross_usdt", D(0))
        or closed_entry != m.get("closed_entry_fee_usdt", D(0))
        or closed_exit != m.get("exit_fee_usdt", D(0))
        or net != m.get("closed_net_usdt", D(0))
        or len(book.trades) != book.counts["closed"]
        or book.counts["accepted"] != (
            book.counts["closed"] + sum(v is not None for v in book.pos.values())
        )
        or book.reserved != sum(book.margin.values(), D(0))
        or book.wallet - old.START_WALLET != (
            gross - m.get("all_entry_fee_usdt", D(0)) - closed_exit
        )
        or m.get("all_entry_fee_usdt", D(0)) != closed_entry
        + sum(book.open_fee.values(), D(0))
    ):
        raise ValueError("v1 independent real 2025 fee/wallet/reserved mismatch: " + label)


def verify_day(
    day: int,
    source_head: str,
    checkpoint_folder: Path,
    required_sha: dict[int, str],
) -> dict:
    """Replay original unmodified v1, capture only Book.__init__ references."""
    original_init = old.Book.__init__
    captured: dict[str, old.Book] = {}

    def recording_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        k = chunk.key(self.scenario, self.mode)
        if k in captured:
            raise ValueError("duplicate v1 scenario Book: " + k)
        captured[k] = self

    # In-memory instrumentation ONLY: do not alter original price/Entry/Exit.
    old.Book.__init__ = recording_init
    try:
        v1_report = old.run(SIDE, source_head, smoke_hours=day * 24)
    finally:
        old.Book.__init__ = original_init

    if v1_report["status"] != "SMOKE_PASS_2025_NO_OUTPUT":
        raise ValueError("original 2025 v1 replay did not pass")
    if len(captured) != 16:
        raise ValueError("original 2025 v1 16-book collection mismatch")

    folder = checkpoint_folder / chunk.part_name(day - 1)
    m_file = folder / "manifest.json"
    s_file = folder / "state.json"
    manifest = json.loads(m_file.read_text(encoding="utf-8"))
    state = chunk.decode(json.loads(s_file.read_text(encoding="utf-8")))
    if (
        manifest["status"] != chunk.STATUS
        or manifest["index"] != day - 1
        or manifest["day"] != chunk.day_date(day - 1)
        or manifest["state_sha256"] != old.sha(s_file)
        or old.sha(m_file) != required_sha[day]
        or manifest["side"] != SIDE
        or state["next_day"] != day
        or state["ticks"] != v1_report["ticks"]
        or len(state["books"]) != 16
    ):
        raise ValueError("2025 real v1/checkpoint day identity/tick mismatch")
    if day == 2 and manifest["previous_manifest_sha256"] != required_sha[1]:
        raise ValueError("2025 checkpoint day2 does not chain day1")
    if set(captured) != set(state["books"]):
        raise ValueError("2025 checkpoint missing original v1 book keys")

    differences: list[str] = []
    for k, book in captured.items():
        assert_finances(book, k)
        saved = state["books"][k]
        if set(saved) != set(chunk.BOOK_KEYS):
            raise ValueError("v2 checkpoint book state schema mismatch: " + k)
        for field in chunk.BOOK_KEYS:
            actual = chunk.encode(getattr(book, field))
            expected = chunk.encode(saved[field])
            if actual != expected:
                differences.append(f"{k}.{field}")
    if differences:
        raise ValueError(
            "real native 2025 uninterrupted vs resumed portfolio DIFFERENCES "
            + ", ".join(differences[:40])
        )
    return {
        "date": chunk.day_date(day - 1),
        "native_ticks_cumulative": v1_report["ticks"],
        "cases_exact": len(captured),
        "fields_per_case_exact": len(chunk.BOOK_KEYS),
        "all_original_v1_closed_trades_and_fees_exact": True,
        "all_original_v1_open_positions_and_cash_exact": True,
        "all_original_v1_trailing_and_pause_states_exact": True,
        "checkpoint_manifest_sha256": required_sha[day],
        "v1_example_no_pause_fixed": {
            "wallet_usdt": str(captured[chunk.key("NO_PAUSE", "fixed")].wallet),
            "closed": len(captured[chunk.key("NO_PAUSE", "fixed")].trades),
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source-head", required=True)
    args = p.parse_args()
    if os.geteuid() != 107 or old.entry.git_head() != args.source_head:
        raise PermissionError("run real 2025 parity only as pinned source actor cripta")
    folder = chunk.PARTS / SIDE.lower()
    last_state, last_manifest, last_digest = chunk.load_parts(folder)
    if (
        last_state is None or last_manifest is None
        or last_state["next_day"] != 2
        or last_manifest["index"] != 1
        or not last_digest
    ):
        raise ValueError("exactly two previously verified UTC days required")
    chunk.pin_current(last_manifest["source_pin_sha256"],
                      last_manifest["source_head_first"])
    required_sha = {
        day: old.sha(folder / chunk.part_name(day - 1) / "manifest.json")
        for day in DAYS
    }
    if required_sha[2] != last_digest:
        raise ValueError("2025 final two-day chain not pinned")
    if (
        last_manifest["unit_ledger_sha256"] != {
            symbol: old.sha(
                old.UNIT_PARENT / symbol / "trade_ledger_long.jsonl"
            )
            for symbol in old.SYMBOLS
        }
    ):
        raise ValueError("2025 unit reference hashes changed")
    results = [
        verify_day(day, args.source_head, folder, required_sha)
        for day in DAYS
    ]
    summary = {
        "status": "INDEPENDENT_REAL_2025_TWO_DAY_V1_V2_WALLET_PARITY_PASS",
        "side": SIDE, "original_source_head": args.source_head,
        "original_v1_sha256": old.sha(SOURCE / old.SELF_REL),
        "chunked_v2_sha256": old.sha(SOURCE / chunk.SELF_REL),
        "validator_sha256": old.sha(Path(__file__)),
        "per_day": results, "wrote_result_or_checkpoint": False,
        "not_full_2025_portfolio_qc": True,
    }
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
