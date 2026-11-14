"""#470 R10 stage 2: default-off private Execution uses ONE PG public source.

No Exchange or production DB use. Negative tests prove that when R10 is ON,
a failed MarketPublication read cannot fall back to Bybit public REST.
"""
from __future__ import annotations

import ast
import sys
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations/connectivity"))
import private_runtime as rt  # noqa: E402

from bybit_workbench.market_data.r10_execution_publication import (  # noqa: E402
    R10ExecutionPublicationUnavailable,
)
from bybit_workbench.universal_entry import p90l90_real_contract_v3 as p90  # noqa: E402

SYMBOL = "APTUSDT"


def _blocked_network(*_args, **_kwargs):
    raise AssertionError("R10 PG-only mode must never query Bybit public REST")


def _r1_payload(*, low: str = "90", high: str = "110") -> dict[str, object]:
    return {
        "side": "Buy",
        "entry_validity": {
            "operator": "R1_EXACT_SIGNAL",
            "signal_entry_price": "100",
            "signal_target_price": "105",
            "history_limit": 207,
            "signal_structure_low": low,
            "signal_structure_high": high,
        },
    }


def _pg_snapshot(last: str = "101", low: str = "90", high: str = "110"):
    return SimpleNamespace(
        quote=SimpleNamespace(last_price=Decimal(last)),
        closed_5m=("real-bar-from-publication",),
        history_limit=207,
        publication_id=27,
        source_session_id="a" * 32,
    )


def test_runtime_market_publication_feature_is_explicitly_default_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CRIPTA_R10_EXECUTION_PUBLICATION", raising=False)
    assert rt._r10_pg_publication_enabled() is False
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "1")
    assert rt._r10_pg_publication_enabled() is True
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "0")
    assert rt._r10_pg_publication_enabled() is False
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "ENABLED")
    with pytest.raises(
        R10ExecutionPublicationUnavailable, match="FLAG_INVALID",
    ):
        rt._r10_pg_publication_enabled()


class SourceConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.events: list[str] = []

    def __enter__(self):
        self.events.append("open")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.events.append("closed")

    def transaction(self):
        self.events.append("transaction")
        return nullcontext()

    def execute(self, sql):
        self.statements.append(sql)
        assert sql == (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )


def test_private_execution_pg_port_pins_one_read_only_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = SourceConnection()
    monkeypatch.setattr(rt, "db", lambda name: (
        c if name.startswith("private-execution-r10-") else _blocked_network()
    ))
    q = SimpleNamespace(
        last_price=Decimal("101"),
        bid_price=Decimal("100"),
        ask_price=Decimal("102"),
    )
    monkeypatch.setattr(
        rt, "read_r10_execution_quote",
        lambda connection, *, symbol, observed_at: (
            q if connection is c and symbol == SYMBOL
            and isinstance(observed_at, datetime)
            else _blocked_network()
        ),
    )
    monkeypatch.setattr(
        rt, "read_r10_exact_r1_causal_snapshot",
        lambda connection, *, symbol, observed_at, history_limit: (
            _pg_snapshot() if connection is c and symbol == SYMBOL
            and history_limit == 207
            else _blocked_network()
        ),
    )
    assert rt._r10_execution_quote(SYMBOL) is q
    assert rt._r10_exact_r1_snapshot(SYMBOL, history_limit=207).publication_id == 27
    assert c.events == [
        "open", "transaction", "closed",
        "open", "transaction", "closed",
    ]
    assert len(c.statements) == 2


@pytest.mark.parametrize(
    "side,expected",
    [("Buy", Decimal("100")), ("Sell", Decimal("102"))],
)
def test_execution_side_price_from_pg_has_no_rest_fallback(
    monkeypatch: pytest.MonkeyPatch, side: str, expected: Decimal,
) -> None:
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "1")
    monkeypatch.setattr(rt, "api_get", _blocked_network)
    monkeypatch.setattr(rt, "_r10_execution_quote", lambda _symbol: (
        SimpleNamespace(
            last_price=Decimal("101"), bid_price=Decimal("100"),
            ask_price=Decimal("102"),
        )
    ))
    assert rt.executable_close_price(SYMBOL, side) == expected


