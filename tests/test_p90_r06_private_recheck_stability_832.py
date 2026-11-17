from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from operations.connectivity import p90_r06_exit_owner as owner

ACCOUNT = "ta-" + "8" * 32
CONNECTOR = "ac-" + "c" * 32
STRATEGY = "p90l90_legacy_sub_dot_arb_apt"
UID = "123456789"
AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _raw(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "symbol": "ARBUSDT",
        "side": "Buy",
        "positionIdx": 0,
        "size": "76.7",
        "avgPrice": "0.13029",
        "stopLoss": "0.11727",
        "takeProfit": "0.1336",
        "trailingStop": "0",
        "markPrice": "0.13220",
        "unrealisedPnl": "0.146",
        "positionValue": "10.140",
    }
    value.update(overrides)
    return value


def _evidence(*, raw: dict[str, object], observed_at: datetime):
    return SimpleNamespace(
        trading_account_id=ACCOUNT,
        connector_id=CONNECTOR,
        signed_provider_uid=UID,
        strategy_execution_binding_id="binding-sub",
        exchange_position_key=f"BYBIT:{ACCOUNT}:LINEAR:USDT:ARBUSDT:0",
        symbol="ARBUSDT",
        position_idx=0,
        observed_at=observed_at,
        position=raw,
        tick_size=owner.Decimal("0.00001") if hasattr(owner, "Decimal") else None,
        pending_exit_mutation=False,
    )


def _safety(monkeypatch: pytest.MonkeyPatch, prior, again):
    monkeypatch.setattr(owner, "p90_real_exit_no_pending", lambda *_a, **_k: True)
    private = SimpleNamespace(for_closed_boundary=lambda *_a, **_k: again)
    routes = SimpleNamespace(
        main=SimpleNamespace(
            strategy_id="p90l90_legacy_main_ltc_inj",
            registered_exchange_uid="999",
            trading_account_id="ta-" + "1" * 32,
        ),
        sub=SimpleNamespace(
            strategy_id=STRATEGY,
            registered_exchange_uid=UID,
            trading_account_id=ACCOUNT,
        ),
    )
    return owner._MutationSafety(
        object(),
        routes=routes,
        private=private,
        first={"pos": prior},
        boundary=AT,
    )


def _position():
    return SimpleNamespace(
        strategy_position_id="pos",
        strategy_id=STRATEGY,
    )


def _evaluation():
    return SimpleNamespace(
        decision=SimpleNamespace(exit_decision_id="decision-1"),
    )


def test_private_recheck_accepts_only_volatile_market_telemetry_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _evidence(raw=_raw(), observed_at=AT)
    again = _evidence(
        raw=_raw(
            markPrice="0.13224",
            unrealisedPnl="0.149",
            positionValue="10.143",
        ),
        observed_at=AT + timedelta(milliseconds=400),
    )
    safety = _safety(monkeypatch, prior, again)

    assert safety(_position(), _evaluation(), AT + timedelta(seconds=1)) is True
    assert safety.last_denial_code is None


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("symbol", "DOTUSDT"),
        ("side", "Sell"),
        ("positionIdx", 1),
        ("size", "76.6"),
        ("avgPrice", "0.13030"),
        ("stopLoss", "0.11728"),
        ("takeProfit", "0.1337"),
        ("trailingStop", "0.00045"),
    ],
)
def test_private_recheck_still_rejects_identity_or_protection_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    changed: object,
) -> None:
    prior = _evidence(raw=_raw(), observed_at=AT)
    again = _evidence(
        raw=_raw(**{field: changed}),
        observed_at=AT + timedelta(milliseconds=400),
    )
    safety = _safety(monkeypatch, prior, again)

    assert safety(_position(), _evaluation(), AT + timedelta(seconds=1)) is False
    assert safety.last_denial_code == "P90_R06_EXIT_PRIVATE_RECHECK_DRIFT"


def test_private_recheck_rejects_missing_stable_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_raw = _raw()
    again_raw = _raw()
    again_raw.pop("stopLoss")
    prior = _evidence(raw=prior_raw, observed_at=AT)
    again = _evidence(raw=again_raw, observed_at=AT + timedelta(milliseconds=400))
    safety = _safety(monkeypatch, prior, again)

    assert safety(_position(), _evaluation(), AT + timedelta(seconds=1)) is False
    assert safety.last_denial_code == "P90_R06_EXIT_PRIVATE_RECHECK_DRIFT"
