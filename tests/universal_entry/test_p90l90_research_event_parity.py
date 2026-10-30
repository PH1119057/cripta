"""P90L90 paper native tick parity against reference research in an isolated process.

Other research tests intentionally monkeypatch scripts.research.* at collection.
Running the frozen reference in a clean subprocess avoids those module stubs.
No files written, network access, or exchange orders. This is event-level,
NOT full 90-day raw dataset replay.
"""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalExit,
    modeled_pnl_after_fees,
)

D = Decimal
CASES = (
    ("LONG", ("100.20", "100.50", "100.90", "100.66"), (None,) * 4),
    ("LONG", ("100.2", "100.6", "100.7", "101.2"),
     (None, None, "101", None)),
    ("LONG", ("99.5", "98", "94", "89"), (None,) * 4),
    ("SHORT", ("99.9", "99.5", "99.1", "99.4"), (None,) * 4),
    ("SHORT", ("99.7", "99.5", "99.3", "98.7"),
     (None, None, "99", None)),
    ("SHORT", ("100.3", "103", "108", "111"), (None,) * 4),
)

REFERENCE = """
import json
import sys
from decimal import Decimal as D
from scripts.research.p90l90_exit_90d_v2 import (
    Position, gross_and_net, initial_position, on_trade,
)
output = []
for side, prices, targets in json.load(sys.stdin):
    pos = initial_position(
        {"side": side, "price": "100", "ts": "1"},
        seq=0, bucket=0, cohort="PREV7D", exit_kind="TRAIL035",
    )
    ticks = []
    for seq, (price, target) in enumerate(zip(prices, targets, strict=True), 1):
        bucket = 300 if seq >= 3 else 0
        pos, exit_row = on_trade(
            pos, D(price), seq, bucket,
            None if target is None else D(target), "TRAIL035",
        )
        if exit_row is not None:
            reason, exit_price, maker = exit_row
            fee_pos = Position(
                side=side, entry=D("100"), entry_seq=0, entry_ts=1.,
                state_bucket=0, stop=D("90") if side == "LONG" else D("110"),
            )
            fees = gross_and_net(fee_pos, exit_price, maker_exit=maker)
            ticks.append({
                "reason": reason, "exit": str(exit_price), "maker": maker,
                "fees": list(map(str, fees)),
            })
            break
        ticks.append({
            "stop": str(pos.stop),
            "best": str(pos.best_favorable),
            "protected": pos.protected,
            "target": None if pos.tp is None else str(pos.tp),
            "target_live": pos.tp_live_after_seq,
        })
    output.append(ticks)
print(json.dumps(output))
"""


def test_native_research_exit_and_fees_agree_per_event():
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", REFERENCE],
        input=json.dumps(CASES), text=True,
        capture_output=True, check=True, timeout=20, cwd=root,
    )
    expected = json.loads(proc.stdout)
    for (side, prices, targets), expected_ticks in zip(CASES, expected, strict=True):
        state = P90L90CausalExit(
            side=side,  # type: ignore[arg-type]
            entry_price=D("100"), entry_sequence=0, state_bucket=0,
        )
        assert len(expected_ticks) <= len(prices)
        for seq, (price, target, reference) in enumerate(
            zip(prices, targets, expected_ticks, strict=False), start=1
        ):
            result = state.on_trade(
                price=D(price), sequence=seq, bucket=300 if seq >= 3 else 0,
                candidate_opposite_raw_tp=None if target is None else D(target),
            )
            if "reason" in reference:
                assert result is not None
                assert result.reason == reference["reason"]
                assert result.modeled_price == D(reference["exit"])
                assert result.resting_maker_proxy is reference["maker"]
                fees = modeled_pnl_after_fees(
                    side=side,  # type: ignore[arg-type]
                    entry_price=D("100"), exit_price=result.modeled_price,
                    entry_notional_usdt=D("10"),
                    maker_exit=result.resting_maker_proxy,
                )
                assert [
                    fees.gross_usdt, fees.entry_fee_usdt,
                    fees.exit_fee_usdt, fees.net_usdt,
                ] == [D(value) for value in reference["fees"]]
                break
            assert result is None
            assert state.stop == D(reference["stop"])
            assert state.best_favorable_pct == D(reference["best"])
            assert state.protected is reference["protected"]
            assert state.target == (
                None if reference["target"] is None else D(reference["target"])
            )
            assert state.target_live_after_sequence == reference["target_live"]