def test_execution_price_read_failure_never_uses_market_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "1")
    monkeypatch.setattr(rt, "api_get", _blocked_network)

    def no_source(_symbol):
        raise R10ExecutionPublicationUnavailable("SOURCE_GAP")

    monkeypatch.setattr(rt, "_r10_execution_quote", no_source)
    with pytest.raises(R10ExecutionPublicationUnavailable, match="SOURCE_GAP"):
        rt.executable_close_price(SYMBOL, "Buy")


@pytest.mark.parametrize(
    "last,low,high,expected",
    [
        ("101", "90", "110", (True, "R1_SIGNAL_STILL_VALID")),
        ("105", "90", "110", (
            False, "R1_OPPOSITE_TARGET_REACHED_BEFORE_FILL",
        )),
        ("101", "90", "111", (False, "R1_EXACT_ENTRY_LEVEL_CHANGED")),
    ],
)
def test_r1_on_uses_original_pg_snapshot_not_http(
    monkeypatch: pytest.MonkeyPatch,
    last: str, low: str, high: str, expected,
) -> None:
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "1")
    monkeypatch.setattr(rt, "api_get", _blocked_network)
    monkeypatch.setattr(rt.urllib.request, "urlopen", _blocked_network)
    monkeypatch.setattr(
        rt, "_r10_exact_r1_snapshot",
        lambda symbol, *, history_limit: (
            _pg_snapshot(last=last, low=low, high=high)
            if symbol == SYMBOL and history_limit == 207
            else _blocked_network()
        ),
    )
    monkeypatch.setattr(
        rt, "compute_r1_l53_stable_zone",
        lambda _bars: SimpleNamespace(
            range_low=Decimal(low), range_high=Decimal(high),
        ),
    )
    assert rt._r1_signal_validity(_r1_payload(), SYMBOL) == expected


def test_r1_unverified_pg_snapshot_defers_and_never_cancels_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRIPTA_R10_EXECUTION_PUBLICATION", "1")
    monkeypatch.setattr(rt, "api_get", _blocked_network)
    monkeypatch.setattr(rt.urllib.request, "urlopen", _blocked_network)

    def missing(*_args, **_kwargs):
        raise R10ExecutionPublicationUnavailable("R10_SOURCE_GAP")

    monkeypatch.setattr(rt, "_r10_exact_r1_snapshot", missing)
    with pytest.raises(rt.EntryValidityReadUnavailable, match="source unavailable"):
        rt._r1_signal_validity(_r1_payload(), SYMBOL)


@pytest.mark.parametrize(
    "side,stop,target,last,accepted",
    [
        ("Buy", "95", "110", "100", True),
        ("Sell", "110", "95", "100", True),
        ("Buy", "95", "110", "110", False),
        ("Sell", "110", "95", "95", False),
        ("Buy", "95", "110", "94", False),
        ("Sell", "110", "95", "111", False),
    ],
)
def test_p90_original_publication_price_has_exact_tp_sl_semantics(
    side: str, stop: str, target: str, last: str, accepted: bool,
) -> None:
    args = {
        "symbol": SYMBOL,
        "side": side,
        "instrument_tick": Decimal("0.1"),
        "stop_loss": Decimal(stop),
        "take_profit": Decimal(target),
        "last_price": Decimal(last),
    }
    if accepted:
        assert (
            p90.validate_real_v3_attached_protection_at_original_price(**args)
            == Decimal(last)
        )
    else:
        with pytest.raises(ValueError, match="ALREADY_CROSSED"):
            p90.validate_real_v3_attached_protection_at_original_price(**args)


def test_r10_safety_route_only_occurs_under_opt_in_and_never_fakes_ticker() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text()
    tree = ast.parse(source)
    execution = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_r1_signal_validity"
    )
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_r10_exact_r1_snapshot"
        for n in ast.walk(execution)
    )
    assert "validate_real_v3_attached_protection_at_original_price(" in source
    assert "read_r10_execution_quote(" in source
    assert "REPEATABLE READ READ ONLY" in source
    assert "CRIPTA_R10_EXECUTION_PUBLICATION" in source
    assert "r10_active" in source
    assert "ticker_response=current_ticker" in source  # existing OFF path preserved
    assert "if _r10_pg_publication_enabled():" in source
