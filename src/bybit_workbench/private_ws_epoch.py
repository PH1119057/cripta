"""Account-scoped, current in-process private WS epoch safety proof.

This is only a liveness/subscribe-ACK component, never signed REST UID proof,
a durable native cursor or an Exchange order permission. The caller must take
the status from its currently running, correctly credentialed private worker;
copying an old status.json file cannot establish present liveness.
"""
from __future__ import annotations

from collections.abc import Mapping

from bybit_workbench.private_ws_subscriptions import PRIVATE_TOPICS


def verified_private_ws_epoch(
    state: Mapping[str, object],
    *,
    trading_account_id: str,
    account_connector_id: str,
    now_epoch: int,
    max_message_age_seconds: int = 45,
) -> bool:
    """Fail closed unless this exact process has a fresh, ACK'd WS epoch."""
    if (
        not trading_account_id or not account_connector_id
        or type(now_epoch) is not int or now_epoch <= 0
        or type(max_message_age_seconds) is not int
        or not 0 < max_message_age_seconds <= 45
    ):
        return False
    identity = state.get("identity")
    private = state.get("private")
    if (
        not isinstance(identity, Mapping)
        or not isinstance(private, Mapping)
        or identity.get("state") != "READY"
        or identity.get("trading_account_id") != trading_account_id
        or identity.get("account_connector_id") != account_connector_id
        or private.get("state") != "connected"
        or private.get("reconciliation_error") not in (None, "")
    ):
        return False
    topics = private.get("subscriptions_topics")
    if (
        not isinstance(topics, (list, tuple))
        or len(topics) != len(PRIVATE_TOPICS)
        or any(not isinstance(topic, str) for topic in topics)
        or len(set(topics)) != len(PRIVATE_TOPICS)
        or set(topics) != set(PRIVATE_TOPICS)
    ):
        return False
    keys = (
        "subscriptions_confirmed_at_epoch",
        "connected_at_epoch",
        "last_verified_message_epoch",
        "reconnects",
    )
    if any(type(private.get(key)) is not int for key in keys):
        return False
    ack, connected, message, reconnects = (int(private[k]) for k in keys)
    if reconnects < 0 or not 0 < ack <= connected <= message <= now_epoch:
        return False
    return now_epoch - message <= max_message_age_seconds
