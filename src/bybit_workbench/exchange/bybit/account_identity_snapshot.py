"""Normalize one authenticated Bybit V5 API-key-info GET to a minimal account snapshot.

This decoder never reads credentials or performs network I/O. Its caller must
prove that the exact connector made a fresh signed GET /v5/user/query-api,
and separately establish mounted credential provenance. The returned snapshot
is only an input for registered_uid_match, NOT an identity attestation or
permission to place orders. Never persist/log the original Bybit response:
it contains API key details and other operationally sensitive data.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime


def _decimal_uid(value: object, *, allow_zero: bool = False) -> str:
    if type(value) not in (int, str):
        return ""
    raw = str(value)
    if not raw or not raw.isascii() or not raw.isdecimal():
        return ""
    numeric = int(raw)
    if numeric < 0 or (numeric == 0 and not allow_zero):
        return ""
    return str(numeric)


def normalize_authenticated_api_key_info(
    payload: Mapping[str, object] | None,
    *,
    observed_at: datetime,
    signed_get_proven: bool,
) -> dict[str, object]:
    """Fail closed on missing/invalid account identity and return only safe fields.

    signed_get_proven must be established by a separately authorized transport;
    merely passing True cannot independently authenticate a connector or UID.
    """
    if signed_get_proven is not True:
        raise ValueError("BYBIT_SIGNED_ACCOUNT_GET_NOT_PROVEN")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("ACCOUNT_OBSERVATION_TIME_NOT_AWARE")
    if not isinstance(payload, Mapping):
        raise ValueError("BYBIT_API_KEY_INFO_UNAVAILABLE")
    if type(payload.get("retCode")) is not int or payload["retCode"] != 0:
        raise ValueError("BYBIT_API_KEY_INFO_REJECTED")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("BYBIT_API_KEY_INFO_RESULT_MISSING")
    user_id = _decimal_uid(result.get("userID"))
    parent_uid = _decimal_uid(result.get("parentUid"), allow_zero=True)
    is_master = result.get("isMaster")
    uta = result.get("uta")
    if not user_id or not parent_uid or type(is_master) is not bool:
        raise ValueError("BYBIT_API_KEY_INFO_IDENTITY_INVALID")
    if type(uta) is not int or uta != 1:
        raise ValueError("BYBIT_API_KEY_INFO_NOT_UNIFIED")
    if (is_master and parent_uid != "0") or (not is_master and parent_uid == "0"):
        raise ValueError("BYBIT_API_KEY_INFO_MAIN_SUB_MISMATCH")
    # Deliberate whitelist. Never pass through apiKey, permissions, IPs, KYC,
    # response metadata, or any other potentially sensitive provider fields.
    return {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "complete": True,
        "observed_at": observed_at.isoformat(),
        "account_user_id": user_id,
        "is_master": is_master,
        "parent_uid": parent_uid,
        "uta": uta,
    }
