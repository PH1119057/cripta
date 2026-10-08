"""Regression: ATR width drift must never cancel unchanged R1 L5-3 structure."""
from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/server/connectivity"))
sys.path.insert(0, str(ROOT / "operations/connectivity"))

import private_runtime  # noqa: E402

from bybit_workbench.exchange.bybit.mappers import map_rest_klines  # noqa: E402
from bybit_workbench.universal_entry.market_watch import (  # noqa: E402
    compute_r1_l53_stable_zone,
)

CHECKPOINT = datetime(2026, 10, 8, 7, 23, 16, tzinfo=UTC)


def _rest_rows() -> list[list[str]]:
    current_bar_open = datetime(2026, 10, 8, 7, 20, tzinfo=UTC)
    rows: list[list[str]] = []
    for i in range(240):
        opened = current_bar_open - timedelta(minutes=(239 - i) * 5)
        early = i < 32
        rows.append(
            [
                str(int(opened.timestamp() * 1000)),
                "7.35",
                "7.65" if early else ("7.50" if i == 225 else "7.37"),
                "7.10" if early else ("7.20" if i == 220 else "7.33"),
                "7.35",
                "100",
            ]
        )
    return list(reversed(rows))


def _install_fake_market(monkeypatch: pytest.MonkeyPatch, rows: list[list[str]]) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return CHECKPOINT

    class Response(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(
                json.dumps({"retCode": 0, "result": {"list": rows}}).encode("utf-8")
            )

    monkeypatch.setattr(private_runtime, "datetime", FixedDatetime)
    monkeypatch.setattr(
        private_runtime,
        "api_get",
        lambda *_args, **_kwargs: (
            {"result": {"list": [{"lastPrice": "7.40"}]}},
            0,
        ),
    )
    monkeypatch.setattr(
        private_runtime.urllib.request, "urlopen", lambda *_args, **_kwargs: Response()
    )


def test_pending_r1_order_survives_identical_closed_geometry_despite_atr_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rest_rows()
    candles = map_rest_klines(
        rows, symbol="INJUSDT", interval="5", observed_at=CHECKPOINT
    )
    closed = tuple(item for item in candles if item.is_closed)
    assert len(closed) == 239
    observer_zone = compute_r1_l53_stable_zone(closed[-207:])
    full_rest_zone = compute_r1_l53_stable_zone(closed)
    assert observer_zone is not None and full_rest_zone is not None
    assert observer_zone.range_low == full_rest_zone.range_low
    assert observer_zone.range_high == full_rest_zone.range_high
    assert observer_zone.atr != full_rest_zone.atr

    _install_fake_market(monkeypatch, rows)
    payload = {
        "side": "Sell",
        "entry_validity": {
            "operator": "R1_EXACT_SIGNAL",
            "signal_entry_price": str(observer_zone.resistance_bottom),
            "signal_target_price": str(observer_zone.support_top),
            "history_limit": 207,
            "signal_structure_low": str(observer_zone.range_low),
            "signal_structure_high": str(observer_zone.range_high),
        },
    }
    assert private_runtime._r1_signal_validity(payload, "INJUSDT") == (
        True, "R1_SIGNAL_STILL_VALID"
    )
    # Even a moved ATR-derived entry boundary must NOT invalidate structure.
    payload["entry_validity"]["signal_entry_price"] = "7.499999999"
    assert private_runtime._r1_signal_validity(payload, "INJUSDT") == (
        True, "R1_SIGNAL_STILL_VALID"
    )


def test_pending_r1_order_fails_closed_if_exact_atr_history_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_market(monkeypatch, _rest_rows()[-205:])
    payload = {
        "side": "Sell",
        "entry_validity": {
            "operator": "R1_EXACT_SIGNAL",
            "signal_entry_price": "7.4",
            "signal_target_price": "7.2",
            "history_limit": 207,
            "signal_structure_low": "7.20",
            "signal_structure_high": "7.50",
        },
    }
    with pytest.raises(
        private_runtime.ExchangeMutationBarrier,
        match="lacks full causal ATR history",
    ):
        private_runtime._r1_signal_validity(payload, "INJUSDT")



def test_pending_r1_order_cancels_on_real_structural_extremum_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rest_rows()
    candles = map_rest_klines(
        rows, symbol="INJUSDT", interval="5", observed_at=CHECKPOINT
    )
    zone = compute_r1_l53_stable_zone(tuple(c for c in candles if c.is_closed)[-207:])
    assert zone is not None
    # Replace one stable-window upper extremum with a new, larger extreme.
    modified_rows = [list(row) for row in rows]
    for row in modified_rows:
        if row[2] == "7.50":
            row[2] = "7.52"
    _install_fake_market(monkeypatch, modified_rows)
    payload = {
        "side": "Sell",
        "entry_validity": {
            "operator": "R1_EXACT_SIGNAL",
            "signal_entry_price": str(zone.resistance_bottom),
            "signal_target_price": str(zone.support_top),
            "history_limit": 207,
            "signal_structure_low": str(zone.range_low),
            "signal_structure_high": str(zone.range_high),
        },
    }
    assert private_runtime._r1_signal_validity(payload, "INJUSDT") == (
        False, "R1_EXACT_ENTRY_LEVEL_CHANGED"
    )


def test_cancel_intent_is_committed_before_exchange_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeConnection:
        def execute(self, sql: str, *args: object) -> None:
            assert "INSERT INTO runtime.entry_cancel_intents" in sql
            events.append("persist_intent")

        def commit(self) -> None:
            events.append("commit_intent")

    def fake_post(*args: object, **kwargs: object) -> dict[str, object]:
        assert events == ["persist_intent", "commit_intent"]
        events.append("exchange_cancel")
        return {"retCode": 0}

    monkeypatch.setattr(private_runtime, "api_post", fake_post)
    monkeypatch.setattr(
        private_runtime, "reconcile",
        lambda *_args, **_kwargs: events.append("reconcile")
    )
    monkeypatch.setattr(
        private_runtime,
        "resolve_cancelled_entry_reservation_after_reconcile",
        lambda *_args, **_kwargs: "RELEASED",
    )
    private_runtime._cancel_entry_limit(
        FakeConnection(),  # type: ignore[arg-type]
        "unused-key", "unused-secret",
        order_id="test-order", symbol="INJUSDT",
        command_id="ue-test", reason="R1_EXACT_ENTRY_LEVEL_CHANGED",
    )
    assert events == [
        "persist_intent",
        "commit_intent",
        "exchange_cancel",
        "reconcile",
        "commit_intent",
    ]
