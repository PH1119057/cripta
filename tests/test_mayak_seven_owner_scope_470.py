"""Issue #470: owner-approved seven-symbol MAYAK only; native stays five.

Pure tests. No public network, production DB, service restart or LIVE action.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.mayak.core.live import LiveMayakEngine
from operations.monitoring import mayak_v2

SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
REMOVED = {
    "AAVEUSDT", "ADAUSDT", "AVAXUSDT", "BCHUSDT", "BNBUSDT",
    "HBARUSDT", "LINKUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT",
    "TRXUSDT", "UNIUSDT", "XRPUSDT",
}


def test_default_mayak_seven_keeps_btc_eth_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAYAK_V2_SYMBOLS", raising=False)
    source = mayak_v2.Collector()
    assert mayak_v2.DEFAULT_SYMBOLS == SEVEN
    assert source.symbols == SEVEN
    assert source.engine.symbols == SEVEN
    assert set(source.symbols).isdisjoint(REMOVED)
    snapshot = source.engine.snapshot(datetime(2026, 11, 11, 10, 0, tzinfo=UTC))
    assert set(snapshot["coins"]) == set(SEVEN)
    assert snapshot["btc"] == snapshot["coins"]["BTCUSDT"]
    assert snapshot["eth"] == snapshot["coins"]["ETHUSDT"]
    assert snapshot["config_fingerprint"] == (
        source.engine.configuration_fingerprint()
    )
    assert snapshot["derivatives_coverage"]["total"] == len(SEVEN)


def test_subscriptions_are_exact_scope_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAYAK_V2_SYMBOLS", raising=False)
    source = mayak_v2.Collector()
    spot = source._subscription_topics("spot")
    linear = source._subscription_topics("linear")
    assert len(spot) == 14 and len(set(spot)) == len(spot)
    assert len(linear) == 28 and len(set(linear)) == len(linear)
    assert set(spot) == {
        topic for s in SEVEN
        for topic in (f"publicTrade.{s}", f"orderbook.50.{s}")
    }
    assert set(linear) == set(spot) | {
        topic for s in SEVEN
        for topic in (f"tickers.{s}", f"allLiquidation.{s}")
    }
    assert mayak_v2.SUBSCRIBE_BATCH_LIMIT["spot"] == 10
    assert mayak_v2.SUBSCRIBE_BATCH_LIMIT["linear"] == 30
    assert all(
        not any(topic.endswith("." + removed) for removed in REMOVED)
        for topic in spot + linear
    )
    with pytest.raises(ValueError, match="MAYAK_SOURCE_MARKET_INVALID"):
        source._subscription_topics("private")


@pytest.mark.parametrize(
    "configured",
    (
        "",
        ",".join(SEVEN[:5]),
        ",".join((*SEVEN, "TRXUSDT")),
        ",".join((*SEVEN, "ETHUSDT")),
        ",".join((*SEVEN[:4], SEVEN[5:])),
        ",".join(sorted(SEVEN)),
        ",".join((
            "AAVEUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT",
            "BCHUSDT", "BNBUSDT", "DOTUSDT", "HBARUSDT", "INJUSDT",
            "LINKUSDT", "LTCUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT",
            "TRXUSDT", "UNIUSDT", "XRPUSDT", "BTCUSDT", "ETHUSDT",
        )),
    ),
)
def test_stale_or_unapproved_mayak_override_fails_before_network(
    monkeypatch: pytest.MonkeyPatch, configured: str,
) -> None:
    monkeypatch.setenv("MAYAK_V2_SYMBOLS", configured)
    with pytest.raises(ValueError, match="MAYAK_OWNER_APPROVED_SEVEN_SCOPE_INVALID"):
        mayak_v2.Collector()


def test_exact_seven_env_override_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAYAK_V2_SYMBOLS", ",".join(SEVEN))
    assert mayak_v2.Collector().symbols == SEVEN


def test_mayak_universe_change_cannot_replay_old_dispatcher_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAYAK_V2_SYMBOLS", raising=False)
    historical = (
        "AAVEUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT",
        "BCHUSDT", "BNBUSDT", "DOTUSDT", "HBARUSDT", "INJUSDT",
        "LINKUSDT", "LTCUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT",
        "TRXUSDT", "UNIUSDT", "XRPUSDT", "BTCUSDT", "ETHUSDT",
    )
    previous = LiveMayakEngine(historical).configuration_fingerprint()
    current = LiveMayakEngine(SEVEN).configuration_fingerprint()
    assert previous != current
    latest_minute = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    old_handoff = {"panel": "historical-20", "config_fingerprint": previous}
    state = {"fingerprint": previous}
    observed_params: list[object] = []

    class Result:
        def __init__(self, one=None, many=None):
            self.one, self.many = one, many

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.many

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params=()):
            if "j.config_fingerprint" in query:
                return Result((
                    latest_minute, "OLD_GLOBAL_STATE",
                    old_handoff, state["fingerprint"],
                ))
            if "min(regular_minute)" in query:
                return Result((latest_minute, latest_minute, 1))
            if "coalesce(max(extract" in query:
                return Result((0,))
            if "FROM mayak_v2.liquidations" in query:
                observed_params.append(params)
                return Result(many=[])
            raise AssertionError(f"unexpected SELECT {query[:70]}")

    monkeypatch.setattr(mayak_v2.psycopg, "connect", lambda *_a, **_k: DB())
    collector = mayak_v2.Collector()
    collector._load_persistence_checkpoint()
    assert collector.last_persisted_minute == latest_minute
    assert collector.last_persisted_handoff is None
    assert collector.previous_state is None
    assert observed_params and observed_params[0][-1] == list(SEVEN)

    # Existing same-panel records still have valid causal restart lineage.
    state["fingerprint"] = current
    compatible = mayak_v2.Collector()
    compatible._load_persistence_checkpoint()
    assert compatible.last_persisted_handoff == old_handoff
    assert compatible.previous_state == "OLD_GLOBAL_STATE"
