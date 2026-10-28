"""Signed MAIN/SUB Exit producer: provider, registry and inventory rejection."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_p90l90_real_observation import _observation, _scope

from bybit_workbench.universal_exit.p90l90_real_signed_position import (
    P90RealSignedPositionReader,
    P90RealSignedPositionUnverified,
)

AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
D = Decimal


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _DB:
    def __init__(self, *, sub: bool = False) -> None:
        self.account = "ta-sub" if sub else "ta-main"
        self.connector = "connector-sub" if sub else "connector-main"
        self.kind = "SUB" if sub else "MAIN"
        self.uid = "12345679" if sub else "12345678"
        self.age = timedelta(milliseconds=1)
        self.wrong_connector = False

    def execute(self, statement: str, parameters=()) -> _Cursor:
        if "SELECT generation_id,state,started_at" in statement:
            return _Cursor([(
                "generation-proven", "COMPLETE",
                AT - self.age - timedelta(seconds=1),
                AT + timedelta(milliseconds=5) - self.age,
                "BYBIT:UNIFIED", "UNIFIED", D("30"), D("25"),
                D("20"), 1, 0, {}, None, self.account, "PROVEN",
            )])
        if "SELECT account_connector_id FROM runtime.account_state_generations" in statement:
            if self.wrong_connector:
                return _Cursor([])
            return _Cursor([(self.connector,)])
        if "FROM identity.trading_accounts" in statement:
            return _Cursor([
                ("ta-main", "venue-bybit", "12345678", None, "MAIN",
                 "UNIFIED", "MAINNET", "ACTIVE"),
                ("ta-sub", "venue-bybit", "12345679", "ta-main", "SUB",
                 "UNIFIED", "MAINNET", "ACTIVE"),
            ])
        if "FROM identity.account_connectors" in statement:
            return _Cursor([(
                self.connector, self.account, "CEX_PRIVATE_RUNTIME",
                "BYBIT_V5_PRIVATE", "mount-ref", "ACTIVE", None,
            )])
        raise AssertionError(f"unknown read-only query: {statement[:65]}")


class _Bybit:
    def __init__(self, *, sub: bool = False) -> None:
        self.sub = sub
        self.calls: list[str] = []
        self.overrides: dict[str, dict[str, object]] = {}
        self.symbol = "DOTUSDT" if sub else "LTCUSDT"
        self.position: dict[str, object] = {
            "symbol": self.symbol, "side": "Buy", "positionIdx": 0,
            "avgPrice": "100", "size": "0.1",
            "stopLoss": "90", "takeProfit": "110", "markPrice": "101",
        }

    def get(self, endpoint: str, params: dict[str, str]) -> dict[str, object]:
        self.calls.append(endpoint)
        if endpoint in self.overrides:
            return self.overrides[endpoint]
        if endpoint == "/v5/user/query-api":
            return {"retCode": 0, "result": {
                "userID": "12345679" if self.sub else "12345678",
                "parentUid": "12345678" if self.sub else "0",
                "isMaster": not self.sub, "uta": 1,
            }}
        if endpoint == "/v5/position/list":
            assert params == {"category": "linear", "symbol": self.symbol}
            return {"retCode": 0, "result": {"list": [self.position]}}
        if endpoint == "/v5/market/instruments-info":
            assert params == {"category": "linear", "symbol": self.symbol}
            return {"retCode": 0, "result": {"list": [{
                "symbol": self.symbol, "status": "Trading",
                "quoteCoin": "USDT", "settleCoin": "USDT",
                "priceFilter": {"tickSize": "0.01"},
            }]}}
        raise AssertionError("unknown endpoint")


def _setup(*, sub: bool = False):
    position, _, _, cursor, _, _ = _scope(sub=sub)
    database = _DB(sub=sub)
    api = _Bybit(sub=sub)
    now = AT + timedelta(milliseconds=5)
    proof = {"ws": True, "pending_clear": True}

    def reader():
        return P90RealSignedPositionReader(
            database, trading_account_id=database.account,
            account_connector_id=database.connector,
            mounted_credential_ref="mount-ref", signed_get=api.get,
            verified_private_ws_continuity=lambda _position: proof["ws"],
            no_pending_exit_command=lambda _position: proof["pending_clear"],
            clock=lambda: now,
        )

    return position, cursor, database, api, proof, reader


@pytest.mark.parametrize("sub", [False, True])
def test_main_sub_signed_uid_and_exact_exchange_position(sub: bool) -> None:
    position, cursor, _, api, _, reader = _setup(sub=sub)
    private = reader()(position, cursor)
    assert private.signed_provider_uid == ("12345679" if sub else "12345678")
    assert private.trading_account_id == position.trading_account_id
    assert private.strategy_execution_binding_id == position.strategy_execution_binding_id
    assert private.position == api.position
    assert private.tick_size == D("0.01")
    assert api.calls == [
        "/v5/user/query-api", "/v5/position/list",
        "/v5/market/instruments-info",
    ]


@pytest.mark.parametrize(("which", "why"), [
    ("ws", "PRIVATE_WS_CONTINUITY"), ("pending_clear", "PENDING_EXIT_COMMAND"),
])
def test_unproved_ws_or_pending_commands_short_circuit_before_signed_reads(
    which: str, why: str,
) -> None:
    position, cursor, _, api, proof, reader = _setup()
    proof[which] = False
    with pytest.raises(P90RealSignedPositionUnverified, match=why):
        reader()(position, cursor)
    assert api.calls == []


def test_stale_generation_and_wrong_connector_fail_before_signed_transport() -> None:
    position, cursor, db, api, _, reader = _setup()
    db.age = timedelta(seconds=16)
    with pytest.raises(P90RealSignedPositionUnverified, match="GENERATION_STALE"):
        reader()(position, cursor)
    db.age = timedelta(seconds=1)
    db.wrong_connector = True
    with pytest.raises(P90RealSignedPositionUnverified, match="CONNECTOR_MISMATCH"):
        reader()(position, cursor)
    assert api.calls == []


def test_cross_account_binding_denied_before_provider_calls() -> None:
    position, cursor, _, api, _, reader = _setup()
    with pytest.raises(P90RealSignedPositionUnverified, match="LINEAGE"):
        reader()(replace(position, trading_account_id="ta-other"), cursor)
    assert api.calls == []


def test_signed_wrong_uid_is_never_accepted_as_exchange_position_source() -> None:
    position, cursor, _, api, _, reader = _setup()
    api.overrides["/v5/user/query-api"] = {"retCode": 0, "result": {
        "userID": "999", "parentUid": "0", "isMaster": True, "uta": 1,
    }}
    with pytest.raises(P90RealSignedPositionUnverified, match="UID_UNVERIFIED"):
        reader()(position, cursor)
    assert api.calls == ["/v5/user/query-api"]


@pytest.mark.parametrize(("changed", "value", "reason"), [
    ("side", "Sell", "FILL_OR_SIDE_MISMATCH"),
    ("size", "0.15", "FILL_OR_SIDE_MISMATCH"),
    ("avgPrice", "99", "FILL_OR_SIDE_MISMATCH"),
    ("stopLoss", "0", "PROTECTION_UNEXECUTABLE"),
    ("takeProfit", "0", "PROTECTION_UNEXECUTABLE"),
    ("takeProfit", "98", "PROTECTION_UNEXECUTABLE"),
    ("markPrice", "89", "PROTECTION_UNEXECUTABLE"),
])
def test_signed_position_or_exchange_protection_mismatch(
    changed: str, value: str, reason: str,
) -> None:
    position, cursor, _, api, _, reader = _setup()
    api.position[changed] = value
    with pytest.raises(P90RealSignedPositionUnverified, match=reason):
        reader()(position, cursor)


def test_duplicate_open_position_or_partial_inventory_is_denied() -> None:
    position, cursor, _, api, _, reader = _setup()
    api.overrides["/v5/position/list"] = {"retCode": 0, "result": {
        "list": [api.position, {**api.position, "positionIdx": 1}],
    }}
    with pytest.raises(P90RealSignedPositionUnverified, match="EXACTLY_ONE"):
        reader()(position, cursor)
    api.overrides["/v5/position/list"] = {"retCode": 0, "result": {}}
    with pytest.raises(P90RealSignedPositionUnverified, match="LIST_INCOMPLETE"):
        reader()(position, cursor)


def test_signed_get_rejection_and_instrument_mismatch_fail_closed() -> None:
    position, cursor, _, api, _, reader = _setup()
    api.overrides["/v5/position/list"] = {"retCode": 10001, "result": {"list": []}}
    with pytest.raises(P90RealSignedPositionUnverified, match="POSITION_UNAVAILABLE"):
        reader()(position, cursor)
    api.overrides.pop("/v5/position/list")
    api.overrides["/v5/market/instruments-info"] = {"retCode": 0, "result": {
        "list": [{"symbol": "BTCUSDT"}],
    }}
    with pytest.raises(P90RealSignedPositionUnverified, match="INSTRUMENT_IDENTITY"):
        reader()(position, cursor)


def test_signed_adapter_compiles_into_existing_real_exit_observation() -> None:
    position, _, checkpoint, cursor, _, price = _scope()
    _, _, _, _, _, reader = _setup()
    signed = reader()(position, cursor)
    result = _observation(position, checkpoint, cursor, signed, price)
    detail = result.attributes.to_dict()["p90l90_real"]
    assert detail["protection_exchange_confirmed"] is True
    assert detail["trading_account_id"] == "ta-main"
    assert detail["exchange_stop_price"] == "90"


def test_exit_observation_rejects_signed_protection_older_than_15_seconds() -> None:
    position, _, checkpoint, cursor, _, price = _scope()
    _, _, _, _, _, reader = _setup()
    signed = reader()(position, cursor)
    from bybit_workbench.universal_exit.p90l90_real_observation import (
        P90RealObservationUnverified,
        build_real_native_exit_observation,
    )
    with pytest.raises(P90RealObservationUnverified, match="NATIVE_MFE_OR_TIME"):
        build_real_native_exit_observation(
            position=position, checkpoint=checkpoint, cursor=cursor,
            price=price, native_fact_id="native-fact-101",
            native_source_refs=("bybit:publicTrade:exact:101",),
            private=signed, received_at=AT + timedelta(seconds=20),
        )


def test_signed_account_reader_supports_closed_five_minute_exit_owner() -> None:
    position, cursor, _, api, _, reader = _setup()
    closed_at = cursor.traded_at
    private = reader().for_closed_boundary(position, closed_at)
    assert private.symbol == position.symbol
    assert private.trading_account_id == position.trading_account_id
    assert api.calls == [
        "/v5/user/query-api", "/v5/position/list",
        "/v5/market/instruments-info",
    ]


def test_closed_bar_from_future_or_before_fill_cannot_request_signed_inventory() -> None:
    position, cursor, _, api, _, reader = _setup()
    for closed_at in (
        AT - timedelta(milliseconds=1),
        cursor.traded_at + timedelta(seconds=1),
    ):
        with pytest.raises(P90RealSignedPositionUnverified, match="LINEAGE"):
            reader().for_closed_boundary(position, closed_at)
    assert api.calls == []


def test_recent_signed_generation_before_actual_fill_is_not_current_protection() -> None:
    position, cursor, db, api, _, reader = _setup()
    # Still less than 15 seconds old, but older than the actual fill.
    db.age = timedelta(milliseconds=10)
    with pytest.raises(P90RealSignedPositionUnverified, match="GENERATION_STALE"):
        reader()(position, cursor)
    assert api.calls == []
