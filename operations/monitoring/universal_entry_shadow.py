from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import psycopg
import websocket

from bybit_workbench.domain.models import Candle
from bybit_workbench.exchange.bybit.mappers import map_rest_klines, map_ws_klines
from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.market_watch import GenericOiPoint
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.parity import V1DeterministicParityRunner
from bybit_workbench.universal_entry.shadow_runtime import (
    DurableFactJournal,
    OnlineParityComparator,
    ShadowComparability,
    ShadowComparabilityGate,
    ShadowRunIdentity,
    derive_unknown_prestart_horizon_seconds,
)
from bybit_workbench.universal_entry.storage import ShadowParityStore
from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

PROJECT_ROOT = Path(os.environ.get("CRIPTA_U5_SOURCE_ROOT", "/srv/cripta/source_checkout"))
STATE_ROOT = Path(os.environ.get("CRIPTA_U5_STATE_ROOT", "/var/lib/cripta/universal_entry_shadow"))
STATUS_PATH = STATE_ROOT / "status.json"
JOURNAL_PATH = STATE_ROOT / "normalized_facts.jsonl"
PUBLIC_REST = os.environ.get("CRIPTA_U5_PUBLIC_REST", "https://api.bybit.kz")
PUBLIC_WS = os.environ.get("CRIPTA_U5_PUBLIC_WS", "wss://stream.bybit.kz/v5/public/linear")
DATABASE_DSN = os.environ.get(
    "CRIPTA_U5_DATABASE_DSN", "dbname=cripta user=cripta host=/var/run/postgresql"
)
LOADED_COMMIT = os.environ.get("CRIPTA_U5_LOADED_COMMIT", "").strip()
BASELINE_COMMIT = os.environ.get(
    "CRIPTA_U5_BASELINE_COMMIT", "49670cb0631a8742b2bf8dace9ab33d6b29a107d"
).strip()
FACT_SOURCE_ID = os.environ.get("CRIPTA_U5_FACT_SOURCE_ID", "BYBIT_PUBLIC_NORMALIZED_U5_V1").strip()
OI_SAMPLE_SECONDS = int(os.environ.get("CRIPTA_U5_OI_SAMPLE_SECONDS", "30"))
_HTTPS_CONTEXT = ssl.create_default_context()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _result_list(payload: Mapping[str, object]) -> list[Any]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("public response result is not an object")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise ValueError("public response result.list is not a list")
    return rows


def _get_json(endpoint: str, params: Mapping[str, object]) -> dict[str, Any]:
    url = f"{PUBLIC_REST.rstrip('/')}{endpoint}?{urllib.parse.urlencode(params)}"
    transient: tuple[type[Exception], ...] = (
        TimeoutError,
        socket.timeout,
        ssl.SSLError,
        urllib.error.URLError,
        ConnectionError,
    )
    for attempt in range(4):
        request = urllib.request.Request(url, headers={"User-Agent": "Cripta-U5-Shadow/1"})
        try:
            with urllib.request.urlopen(request, timeout=15.0, context=_HTTPS_CONTEXT) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except transient:
            if attempt == 3:
                raise
            time.sleep(min(4.0, 0.75 * (2**attempt)))
            continue
        if not isinstance(payload, dict):
            raise ValueError("public response is not an object")
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(
                f"public endpoint failed: retCode={payload.get('retCode')} "
                f"retMsg={payload.get('retMsg')}"
            )
        return cast(dict[str, Any], payload)
    raise RuntimeError("unreachable public REST retry state")


def _fetch_history(symbol: str, observed_at: datetime) -> dict[str, tuple[Candle, ...]]:
    history: dict[str, tuple[Candle, ...]] = {}
    for interval in ("5", "15", "60"):
        payload = _get_json(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": interval, "limit": 1000},
        )
        candles = map_rest_klines(
            cast(list[list[Any]], _result_list(payload)),
            symbol=symbol,
            interval=interval,
            observed_at=observed_at,
        )
        history[interval] = tuple(item for item in candles if item.is_closed)
    return history


