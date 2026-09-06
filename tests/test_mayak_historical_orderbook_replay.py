from __future__ import annotations

import json
import zipfile
from collections import deque
from datetime import UTC, datetime

from bybit_workbench.mayak.research.historical_orderbook_replay import (
    MutableBook,
    ReconstructionState,
    _apply_event,
    _apply_event_light,
    _capture_signal,
    _decode_json,
    _normalize_event,
    _process_archive,
)
from bybit_workbench.mayak.research.historical_signal_backfill import Signal
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent
from bybit_workbench.research.orderbook_pilot_v8 import _normalize_event as legacy_normalize_event


def _data(update_id: int, bid: float, ask: float, *, snapshot: bool = False):
    if snapshot:
        return {
            "u": update_id,
            "b": [["99", str(bid)], ["98", "5"]],
            "a": [["101", str(ask)], ["102", "5"]],
        }
    return {"u": update_id, "b": [["99", str(bid)]], "a": [["101", str(ask)]]}


def _signal(at: float) -> Signal:
    dt = datetime.fromtimestamp(at, UTC)
    return Signal("TESTUSDT", "Long", dt.isoformat(), at, 100.0)


def test_sparse_orderbook_replay_matches_full_live_engine_with_subsecond_updates() -> None:
    base = datetime(2026, 5, 18, 12, 0, tzinfo=UTC).timestamp()
    # Events cover >15m and deliberately contain multiple updates in the same integer second.
    events: list[tuple[float, str, dict]] = []
    update = 1000
    events.append((base, "snapshot", _data(update, 10.0, 11.0, snapshot=True)))
    for second in range(1, 1001):
        update += 1
        events.append((base + second + 0.10, "delta", _data(update, 10.0 + second / 100, 11.0)))
        update += 1
        events.append(
            (base + second + 0.80, "delta", _data(update, 10.0 + second / 100, 11.0 + second / 200))
        )

    target = base + 999.55
    full = CausalMayakReplay(("TESTUSDT",), exact_liquidations=False)
    full.set_supported("linear", {"TESTUSDT"})
    state = ReconstructionState(MutableBook.empty(), deque())
    for serial, (event_at, kind, data) in enumerate(events, start=1):
        if event_at > target:
            break
        # Full route receives the full reconstructed state after every raw event.
        _apply_event(
            state,
            record_type=kind,
            event_at=event_at,
            uid=f"synthetic:{serial}",
            data=data,
        )
        full.feed(
            MarketEvent(
                event_at=event_at,
                kind="ORDERBOOK",
                symbol="TESTUSDT",
                market="linear",
                payload={
                    "bids": [(float(p), q) for p, q in state.book.bids.items()],
                    "asks": [(float(p), q) for p, q in state.book.asks.items()],
                },
            )
        )

    expected = full.snapshot(target)["coin_market_contexts"]["TESTUSDT"]["payload"]["liquidity"][
        "derivatives"
    ]
    sparse = _capture_signal(state, _signal(target))["liquidity"]

    windowed = ReconstructionState.empty()
    for serial, (event_at, kind, data) in enumerate(events, start=1):
        if event_at > target:
            break
        apply = _apply_event if event_at >= target - 1005.0 else _apply_event_light
        apply(
            windowed,
            record_type=kind,
            event_at=event_at,
            uid=f"synthetic:{serial}",
            data=data,
        )
    windowed_liquidity = _capture_signal(windowed, _signal(target))["liquidity"]
    assert sparse == expected
    assert windowed_liquidity == expected


def test_update_id_gap_is_fail_closed() -> None:
    state = ReconstructionState.empty()
    _apply_event(
        state,
        record_type="snapshot",
        event_at=1.0,
        uid="a",
        data=_data(10, 10, 10, snapshot=True),
    )
    try:
        _apply_event(
            state,
            record_type="delta",
            event_at=2.0,
            uid="b",
            data=_data(12, 11, 11),
        )
    except ValueError as exc:
        assert "UPDATE_ID_GAP" in str(exc)
    else:
        raise AssertionError("gap must fail closed")

    light = ReconstructionState.empty()
    _apply_event_light(
        light,
        record_type="snapshot",
        event_at=1.0,
        uid="a",
        data=_data(10, 10, 10, snapshot=True),
    )
    try:
        _apply_event_light(
            light,
            record_type="delta",
            event_at=2.0,
            uid="b",
            data=_data(12, 11, 11),
        )
    except ValueError as exc:
        assert "UPDATE_ID_GAP" in str(exc)
    else:
        raise AssertionError("light path gap must fail closed")


def test_raw_normalization_matches_legacy_p40_semantics() -> None:
    payloads = [
        {
            "type": "snapshot",
            "ts": 1779062400123,
            "data": {"b": [["99", "1"]], "a": [["101", "2"]], "u": 1},
        },
        {
            "type": "delta",
            "ts": 1779062400234,
            "cts": 1779062400220,
            "data": {"b": [["99", "3"]], "a": [["101", "0"]], "u": 2},
        },
    ]
    for payload in payloads:
        assert _normalize_event(payload) == legacy_normalize_event(payload)


def test_json_backend_preserves_normalized_event(monkeypatch) -> None:
    from bybit_workbench.mayak.research import historical_orderbook_replay as replay_mod

    raw = (
        b'{"type":"delta","ts":1779062400234,"cts":1779062400220,'
        b'"data":{"b":[["99","3"]],"a":[["101","0"]],"u":2}}'
    )
    accelerated = _normalize_event(_decode_json(raw))
    installed = replay_mod._orjson
    monkeypatch.setattr(replay_mod, "_orjson", None)
    fallback = _normalize_event(replay_mod._decode_json(raw))
    monkeypatch.setattr(replay_mod, "_orjson", installed)
    assert accelerated == fallback


def test_json_backend_reports_provenance() -> None:
    from bybit_workbench.mayak.research import historical_orderbook_replay as replay_mod

    backend, version = replay_mod._json_backend()
    assert backend in {"orjson", "stdlib-json"}
    if backend == "orjson":
        assert version
    else:
        assert version is None


def test_previous_day_tail_stops_at_next_touch_even_if_archive_spills_past_midnight(
    tmp_path,
) -> None:
    archive_path = tmp_path / "previous_day.data.zip"
    member = "previous_day.data"
    payloads = [
        {
            "type": "snapshot",
            "cts": 86399000,
            "data": {"u": 1, "b": [["99", "1"]], "a": [["101", "1"]]},
        },
        {
            "type": "delta",
            "cts": 86401000,
            "data": {"u": 2, "b": [["99", "2"]], "a": []},
        },
    ]
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, b"".join((json.dumps(item) + "\n").encode() for item in payloads))

    state = ReconstructionState.empty()
    _process_archive(
        archive_path,
        signals=[],
        state=state,
        need_tail=True,
        causal_cutoff=86400.5,
    )
    assert state.last_event_at == 86399.0
    row = _capture_signal(state, _signal(86400.5))
    assert row["source"]["current_event_at"] == 86399.0
