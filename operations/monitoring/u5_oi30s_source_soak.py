from __future__ import annotations

import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bybit_workbench.universal_entry.oi30s_source import (
    CurrentOiResponse,
    Oi30sConfig,
    Oi30sHealthTracker,
    OiSlotResult,
    OiSlotState,
    poll_current_oi_slot,
    slot_at,
)
from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

SOURCE_ROOT = Path(os.environ.get("CRIPTA_U5_OI30S_SOURCE_ROOT", "/srv/cripta/source_checkout"))
STATE_ROOT = Path(
    os.environ.get("CRIPTA_U5_OI30S_SOAK_STATE_ROOT", "/var/lib/cripta/u5_oi30s_source_soak")
)
PUBLIC_REST = os.environ.get("CRIPTA_U5_OI30S_PUBLIC_REST", "https://api.bybit.kz").rstrip("/")
FACT_SOURCE_ID = os.environ.get(
    "CRIPTA_U5_OI30S_FACT_SOURCE_ID", "BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1"
).strip()
LOADED_COMMIT = os.environ.get("CRIPTA_U5_OI30S_LOADED_COMMIT", "").strip()
SOAK_MINUTES = int(os.environ.get("CRIPTA_U5_OI30S_SOAK_MINUTES", "480"))
SLOT_SECONDS = int(os.environ.get("CRIPTA_U5_OI30S_SLOT_SECONDS", "30"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("CRIPTA_U5_OI30S_REQUEST_TIMEOUT_SECONDS", "5"))
RETRY_INTERVAL_SECONDS = float(os.environ.get("CRIPTA_U5_OI30S_RETRY_INTERVAL_SECONDS", "0.25"))
STATUS_PATH = STATE_ROOT / "status.json"
_HTTPS_CONTEXT = ssl.create_default_context()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(descriptor, (line + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fetch_current_oi(timeout: float) -> CurrentOiResponse:
    request_started_at = datetime.now(UTC)
    query = urllib.parse.urlencode({"category": "linear"})
    request = urllib.request.Request(
        f"{PUBLIC_REST}/v5/market/tickers?{query}",
        headers={"User-Agent": "Cripta-U5-OI30S-Soak/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_HTTPS_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    response_received_at = datetime.now(UTC)
    if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
        raise RuntimeError("public current-tickers request failed")
    server_ms = payload.get("time")
    if server_ms is None:
        raise RuntimeError("public current-tickers response lacks server time")
    exchange_server_at = datetime.fromtimestamp(int(str(server_ms)) / 1000, UTC)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("public current-tickers result is not an object")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise RuntimeError("public current-tickers result.list is not a list")
    values: dict[str, Decimal] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").upper()
        raw_oi = row.get("openInterest")
        if not symbol or raw_oi in (None, ""):
            continue
        try:
            value = Decimal(str(raw_oi))
        except InvalidOperation:
            continue
        if value.is_finite() and value > 0:
            values[symbol] = value
    return CurrentOiResponse(
        request_started_at=request_started_at,
        response_received_at=response_received_at,
        exchange_server_observed_at=exchange_server_at,
        open_interest=values,
        provenance="GET /v5/market/tickers?category=linear",
    )


def _slot_evidence(
    soak_id: str, result: OiSlotResult, health: Mapping[str, object]
) -> dict[str, object]:
    return {
        "event": "OI30S_SLOT",
        "soak_id": soak_id,
        "fact_source_id": result.fact_source_id,
        "slot_id": result.slot_id,
        "nominal_slot_at": result.nominal_slot_at.isoformat(),
        "slot_end_at": result.slot_end_at.isoformat(),
        "state": result.state.value,
        "attempts": result.attempts,
        "request_started_at": None
        if result.request_started_at is None
        else result.request_started_at.isoformat(),
        "response_received_at": None
        if result.response_received_at is None
        else result.response_received_at.isoformat(),
        "exchange_server_observed_at": None
        if result.exchange_server_observed_at is None
        else result.exchange_server_observed_at.isoformat(),
        "delivery_delay_seconds": result.delivery_delay_seconds,
        "missing_symbols": list(result.missing_symbols),
        "invalid_symbols": list(result.invalid_symbols),
        "accepted_symbols": [fact.symbol for fact in result.facts],
        "fact_ids": [fact.fact_id for fact in result.facts],
        "source_refs": [list(fact.source_refs) for fact in result.facts],
        "reason": result.reason,
        "health": dict(health),
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    if not LOADED_COMMIT:
        raise RuntimeError("CRIPTA_U5_OI30S_LOADED_COMMIT is required")
    if FACT_SOURCE_ID != "BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1":
        raise RuntimeError("source-only soak requires the owner-approved new fact_source_id")
    if SLOT_SECONDS != 30:
        raise RuntimeError("owner-approved OI source slot cadence is exactly 30 seconds")
    if SOAK_MINUTES < 480:
        raise RuntimeError("source-only soak must be at least 480 minutes")
    total_seconds = SOAK_MINUTES * 60
    if total_seconds % SLOT_SECONDS:
        raise RuntimeError("soak duration must contain a whole number of source slots")

    bundle = load_v1_compatibility_bundle(SOURCE_ROOT)
    symbols = tuple(bundle.card.symbols)
    if len(symbols) != 10:
        raise RuntimeError("frozen V1 source soak requires exactly 10 symbols")
    config = Oi30sConfig(
        fact_source_id=FACT_SOURCE_ID,
        required_symbols=symbols,
        slot_seconds=SLOT_SECONDS,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        retry_interval_seconds=RETRY_INTERVAL_SECONDS,
    )
    expected_slots = total_seconds // SLOT_SECONDS
    process_started_at = datetime.now(UTC)
    first_slot = slot_at(process_started_at, SLOT_SECONDS) + timedelta(seconds=SLOT_SECONDS)
    soak_id = f"oi30s-soak-{int(process_started_at.timestamp())}-{os.getpid()}"
    evidence_path = STATE_ROOT / f"{soak_id}.jsonl"
    health = Oi30sHealthTracker(config)
    _append_jsonl(
        evidence_path,
        {
            "event": "SOAK_START",
            "soak_id": soak_id,
            "fact_source_id": FACT_SOURCE_ID,
            "loaded_commit": LOADED_COMMIT,
            "process_started_at": process_started_at.isoformat(),
            "first_nominal_slot_at": first_slot.isoformat(),
            "expected_slots": expected_slots,
            "required_symbols": list(symbols),
            "endpoint": f"{PUBLIC_REST}/v5/market/tickers?category=linear",
            "trading_effect": "NONE",
        },
    )

    def write_status(state: str, *, completed: int, current: OiSlotResult | None = None) -> None:
        payload: dict[str, object] = {
            "service": "cripta-u5-oi30s-source-soak.service",
            "soak_id": soak_id,
            "state": state,
            "fact_source_id": FACT_SOURCE_ID,
            "loaded_commit": LOADED_COMMIT,
            "process_started_at": process_started_at.isoformat(),
            "first_nominal_slot_at": first_slot.isoformat(),
            "required_symbols": len(symbols),
            "expected_slots": expected_slots,
            "processed_slots": completed,
            "evidence_path": str(evidence_path),
            "trading_effect": "NONE",
            "updated_at": datetime.now(UTC).isoformat(),
            **health.snapshot(),
        }
        if current is not None:
            payload["current_slot_id"] = current.slot_id
            payload["current_slot_state"] = current.state.value
            payload["current_slot_reason"] = current.reason
        _atomic_json(STATUS_PATH, payload)

    write_status("WAITING_FIRST_SLOT", completed=0)
    for index in range(expected_slots):
        nominal = first_slot + timedelta(seconds=index * SLOT_SECONDS)
        delay = (nominal - datetime.now(UTC)).total_seconds()
        if delay > 0:
            time.sleep(delay)
        result = poll_current_oi_slot(
            config,
            nominal_slot_at=nominal,
            fetch=_fetch_current_oi,
        )
        health.accept(result)
        snap = health.snapshot()
        _append_jsonl(evidence_path, _slot_evidence(soak_id, result, snap))
        processed = index + 1
        if result.state is not OiSlotState.COMPLETE:
            write_status("FAIL", completed=processed, current=result)
            _append_jsonl(
                evidence_path,
                {
                    "event": "SOAK_END",
                    "soak_id": soak_id,
                    "state": "FAIL",
                    "failed_slot_id": result.slot_id,
                    "reason": result.reason,
                    "health": snap,
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
            return 2
        write_status("RUNNING", completed=processed, current=result)

    final = health.snapshot()
    pass_state = (
        final["complete_slots"] == expected_slots
        and final["missed_slots"] == 0
        and final["incomplete_slots"] == 0
        and final["silent_gaps"] == 0
    )
    state = "PASS" if pass_state else "FAIL"
    write_status(state, completed=expected_slots)
    _append_jsonl(
        evidence_path,
        {
            "event": "SOAK_END",
            "soak_id": soak_id,
            "state": state,
            "health": final,
            "completed_at": datetime.now(UTC).isoformat(),
        },
    )
    return 0 if pass_state else 3


if __name__ == "__main__":
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    raise SystemExit(main())