def _fetch_oi_history(symbol: str) -> tuple[GenericOiPoint, ...]:
    payload = _get_json(
        "/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 30},
    )
    points: list[GenericOiPoint] = []
    for raw in _result_list(payload):
        if not isinstance(raw, Mapping):
            continue
        try:
            value = Decimal(str(raw["openInterest"]))
            timestamp = datetime.fromtimestamp(int(str(raw["timestamp"])) / 1000, UTC)
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid OI row for {symbol}") from exc
        if value > 0:
            points.append(GenericOiPoint(timestamp, value))
    return tuple(sorted(points, key=lambda item: item.timestamp))


def _trade_fact(raw: Mapping[str, object], received_at: datetime) -> MarketFactEnvelope:
    symbol = str(raw.get("s") or "").upper()
    traded_at = datetime.fromtimestamp(int(str(raw["T"])) / 1000, UTC)
    trade_id = str(raw.get("i") or "")
    source_identity = (
        trade_id
        or hashlib.sha256(
            json.dumps(dict(raw), sort_keys=True, default=str, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
    )
    return MarketFactEnvelope(
        fact_id="trade-" + source_identity,
        event_kind="PUBLIC_TRADE",
        symbol=symbol,
        observed_at=traded_at,
        event_at=traded_at,
        received_at=received_at,
        source_refs=(f"bybit:publicTrade:{symbol}:{source_identity}",),
        attributes=FrozenPolicy.from_mapping(
            {
                "price": str(raw["p"]),
                "size": str(raw["v"]),
                "taker_side": str(raw["S"]),
            }
        ),
    )


def _kline_facts(
    message: Mapping[str, object], received_at: datetime
) -> tuple[MarketFactEnvelope, ...]:
    try:
        candles = map_ws_klines(dict(message))
    except Exception:
        return ()
    topic = str(message.get("topic") or "")
    result: list[MarketFactEnvelope] = []
    for candle in candles:
        boundary = candle.closed_at if candle.is_closed else candle.opened_at
        kind = "CANDLE_CLOSED" if candle.is_closed else "BAR_OPEN"
        if not candle.is_closed and candle.timeframe != "5":
            continue
        observed_at = candle.closed_at if candle.is_closed else received_at
        attributes: dict[str, object]
        if candle.is_closed:
            attributes = {
                "timeframe": candle.timeframe,
                "opened_at": candle.opened_at.isoformat(),
                "closed_at": candle.closed_at.isoformat(),
                "open": str(candle.open),
                "high": str(candle.high),
                "low": str(candle.low),
                "close": str(candle.close),
                "volume": str(candle.volume),
            }
        else:
            attributes = {
                "opened_at": candle.opened_at.isoformat(),
                "open_price": str(candle.open),
            }
        identity = fingerprint(
            {
                "topic": topic,
                "symbol": candle.symbol,
                "timeframe": candle.timeframe,
                "boundary": boundary,
                "closed": candle.is_closed,
            }
        )[:32]
        result.append(
            MarketFactEnvelope(
                fact_id=f"{kind.lower()}-{identity}",
                event_kind=kind,
                symbol=candle.symbol,
                observed_at=observed_at,
                event_at=boundary,
                received_at=received_at,
                source_refs=(
                    f"bybit:{topic}:{candle.symbol}:{candle.timeframe}:{boundary.isoformat()}",
                ),
                attributes=FrozenPolicy.from_mapping(attributes),
            )
        )
    return tuple(result)


def _ticker_oi_fact(
    message: Mapping[str, object],
    received_at: datetime,
    last_samples: dict[str, datetime],
) -> MarketFactEnvelope | None:
    raw = message.get("data")
    if isinstance(raw, list):
        if not raw or not isinstance(raw[0], Mapping):
            return None
        data = raw[0]
    elif isinstance(raw, Mapping):
        data = raw
    else:
        return None
    symbol = str(data.get("symbol") or str(message.get("topic") or "").split(".")[-1]).upper()
    observed_ms = int(str(message.get("ts") or int(received_at.timestamp() * 1000)))
    observed_at = datetime.fromtimestamp(observed_ms / 1000, UTC)
    previous = last_samples.get(symbol)
    if previous is not None and observed_at - previous < timedelta(seconds=OI_SAMPLE_SECONDS):
        return None
    raw_oi = data.get("openInterest")
    if raw_oi in (None, ""):
        return None
    try:
        oi = Decimal(str(raw_oi))
    except InvalidOperation:
        return None
    last_samples[symbol] = observed_at
    identity = f"{symbol}:{observed_ms}:{oi}"
    return MarketFactEnvelope(
        fact_id="oi-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        event_kind="OPEN_INTEREST",
        symbol=symbol,
        observed_at=observed_at,
        event_at=observed_at,
        received_at=received_at,
        source_refs=(f"bybit:ticker-oi:{identity}",),
        attributes=FrozenPolicy.from_mapping({"open_interest": str(oi)}),
    )


def _topics(symbols: tuple[str, ...]) -> tuple[str, ...]:
    topics: list[str] = []
    for symbol in symbols:
        for interval in ("5", "15", "60"):
            topics.append(f"kline.{interval}.{symbol}")
        topics.append(f"tickers.{symbol}")
        topics.append(f"publicTrade.{symbol}")
    return tuple(topics)


def _status_payload(
    *,
    identity: ShadowRunIdentity,
    state: ShadowComparability,
    gate: ShadowComparabilityGate,
    comparator: OnlineParityComparator,
    facts_received: int,
    seeded_symbols: int,
    total_symbols: int,
) -> dict[str, object]:
    return {
        "updated_at": datetime.now(UTC).isoformat(),
        "service": "cripta-universal-entry-shadow.service",
        "trading_effect": "NONE",
        "parity_run_id": identity.parity_run_id,
        "state": state.value,
        "state_reason": gate.reason,
        "source_commit": identity.universal_source_commit,
        "baseline_commit": identity.baseline_source_commit,
        "strategy_config_fingerprint": identity.strategy_config_fingerprint,
        "entry_plan_fingerprint": identity.entry_plan_fingerprint,
        "calibration_sha256": identity.calibration_sha256,
        "calibration_size": identity.calibration_size,
        "fact_source_id": identity.fact_source_id,
        "facts_received": facts_received,
        "seeded_symbols": seeded_symbols,
        "total_symbols": total_symbols,
        **comparator.summary(),
    }


def main() -> None:
    if not LOADED_COMMIT:
        raise RuntimeError("CRIPTA_U5_LOADED_COMMIT is required")
    if OI_SAMPLE_SECONDS <= 0:
        raise RuntimeError("CRIPTA_U5_OI_SAMPLE_SECONDS must be positive")
    bundle = load_v1_compatibility_bundle(PROJECT_ROOT)
    plan, _ = materialize_plans(bundle.card, bundle.activation)
    calibration_path = PROJECT_ROOT / bundle.calibration_relative_path
    calibration_size = calibration_path.stat().st_size
    if calibration_size != 4647:
        raise RuntimeError("frozen calibration size does not match published U4 evidence")
    symbols = bundle.card.symbols
    if len(symbols) != 10:
        raise RuntimeError("U5 frozen V1 Strategy scope must contain exactly 10 symbols")

    started_at = datetime.now(UTC)
    service_instance_id = f"{socket.gethostname()}:{os.getpid()}:{started_at.isoformat()}"
    parity_run_id = (
        "spr-"
        + fingerprint(
            {
                "strategy": bundle.card.strategy_config_fingerprint,
                "plan": plan.entry_plan_fingerprint,
                "service": service_instance_id,
                "started_at": started_at,
            }
        )[:32]
    )
    identity = ShadowRunIdentity(
        parity_run_id=parity_run_id,
        strategy_id=bundle.card.strategy_id,
        strategy_version=bundle.card.strategy_version,
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        entry_plan_fingerprint=plan.entry_plan_fingerprint,
        calibration_sha256=bundle.calibration_sha256,
        calibration_size=calibration_size,
        baseline_source_commit=BASELINE_COMMIT,
        universal_source_commit=LOADED_COMMIT,
        fact_source_id=FACT_SOURCE_ID,
        service_instance_id=service_instance_id,
        started_at=started_at,
    )
    gate = ShadowComparabilityGate(
        started_at,
        required_warmup_seconds=derive_unknown_prestart_horizon_seconds(plan),
    )
    journal = DurableFactJournal(JOURNAL_PATH)
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    connection = psycopg.connect(DATABASE_DSN, autocommit=True)
    store = ShadowParityStore(connection)
    store.ensure_policy_identity(bundle.card, plan)
    recovered_run_id = store.finalize_unfinished_run_for_restart(
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        entry_plan_fingerprint=plan.entry_plan_fingerprint,
        fact_source_id=FACT_SOURCE_ID,
        occurred_at=started_at,
    )
    initial_summary: dict[str, object] = {
        "trading_effect": "NONE",
        "state": "WARMUP",
        "reason": "service startup",
        "recovered_unfinished_run_id": recovered_run_id,
        "symbols": list(symbols),
        "facts_received": 0,
        "comparable_events": 0,
        "matched_events": 0,
        "mismatches_by_category": {},
    }
    store.start_run(identity, summary=initial_summary)

    runners: dict[str, V1DeterministicParityRunner] = {}
    try:
        for symbol in symbols:
            if stopping:
                break
            observed_at = datetime.now(UTC)
            history = _fetch_history(symbol, observed_at)
            oi_history = _fetch_oi_history(symbol)
            runner_history: dict[str, tuple[object, ...]] = {
                timeframe: tuple(rows) for timeframe, rows in history.items()
            }
            runners[symbol] = V1DeterministicParityRunner(
                bundle,
                symbol=symbol,
                candles=runner_history,
                oi_points=tuple(oi_history),
                observed_at=observed_at,
            )
        if len(runners) != len(symbols):
            raise RuntimeError("causal history seed incomplete")
        gate.mark_seed_complete()
        comparator = OnlineParityComparator(identity, runners)
        state = gate.state_at(datetime.now(UTC))
        facts_received = 0
        flow_minutes: dict[str, set[datetime]] = {symbol: set() for symbol in symbols}
        last_oi_sample: dict[str, datetime] = {}
        _atomic_json(
            STATUS_PATH,
            _status_payload(
                identity=identity,
                state=state,
                gate=gate,
                comparator=comparator,
                facts_received=facts_received,
                seeded_symbols=len(runners),
                total_symbols=len(symbols),
            ),
        )

        sock = websocket.create_connection(PUBLIC_WS, timeout=10.0, enable_multithread=False)
        with suppress(AttributeError):
            sock.settimeout(1.0)
        try:
            topics = _topics(symbols)
            for chunk_index, start in enumerate(range(0, len(topics), 40), start=1):
                sock.send(
                    json.dumps(
                        {
                            "op": "subscribe",
                            "req_id": f"u5-shadow-{chunk_index}",
                            "args": list(topics[start : start + 40]),
                        },
                        separators=(",", ":"),
                    )
                )
            next_ping = time.monotonic() + 20.0
            next_status = time.monotonic()
            last_state = state
            while not stopping:
                message: dict[str, Any] | None = None
                received_at = datetime.now(UTC)
                try:
                    raw_message = sock.recv()
                    received_at = datetime.now(UTC)
                    if raw_message in (None, ""):
                        raise ConnectionError("public WebSocket closed")
                    parsed = json.loads(raw_message)
                    if isinstance(parsed, dict):
                        message = cast(dict[str, Any], parsed)
                except websocket.WebSocketTimeoutException:
                    pass
                facts: tuple[MarketFactEnvelope, ...] = ()
                if message is not None:
                    if message.get("op") == "subscribe":
                        if message.get("success") is False or message.get("retCode") not in (
                            None,
                            0,
                        ):
                            raise ConnectionError("U5 public subscription rejected")
                    elif message.get("op") == "pong" or message.get("ret_msg") == "pong":
                        pass
                    else:
                        topic = str(message.get("topic") or "")
                        if topic.startswith("publicTrade."):
                            data = message.get("data")
                            if isinstance(data, list):
                                rows = [
                                    _trade_fact(item, received_at)
                                    for item in data
                                    if isinstance(item, Mapping)
                                    and str(item.get("s") or "").upper() in runners
                                ]
                                facts = tuple(rows)
                        elif topic.startswith("kline."):
                            facts = tuple(
                                fact
                                for fact in _kline_facts(message, received_at)
                                if fact.symbol in runners
                            )
                        elif topic.startswith("tickers."):
                            oi_fact = _ticker_oi_fact(message, received_at, last_oi_sample)
                            if oi_fact is not None and oi_fact.symbol in runners:
                                facts = (oi_fact,)
                for fact in facts:
                    journal.append(fact)
                    facts_received += 1
                    if fact.event_kind == "PUBLIC_TRADE":
                        flow_minutes[fact.symbol].add(
                            fact.observed_at.astimezone(UTC).replace(second=0, microsecond=0)
                        )
                    if all(len(minutes) >= 5 for minutes in flow_minutes.values()):
                        gate.mark_live_sensor_complete()
                    current_state = gate.state_at(fact.observed_at)
                    if current_state is ShadowComparability.PARITY_COMPARABLE:
                        observation = comparator.process(fact)
                        store.record_observation(observation)
                        store.update_summary(
                            identity,
                            {
                                "trading_effect": "NONE",
                                "state": current_state.value,
                                "facts_received": facts_received,
                                **comparator.summary(),
                            },
                        )
                    else:
                        runners[fact.symbol].step(fact)
                    if current_state is not last_state:
                        store.transition_run(
                            identity,
                            status=current_state.value,
                            occurred_at=fact.observed_at,
                            summary={
                                "trading_effect": "NONE",
                                "state": current_state.value,
                                "reason": gate.reason,
                                "facts_received": facts_received,
                                **comparator.summary(),
                            },
                        )
                        last_state = current_state
                    if comparator.first_mismatch is not None:
                        final_summary = {
                            "trading_effect": "NONE",
                            "state": "FAIL",
                            "facts_received": facts_received,
                            **comparator.summary(),
                        }
                        store.transition_run(
                            identity,
                            status="FAIL",
                            occurred_at=fact.observed_at,
                            summary=final_summary,
                        )
                        _atomic_json(
                            STATUS_PATH,
                            _status_payload(
                                identity=identity,
                                state=current_state,
                                gate=gate,
                                comparator=comparator,
                                facts_received=facts_received,
                                seeded_symbols=len(runners),
                                total_symbols=len(symbols),
                            )
                            | {"run_status": "FAIL"},
                        )
                        return
                now_monotonic = time.monotonic()
                if now_monotonic >= next_ping:
                    sock.send('{"op":"ping"}')
                    next_ping = now_monotonic + 20.0
                if now_monotonic >= next_status:
                    state = gate.state_at(datetime.now(UTC))
                    _atomic_json(
                        STATUS_PATH,
                        _status_payload(
                            identity=identity,
                            state=state,
                            gate=gate,
                            comparator=comparator,
                            facts_received=facts_received,
                            seeded_symbols=len(runners),
                            total_symbols=len(symbols),
                        ),
                    )
                    next_status = now_monotonic + 2.0
        finally:
            with suppress(Exception):
                sock.close()

        stopped_at = datetime.now(UTC)
        final_state = gate.state_at(stopped_at)
        if final_state is ShadowComparability.PARITY_COMPARABLE:
            final_status = "PASS" if comparator.first_mismatch is None else "FAIL"
        else:
            final_status = "NOT_COMPARABLE"
        store.transition_run(
            identity,
            status=final_status,
            occurred_at=stopped_at,
            summary={
                "trading_effect": "NONE",
                "state": final_status,
                "facts_received": facts_received,
                **comparator.summary(),
            },
        )
    except Exception as exc:
        failed_at = datetime.now(UTC)
        with suppress(Exception):
            store.transition_run(
                identity,
                status="NOT_COMPARABLE",
                occurred_at=failed_at,
                summary={
                    "trading_effect": "NONE",
                    "state": "NOT_COMPARABLE",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    main()
