"""P90 90-day source-order audit is reproducible, read-only in pilot mode.

Synthetic fixtures are deliberately tiny and use real source+ledger schema;
they never claim that source row ordinal is Bybit WebSocket sequence.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.p90l90_causal import modeled_pnl_after_fees
from scripts.research import p90l90_source_order_ledger_audit_v1 as audit

SHA = "a" * 40
START = datetime(2026, 5, 8, tzinfo=UTC)


def _create(root, *, bad_trade_id=False, bad_net=False, regress=False):
    raw_root = root / "inputs" / "LTCUSDT" / "public_trades"
    raw_root.mkdir(parents=True)
    result = root / "results" / audit.LEDGER_DIRS["MAIN"]
    result.mkdir(parents=True)
    fieldnames = list(audit.COLUMNS)
    files = []
    for i in range(100):
        day = (START + timedelta(days=i)).date().isoformat()
        day_rows = [
            {
                "timestamp": str(Decimal("1778198400.1") + i * 86400),
                "symbol": "LTCUSDT", "side": "Sell", "size": "1",
                "price": "100", "tickDirection": "ZeroMinusTick",
                "trdMatchID": f"native-warmup-{i}",
            }
        ]
        if i == 10:
            day_rows = [
                {
                    "timestamp": "1779062400.1",
                    "symbol": "LTCUSDT", "side": "Sell",
                    "size": "1", "price": "100",
                    "tickDirection": "ZeroMinusTick",
                    "trdMatchID": "native-entry" if not bad_trade_id else "forged",
                },
                {
                    "timestamp": "1779062402.1" if not regress else "1779062399.9",
                    "symbol": "LTCUSDT", "side": "Buy",
                    "size": "1", "price": "101",
                    "tickDirection": "PlusTick",
                    "trdMatchID": "native-exit",
                },
            ]
        path = raw_root / f"LTCUSDT{day}.csv.gz"
        if i <= 10:
            with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(day_rows)
            size, sha = path.stat().st_size, audit._sha256(path)
        else:
            size, sha = 0, "0" * 64
        files.append({
            "date": day, "file": path.name, "rows": len(day_rows),
            "size": size, "sha256": sha,
        })
    (root / "inputs" / "manifest_LTCUSDT.json").write_text(
        json.dumps({"symbol":"LTCUSDT","files":files,
                    "input_rows_counted_warmup":10}), encoding="utf-8",
    )
    fee = modeled_pnl_after_fees(
        side="LONG", entry_price=Decimal("100"),
        exit_price=Decimal("101"), entry_notional_usdt=Decimal("10"),
        maker_exit=False,
    )
    ledger = []
    for scenario in ("BASELINE", "SECOND_48H"):
        ledger.append({
            "symbol": "LTCUSDT", "side": "LONG",
            "portfolio_scenario": scenario,
            "scenario": "PREV7D_TRAIL035",
            "entry_source_order": 11,
            "exit_source_order": 12,
            "entry_trade_id": "native-entry",
            "entry_ts": "1779062400.1",
            "exit_ts": "1779062402.1",
            "entry_price": "100", "exit_price": "101",
            "exit_reason": "PROTECTED_STOP", "maker_exit": False,
            "modeled_exchange_fill": False,
            "gross_usdt": str(fee.gross_usdt),
            "entry_fee_usdt": str(fee.entry_fee_usdt),
            "exit_fee_usdt": str(fee.exit_fee_usdt),
            "net_usdt": str(fee.net_usdt + (Decimal("0.01") if bad_net else 0)),
        })
    payload = "".join(json.dumps(row, sort_keys=True)+"\n" for row in ledger)
    (result / "trade_ledger.jsonl").write_text(payload, encoding="utf-8")
    money = {
        "gross_usdt": str(fee.gross_usdt),
        "entry_fees_usdt": str(fee.entry_fee_usdt),
        "exit_fees_usdt": str(fee.exit_fee_usdt),
        "net_usdt": str(fee.net_usdt),
    }
    (result / "result.json").write_text(json.dumps({
        "ledger_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "ledger_rows": 2,
        "net_pnl_usdt": {
            "BASELINE": str(fee.net_usdt),
            "SECOND_48H": str(fee.net_usdt),
        },
        "scenarios": {
            "BASELINE": {"money":money}, "SECOND_48H": {"money":money},
        },
    }), encoding="utf-8")
    return ledger


def test_ten_day_warmup_1_based_source_order_and_new_fee_code(tmp_path):
    root = tmp_path / "source"
    _create(root)
    result = audit.run(
        root=root, symbols=("LTCUSDT",), days=11, source_commit=SHA,
    )
    assert result["source_rows_checked"] == 12
    assert result["symbols"][0]["matched_reference_endpoints"] == 4
    assert result["symbols"][0]["remaining_reference_endpoints"] == 0
    assert result["complete_five_symbol_source_audit"] is False
    assert result["live_websocket_exchange_seq_parity"] is False
    assert result["full_new_entry_exit_replay"] is False


def test_forged_native_identity_is_detected_at_exact_ledger_row(tmp_path):
    root = tmp_path / "source"
    _create(root, bad_trade_id=True)
    with pytest.raises(RuntimeError, match="SOURCE_ORDER_ENTRY_MISMATCH"):
        audit.run(root=root, symbols=("LTCUSDT",), days=11, source_commit=SHA)


def test_frozen_fee_ledger_cannot_disagree_with_current_model(tmp_path):
    root = tmp_path / "source"
    _create(root, bad_net=True)
    with pytest.raises(RuntimeError, match="NEW_CODE_FEE_PARITY_FAILED"):
        audit.run(root=root, symbols=("LTCUSDT",), days=11, source_commit=SHA)


def test_timestamp_order_regression_rejected_before_ledger_match(tmp_path):
    root = tmp_path / "source"
    _create(root, regress=True)
    with pytest.raises(RuntimeError, match="SOURCE_ARCHIVE_EVENT_TIME_REGRESSION"):
        audit.run(root=root, symbols=("LTCUSDT",), days=11, source_commit=SHA)


def test_non_exact_source_commit_or_missing_universe_rejected(tmp_path):
    with pytest.raises(ValueError, match="exact 40-char"):
        audit.run(root=tmp_path, symbols=("LTCUSDT",), days=1, source_commit="fake")
    with pytest.raises(ValueError, match="invalid source"):
        audit.run(root=tmp_path, symbols=("BTCUSDT",), days=1, source_commit=SHA)
