"""Pure and tiny native-UTC-day tests for resumable P90L90 2025 cash replay."""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter, defaultdict, deque
from decimal import Decimal as D
from pathlib import Path

import pytest

from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as chunk


def _book(side: str = "LONG_ONLY"):
    return chunk.new_books(side, {bps: [] for bps in chunk.old.THRESHOLDS})


def _tiny_native_data(tmp_path: Path, days: int = 2):
    root = tmp_path / "native"
    result = {}
    for priority, symbol in enumerate(chunk.old.SYMBOLS):
        inputs = [{"rows": 1, "file": f"warmup_{i}", "sha256": "0" * 64}
                  for i in range(10)]
        states = {}
        for index in range(days):
            day = chunk.day_date(index)
            t = chunk.old.START_TS + index * 86400 + 60
            price = "100" if index == 0 else "101"
            path = root / "inputs" / symbol / "public_trades" / f"{symbol}{day}.csv.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("timestamp", "price", "trdMatchID"))
                writer.writeheader()
                writer.writerow({"timestamp": str(t), "price": price, "trdMatchID": f"{symbol}-{index}"})
            inputs.append({"rows": 1, "file": path.name, "sha256": chunk.old.sha(path)})
            states[int(t // 300) * 300] = (D("90"), D("110"), D("1"))
        first_ts = chunk.old.START_TS + 60
        event = {
            "side": "LONG", "PREV7D": True, "price": "100",
            "ts": str(first_ts), "trade_id": f"{symbol}-0",
            "event_id": f"{symbol}-event",
        }
        result[symbol] = {
            "symbol": symbol, "manifest": {"inputs": inputs, "events": 1},
            "events": {11: (event,)} if priority == 0 else {},
            "states": states,
        }
    return root, result


def test_json_roundtrip_preserves_decimal_float_position_counters_and_deque():
    books = _book()
    book = books[chunk.key("NO_PAUSE", "fixed")]
    e = {
        "side": "LONG", "PREV7D": True, "price": "100",
        "ts": str(chunk.old.START_TS + 1),
        "trade_id": "first", "event_id": "first-e",
    }
    assert book.handle("LTCUSDT", chunk.old.START_TS + 1, 77, D("100"), (e,),
                       (D("90"), D("110"), D("1")), 0)
    book.last_stops = deque([chunk.old.START_TS + 2.5])
    book.counts = Counter({"accepted": 1})
    book.m = defaultdict(lambda: D(0), {"all_entry_fee_usdt": D("0.550000000000")})
    snapshot = {"books": chunk.freeze_books(books), "prices": {"LTCUSDT": D("100")},
                "shadow_pos": {"LTCUSDT": book.pos["LTCUSDT"]}}
    restored = chunk.decode(json.loads(json.dumps(chunk.encode(snapshot), allow_nan=False)))
    rebuilt = _book()
    chunk.restore_books(rebuilt, restored["books"])
    second = rebuilt[chunk.key("NO_PAUSE", "fixed")]
    assert second.pos["LTCUSDT"] == book.pos["LTCUSDT"]
    assert second.wallet == book.wallet
    assert second.reserved == book.reserved
    assert second.last_stops == book.last_stops
    assert second.counts["new_counter"] == 0
    assert second.m["new_cash_key"] == D(0)
    assert restored["shadow_pos"]["LTCUSDT"].entry_seq == 77


def test_immutable_chain_validates_resume_and_rejects_tamper(tmp_path: Path):
    folder = tmp_path / "long_only"
    folder.mkdir()
    state = {"next_day": 1, "ticks": 5, "money": D("0.000000000001")}
    state_text = json.dumps(chunk.encode(state), sort_keys=True) + "\n"
    import hashlib
    m = {
        "status": chunk.STATUS, "index": 0, "day": chunk.day_date(0),
        "previous_manifest_sha256": None,
        "ticks_total": 5,
        "state_sha256": hashlib.sha256(state_text.encode()).hexdigest(),
    }
    path = chunk.commit_bundle(folder, chunk.part_name(0), {
        "state.json": state_text,
        "manifest.json": json.dumps(m, sort_keys=True) + "\n",
    })
    s, last, digest = chunk.load_parts(folder)
    assert s == state and last["index"] == 0 and digest == chunk.old.sha(path / "manifest.json")
    with pytest.raises(FileExistsError):
        chunk.commit_bundle(folder, chunk.part_name(0), {})
    (path / "state.json").write_text(state_text + "tamper", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt checkpoint"):
        chunk.load_parts(folder)


def test_resume_is_identical_to_uninterrupted_two_utc_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    root, data = _tiny_native_data(tmp_path)
    monkeypatch.setattr(chunk, "ROOT", root)
    original_books = _book()
    original_state = chunk.blank_state(data)
    chunk.replay_day(0, original_state, data, original_books, "LONG_ONLY")
    checkpoint_text = json.dumps(chunk.encode(original_state), sort_keys=True, allow_nan=False)
    resumed_state = chunk.decode(json.loads(checkpoint_text))
    resumed_books = _book()
    chunk.restore_books(resumed_books, resumed_state["books"])
    # The same second-day print (and new UTC-day mark) must give identical
    # open positions, wallet, 48h pause state, causal geometry and ledger.
    chunk.replay_day(1, original_state, data, original_books, "LONG_ONLY")
    chunk.replay_day(1, resumed_state, data, resumed_books, "LONG_ONLY")
    assert chunk.encode(original_state) == chunk.encode(resumed_state)
    assert chunk.encode(chunk.freeze_books(original_books)) == chunk.encode(
        chunk.freeze_books(resumed_books)
    )
    example = original_books[chunk.key("NO_PAUSE", "fixed")]
    assert example.counts["accepted"] == 1
    assert len(example.daily_marks) == 1
    assert original_state["next_seq"] == {s: 12 for s in chunk.old.SYMBOLS}
    assert original_state["ticks"] == 10


def test_one_day_source_hash_global_sequence_and_timestamp_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    root, data = _tiny_native_data(tmp_path)
    monkeypatch.setattr(chunk, "ROOT", root)
    symbol = chunk.old.SYMBOLS[0]
    ticks = list(chunk.native_day(data[symbol], 0, 10))
    assert len(ticks) == 1 and ticks[0][1] == 11
    assert data[symbol]["day_rows"] == 1
    row = data[symbol]["manifest"]["inputs"][10]
    row["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="daily SHA"):
        list(chunk.native_day(data[symbol], 0, 10))


def test_bounded_run_and_explicit_no_pickle_or_2026_policy_reuse():
    assert chunk.TOTAL_DAYS == 183
    assert chunk.PINNED[-2:] == (chunk.old.SELF_REL, chunk.SELF_REL)
    assert chunk.old.SIDES == ("LONG_ONLY", "SHORT_ONLY")
    source = Path(chunk.__file__).read_text(encoding="utf-8")
    assert "pickle.load" not in source and "pickle.dump" not in source
    assert "original.on_trade(" in source
    assert "book.handle(" in source
    assert "2026-05-18" not in source
    assert chunk.STATUS != "INTERNAL_QC_PASS_PENDING_INDEPENDENT"
    with pytest.raises(ValueError):
        chunk.run("LONG_ONLY", "0" * 40, max_days=11)
