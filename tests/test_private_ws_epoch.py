"""Private WS liveness is a current, account-scoped, ACK'd epoch."""
from __future__ import annotations

from copy import deepcopy

import pytest

from bybit_workbench.private_ws_epoch import verified_private_ws_epoch
from bybit_workbench.private_ws_subscriptions import PRIVATE_TOPICS


def _state() -> dict[str, object]:
    return {
        "identity": {
            "state": "READY", "trading_account_id": "ta-main",
            "account_connector_id": "connector-main",
        },
        "private": {
            "state": "connected",
            "subscriptions_topics": list(PRIVATE_TOPICS),
            "subscriptions_confirmed_at_epoch": 1980,
            "connected_at_epoch": 1981, "last_message_epoch": 1995,
            "reconnects": 1, "hot_positions": 0, "hot_orders": 0,
        },
    }


def _proven(state: dict[str, object], *, now: int = 2000) -> bool:
    return verified_private_ws_epoch(
        state, trading_account_id="ta-main",
        account_connector_id="connector-main", now_epoch=now,
    )


def test_current_live_private_ws_ack_and_authenticated_message() -> None:
    assert _proven(_state()) is True


@pytest.mark.parametrize(("field", "wrong"), [
    ("state", "reconnecting"),
    ("subscriptions_confirmed_at_epoch", None),
    ("connected_at_epoch", None),
    ("last_message_epoch", None),
    ("reconnects", -1),
    ("subscriptions_topics", ["wallet"]),
])
def test_missing_acks_reconnect_or_missing_private_heartbeat_fails_closed(
    field: str, wrong: object,
) -> None:
    state = _state()
    private = state["private"]
    assert isinstance(private, dict)
    private[field] = wrong
    assert not _proven(state)


def test_stale_heartbeat_and_old_status_file_are_not_ready() -> None:
    state = _state()
    assert _proven(state, now=2050) is False


def test_old_sub_connected_state_cannot_satisfy_main() -> None:
    state = _state()
    identity = state["identity"]
    assert isinstance(identity, dict)
    identity["trading_account_id"] = "ta-sub"
    assert not _proven(state)
    identity["trading_account_id"] = "ta-main"
    identity["account_connector_id"] = "connector-sub"
    assert not _proven(state)


def test_reconnect_discards_old_ack_even_with_recent_message() -> None:
    state = _state()
    private = state["private"]
    assert isinstance(private, dict)
    private["subscriptions_confirmed_at_epoch"] = 2000
    private["connected_at_epoch"] = 1990
    assert not _proven(state)


def test_negative_and_future_time_deny_liveness() -> None:
    state = _state()
    assert not _proven(state, now=-1)
    assert not _proven(state, now=1994)


def test_reconciliation_error_blocks_pending_real_exit_input() -> None:
    state = _state()
    private = state["private"]
    assert isinstance(private, dict)
    private["reconciliation_error"] = "ExchangeReadUnavailable"
    assert not _proven(state)
    restored = deepcopy(state)
    restored_private = restored["private"]
    assert isinstance(restored_private, dict)
    restored_private.pop("reconciliation_error")
    assert _proven(restored)


def test_no_synthetic_broader_idle_window() -> None:
    assert not verified_private_ws_epoch(
        _state(), trading_account_id="ta-main",
        account_connector_id="connector-main", now_epoch=2000,
        max_message_age_seconds=600,
    )
