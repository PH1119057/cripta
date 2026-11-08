"""Actual mirror -> strict coverage -> MFE -> Exit: delayed first-fill visibility."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_p90l90_real_live_source_ports import EPOCH, NOW, _ports, _real_mirror
from test_p90l90_real_mfe import _MfeDB
from test_p90l90_real_observation import AT, _scope

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.contracts import ExitEvaluationStatus
from bybit_workbench.universal_exit.p90l90_real_exit_port import P90RealExitNativePort
from bybit_workbench.universal_exit.p90l90_real_mfe_ledger import P90RealMfeLedger
from operations.monitoring import p90l90_real_live_source_ports as module


class SourceDb(_MfeDB):
    def __init__(self):
        super().__init__()
        self.locked = True  # Source reads precede the ledger's per-position lock.

    def execute(self, sql, params=()):
        if "p90l90_real_source_epochs" in sql:
            return SimpleNamespace(fetchone=lambda: (EPOCH,))
        return super().execute(sql, params)


def _append(mirror, symbol, number, price):
    at = AT + timedelta(milliseconds=number)
    mirror.record_message({"topic": f"publicTrade.{symbol}", "data": [{
        "s": symbol, "i": f"original-{number}", "seq": number,
        "T": int(at.timestamp()*1000), "p": price, "v": ".1", "S": "Buy",
    }]}, received_at=at)
    return TradeCursor(symbol=symbol, seq=number, exec_id=f"original-{number}", traded_at=at)


def _fact(cursor):
    return MarketFactEnvelope(
        fact_id="trade-" + cursor.exec_id, event_kind="PUBLIC_TRADE",
        symbol=cursor.symbol, event_at=cursor.traded_at, observed_at=cursor.traded_at,
        received_at=AT+timedelta(milliseconds=9),
        source_refs=(f"bybit:publicTrade:{cursor.symbol}:{cursor.exec_id}",),
        attributes=FrozenPolicy.from_mapping({"price": "100.2"}),
    )


@pytest.mark.parametrize("pending", [False, True])
def test_delayed_visibility_preserves_all_original_ticks_and_pending_is_not_epoch_error(
    monkeypatch, pending,
):
    position, plan, _, _, private, _ = _scope()
    private = replace(private, observed_at=AT+timedelta(milliseconds=8))
    db = SourceDb()
    mirror = _real_mirror()
    _append(mirror, position.symbol, 1, "100.1")
    _append(mirror, position.symbol, 2, "100.4")  # Must retain favorable excursion.
    cursor = _append(mirror, position.symbol, 3, "100.2")
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _: NOW))
    source = _ports(db, mirror)
    # Baseline rejects the first newly visible tick even though mirror has all ticks.
    with pytest.raises(module.P90RealLiveSourceUnverified, match="FIRST_POSTFILL_TRADE_SKIPPED"):
        source.native_coverage(position, cursor)
    records, commands = [], []
    state = {"pending": pending}

    def signed(*_):
        assert not state["pending"], "no private proof may be fabricated while pending"
        return private

    port = P90RealExitNativePort(
        position_provider=lambda _: ((position, plan),),
        independently_verified_source_coverage=source.native_coverage,
        independently_signed_private_evidence=signed,
        source_commit="a"*40, mfe_ledger=P90RealMfeLedger(db),
        exit_store=SimpleNamespace(record=lambda evaluation, **_: records.append(evaluation)),
        verified_execution_port=lambda *args: commands.append(args),
        verified_native_prefix=source.native_prefix,
        verified_historical_prefix_coverage=source.native_historical_coverage,
        private_state_pending=lambda _: state["pending"],
    )
    result = port(_fact(cursor), cursor)[0]
    assert len(db.saved) == 3
    latest = max(db.saved.values(), key=lambda row: row["event_at"])
    assert Decimal(latest["attributes"]["checkpoint"]["best_favorable_price"]) == Decimal("100.4")
    assert {row["source_refs"][0] for row in db.saved.values()} == {
        f"bybit:publicTrade:{position.symbol}:original-{i}" for i in (1, 2, 3)
    }
    assert len(commands) == (0 if pending else 1)
    assert result.status is (ExitEvaluationStatus.BLOCKED if pending
                            else ExitEvaluationStatus.DECISION_CREATED)
    if pending:
        state["pending"] = False
        cursor = _append(mirror, position.symbol, 4, "100.2")
        result = port(_fact(cursor), cursor)[0]
        assert result.status is ExitEvaluationStatus.DECISION_CREATED
        assert len(commands) == 1
