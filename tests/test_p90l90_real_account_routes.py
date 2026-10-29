"""Exact MAIN/SUB P90 REAL account routing: database-only, never an arm."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    P90RealAccountRoutes,
    P90RealAccountRoutesUnverified,
    read_p90_real_account_routes,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)

AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
MAIN = "ta-" + "1" * 32
SUB = "ta-" + "2" * 32


def _bundle(strategy: str):
    card = SimpleNamespace(
        strategy_id=strategy,
        strategy_version=VERSION,
        strategy_config_fingerprint="fp-" + strategy,
        symbols=tuple(ALLOWED_SYMBOLS[strategy]),
    )
    active = SimpleNamespace(
        activation_id="act-" + strategy,
        strategy_id=strategy,
        strategy_version=VERSION,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True,
    )
    plan = SimpleNamespace(
        strategy_activation_id=active.activation_id,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        symbols=card.symbols,
    )
    return SimpleNamespace(card=card, activation=active, entry_plan=plan)


BUNDLES = (_bundle(MAIN_ID), _bundle(SUB_ID))


def _row(strategy: str) -> tuple[object, ...]:
    is_main = strategy == MAIN_ID
    return (
        "binding-main" if is_main else "binding-sub",
        MAIN if is_main else SUB,
        "PRIMARY", "PRIMARY", "DEDICATED_STRATEGY",
        "MAIN" if is_main else "SUB",
        "UNIFIED", "MAINNET", "venue-bybit", "ACTIVE",
        "101" if is_main else "202",
        "connector-main" if is_main else "connector-sub",
        "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE", "ACTIVE", True,
    )


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _DB:
    def __init__(self, rows=None):
        self.rows = rows or {
            BUNDLES[0].activation.activation_id: [_row(MAIN_ID)],
            BUNDLES[1].activation.activation_id: [_row(SUB_ID)],
        }
        self.calls = []

    def execute(self, sql, params=()):
        assert sql.lstrip().startswith("SELECT ")
        assert "strategy_entry.strategy_execution_bindings" in sql
        assert "LEFT JOIN identity.account_connectors" in sql
        assert "b.effective_from<=%s" in sql
        assert "credential_ref" in sql
        self.calls.append((sql, params))
        return _Result(self.rows.get(params[0], ()))


def test_both_independent_v3_routes_resolve_without_mutation() -> None:
    db = _DB()
    routes = read_p90_real_account_routes(db, bundles=BUNDLES, at=AT)
    assert isinstance(routes, P90RealAccountRoutes)
    assert routes.account_by_strategy == {MAIN_ID: MAIN, SUB_ID: SUB}
    assert routes.connector_by_strategy == {
        MAIN_ID: "connector-main", SUB_ID: "connector-sub",
    }
    assert routes.main.registered_exchange_uid != routes.sub.registered_exchange_uid
    assert len(db.calls) == 2
    assert all(call[1][1:] == (AT, AT) for call in db.calls)


@pytest.mark.parametrize("bad_field,new_value", (
    (1, "ta-" + "f" * 32),
    (2, "SECONDARY"),
    (3, "SECONDARY"),
    (4, "SHARED"),
    (5, "SUB"),
    (6, "CLASSIC"),
    (7, "TESTNET"),
    (8, "unknown-venue"),
    (9, "INACTIVE"),
    (10, "0"),
    (11, ""),
    (12, "UNVERIFIED"),
    (13, "WRONG_ADAPTER"),
    (14, "INACTIVE"),
    (15, False),
))
def test_wrong_main_binding_or_connector_is_rejected(
    bad_field: int, new_value: object,
) -> None:
    row = list(_row(MAIN_ID))
    row[bad_field] = new_value
    db = _DB({BUNDLES[0].activation.activation_id: [tuple(row)]})
    with pytest.raises(P90RealAccountRoutesUnverified):
        read_p90_real_account_routes(db, bundles=BUNDLES, at=AT)


def test_missing_and_duplicate_active_bindings_never_choose_first() -> None:
    for rows in ([], [_row(MAIN_ID), _row(MAIN_ID)]):
        db = _DB({BUNDLES[0].activation.activation_id: rows})
        with pytest.raises(P90RealAccountRoutesUnverified):
            read_p90_real_account_routes(db, bundles=BUNDLES, at=AT)


def test_main_sub_account_uid_connector_or_binding_collision_is_fatal() -> None:
    for field in (0, 1, 10, 11):
        sub = list(_row(SUB_ID))
        sub[field] = _row(MAIN_ID)[field]
        db = _DB({
            BUNDLES[0].activation.activation_id: [_row(MAIN_ID)],
            BUNDLES[1].activation.activation_id: [tuple(sub)],
        })
        with pytest.raises(P90RealAccountRoutesUnverified):
            read_p90_real_account_routes(db, bundles=BUNDLES, at=AT)


@pytest.mark.parametrize("wrong", ("wrong-version", "disabled", "wrong-plan", "not-two"))
def test_unregistered_v3_or_disabled_activation_cannot_supply_runtime_routes(
    wrong: str,
) -> None:
    main, sub = BUNDLES
    if wrong == "not-two":
        bundles = (main,)
    elif wrong == "wrong-version":
        bundles = (
            SimpleNamespace(card=replace_placeholder(main.card, strategy_version="v2"),
                            activation=main.activation, entry_plan=main.entry_plan),
            sub,
        )
    elif wrong == "disabled":
        bundles = (
            SimpleNamespace(card=main.card,
                            activation=replace_placeholder(main.activation, enabled=False),
                            entry_plan=main.entry_plan),
            sub,
        )
    else:
        bundles = (
            SimpleNamespace(card=main.card, activation=main.activation,
                            entry_plan=replace_placeholder(main.entry_plan,
                                                           strategy_activation_id="other")),
            sub,
        )
    with pytest.raises(P90RealAccountRoutesUnverified):
        read_p90_real_account_routes(_DB(), bundles=bundles, at=AT)


def replace_placeholder(source, **fields):
    return SimpleNamespace(**{**vars(source), **fields})


def test_invalid_or_naive_event_time_prevents_any_db_query() -> None:
    db = _DB()
    with pytest.raises(P90RealAccountRoutesUnverified, match="TIME_UNVERIFIED"):
        read_p90_real_account_routes(
            db, bundles=BUNDLES, at=AT.replace(tzinfo=None),
        )
    assert not db.calls
