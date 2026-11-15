"""R09 #470: postfill continuity broker belongs to one Native MarketSource."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from operations.monitoring import native_public_trade_source as native
from operations.monitoring.p90l90_real_postfill_source_broker import (
    P90RealPostfillMarketSourceUnverified,
    checked_dynamic_request,
)


ROOT = Path(__file__).resolve().parents[1]


def _request(epoch: str = "p90-real-epoch-" + "a" * 40) -> dict[str, object]:
    return {
        "version": 1,
        "kind": "POSTFILL_SOURCE_WINDOW",
        "nonce": "b" * 32,
        "position_id": "position-r09",
        "trading_account_id": "ta-" + "c" * 32,
        "symbol": "APTUSDT",
        "source_epoch_id": epoch,
    }


def test_dynamic_broker_request_keeps_exact_requested_p90_epoch() -> None:
    values = checked_dynamic_request(_request())
    assert values == (
        "position-r09",
        "ta-" + "c" * 32,
        "APTUSDT",
        "b" * 32,
        "p90-real-epoch-" + "a" * 40,
    )
    with pytest.raises(
        P90RealPostfillMarketSourceUnverified,
        match="REQUEST_IDENTITY_INVALID",
    ):
        checked_dynamic_request(_request("wrong-epoch"))


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_recent_trade_gap_proof_is_source_owned_and_bounded(monkeypatch) -> None:
    at = datetime(2026, 11, 15, 2, 30, tzinfo=UTC)
    payload = {
        "retCode": 0,
        "time": int(at.timestamp() * 1000),
        "result": {
            "list": [{
                "symbol": "APTUSDT",
                "execId": "native-1",
                "side": "Buy",
                "seq": "7",
                "price": "1.2",
                "size": "3",
                "time": str(int(at.timestamp() * 1000)),
            }],
        },
    }
    seen: list[tuple[str, float]] = []

    def urlopen(request, timeout: float):
        seen.append((request.full_url, timeout))
        return _Response(payload)

    monkeypatch.setattr(native.urllib.request, "urlopen", urlopen)
    rows, server_at = native._fetch_recent_trade_snapshot("APTUSDT")
    assert rows == payload["result"]["list"]
    assert server_at == at
    assert seen and "/v5/market/recent-trade?" in seen[0][0]
    assert seen[0][1] == 2.0


def test_mirror_fault_blocks_r09_without_stopping_source_capture() -> None:
    mirror = PublicTradeMirrorBuffer(("APTUSDT",), retention_seconds=60)
    mirror.start_epoch(datetime.now(UTC))
    native._record_r09_postfill_message(
        mirror,
        {
            "topic": "publicTrade.APTUSDT",
            "data": [{"s": "APTUSDT"}],
        },
        received_at=datetime.now(UTC),
    )
    assert mirror.snapshot()["state"] == "GAP"


def test_native_source_is_the_only_active_r09_broker_owner() -> None:
    source = (
        ROOT / "operations" / "monitoring" / "native_public_trade_source.py"
    ).read_text(encoding="utf-8")
    union = (
        ROOT / "operations" / "monitoring"
        / "public_market_source_union_capture.py"
    ).read_text(encoding="utf-8")
    observer = (
        ROOT / "operations" / "monitoring" / "universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    unit = (
        ROOT / "operations" / "systemd"
        / "cripta-native-public-trade-source.service"
    ).read_text(encoding="utf-8")

    assert "CRIPTA_R09_SOURCE_POSTFILL_BROKER=ENABLED" in unit
    assert "native-market-source-r09-postfill-broker" in source
    assert "resolve_current_source" in source
    assert "_fetch_recent_trade_snapshot" in source

    simple_persist = source.index("_persist_frame(", source.index("def _run_stream("))
    simple_mirror = source.index(
        "_record_r09_postfill_message(", simple_persist,
    )
    assert simple_persist < simple_mirror

    union_persist = union.index(
        "native._persist_frame(", union.index("def run_source_union("),
    )
    union_mirror = union.index(
        "native._record_r09_postfill_message(", union_persist,
    )
    assert union_persist < union_mirror

    pg_only = observer.index('if r06_mode == "ENABLED":')
    old_multi = observer.index("_run_multi_strategy_observer()", pg_only)
    assert "run_pg_only(" in observer[pg_only:old_multi]
    assert "return" in observer[pg_only:old_multi]
