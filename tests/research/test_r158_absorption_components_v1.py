"""#158/#16 independent native executed trades, exact book changes, price tests."""
import gzip
import importlib.util
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_absorption_components_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_absorption", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

DAY = date(2026, 5, 18)
TS = int(datetime(2026, 5, 18, tzinfo=UTC).timestamp() * 1000)
SYM = "APTUSDT"


def snapshot():
    return {
        "topic": f"orderbook.200.{SYM}", "type": "snapshot", "ts": TS,
        "data": {
            "s": SYM, "u": 100, "seq": 1000,
            "b": [[str(300 - k), "10"] for k in range(200)],
            "a": [[str(310 + k), "10"] for k in range(200)],
        },
    }


def delta(b=None, a=None, *, u=101, seq=1001, ts=TS + 100):
    return {
        "topic": f"orderbook.200.{SYM}", "type": "delta", "ts": ts,
        "data": {
            "s": SYM, "u": u, "seq": seq,
            "b": b if b is not None else [],
            "a": a if a is not None else [],
        },
    }


def stage(tmp_path, records=None, trades=None):
    raw = tmp_path / SYM
    raw.joinpath("orderbook").mkdir(parents=True, exist_ok=True)
    raw.joinpath("public_trades").mkdir(parents=True, exist_ok=True)
    if records is not None:
        name = f"{DAY}_{SYM}_ob200.data"
        path = raw / "orderbook" / f"{name}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name, "\n".join(json.dumps(item) for item in records) + "\n")
    if trades is not None:
        path = raw / "public_trades" / f"{SYM}{DAY}.csv.gz"
        with gzip.open(path, "wt", newline="") as fh:
            fh.write("timestamp,symbol,side,size,price,trdMatchID\n")
            for row in trades:
                fh.write(",".join(str(item) for item in row) + "\n")
    return tmp_path


def test_independent_native_book_trade_and_price_components(tmp_path):
    trade_seconds = TS // 1000
    root = stage(
        tmp_path,
        [snapshot(), delta(b=[["300", "12"]], a=[["310", "7"]])],
        [
            (trade_seconds + 1, SYM, "Buy", "2", "5", "a"),
            (trade_seconds + 2, SYM, "Sell", "3", "4", "b"),
        ],
    )
    manifest, result = m.calculate(SYM, DAY, root)
    current = result[0]
    assert manifest["proxy_classification"] == "ABSORPTION_EVIDENCE_PROXY_COMPONENTS_ONLY"
    assert manifest["available_after"] is None
    assert current["book_status"] == "OBSERVED"
    assert current["trade_status"] == "OBSERVED"
    assert current["book_deltas_valid"] == 1
    assert current["bid_added_quote_usdt"] == "600"
    assert current["bid_removed_quote_usdt"] == "0"
    assert current["ask_added_quote_usdt"] == "0"
    assert current["ask_removed_quote_usdt"] == "930"
    assert current["aggressive_buy_quote_usdt"] == "10"
    assert current["aggressive_sell_quote_usdt"] == "12"
    assert current["within_minute_executed_price_change_pct"] == "-20.0"
    assert current["executed_absorption_confirmed"] is None
    assert current["causal_asof_join_verified"] is False
    assert result[1]["trade_status"] == "NO_DATA"


def test_ob200_out_of_view_price_is_unknown_not_zero(tmp_path):
    root = stage(tmp_path, [snapshot(), delta(b=[["1", "99"]])], [])
    _, rows = m.calculate(SYM, DAY, root)
    assert rows[0]["book_status"] == "UNKNOWN"
    assert rows[0]["book_deltas_unknown"] == 1
    assert rows[0]["bid_added_quote_usdt"] is None


def test_source_gap_poison_until_fresh_snapshot(tmp_path):
    root = stage(tmp_path, [snapshot(), delta(u=102)], [])
    manifest, rows = m.calculate(SYM, DAY, root)
    assert manifest["orderbook"]["gaps"] == 1
    assert rows[0]["book_status"] == "UNKNOWN"


def test_missing_files_have_independent_source_states(tmp_path):
    manifest, result = m.calculate(SYM, DAY, tmp_path)
    assert manifest["orderbook"]["state"] == "SOURCE_MISSING"
    assert manifest["perp_trades"]["state"] == "SOURCE_MISSING"
    assert result[0]["book_status"] == "NO_DATA"
    assert result[0]["trade_status"] == "NO_DATA"


def test_invalid_trade_symbol_and_price_fail(tmp_path):
    t = TS // 1000
    root = stage(tmp_path, [snapshot()], [(t, "DOTUSDT", "Buy", 1, 2, "a")])
    with pytest.raises(ValueError, match="identity"):
        m.calculate(SYM, DAY, root)
    root = stage(tmp_path, [snapshot()], [(t, SYM, "Buy", 1, "NaN", "a")])
    with pytest.raises(ValueError, match="non-finite"):
        m.calculate(SYM, DAY, root)


def test_trade_timestamp_backwards_inside_same_minute_fails(tmp_path):
    t = TS // 1000
    root = stage(
        tmp_path,
        [snapshot()],
        [
            (t + 20, SYM, "Buy", 1, 3, "x"),
            (t + 10, SYM, "Sell", 1, 2, "y"),
        ],
    )
    with pytest.raises(ValueError, match="unsorted"):
        m.calculate(SYM, DAY, root)


def test_crossed_original_snapshot_fails(tmp_path):
    snap = snapshot()
    snap["data"]["a"][0] = ["299", "10"]
    root = stage(tmp_path, [snap], [])
    with pytest.raises(ValueError, match="crossed original snapshot"):
        m.calculate(SYM, DAY, root)


def test_native_resnapshot_marks_continuity_unknown_not_zero(tmp_path):
    initial = snapshot()
    first = delta(b=[["300", "12"]])
    resumed = snapshot()
    resumed["ts"] = TS + 300
    resumed["data"]["u"] = 200
    resumed["data"]["seq"] = 2000
    next_delta = delta(b=[["300", "11"]], u=201, seq=2001, ts=TS + 400)
    root = stage(tmp_path, [initial, first, resumed, next_delta], [])
    meta, rows = m.calculate(SYM, DAY, root)
    assert meta["orderbook"]["snapshots"] == 2
    assert rows[0]["book_deltas_valid"] == 2
    assert rows[0]["book_deltas_unknown"] == 1
    assert rows[0]["book_status"] == "UNKNOWN"
    assert rows[0]["bid_added_quote_usdt"] is None
