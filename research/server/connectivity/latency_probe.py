from __future__ import annotations

import json
import os
import statistics
import time
from collections import deque
from pathlib import Path

import websocket

URL = os.environ.get("BYBIT_PUBLIC_WS", "wss://stream.bybit.kz/v5/public/linear")
SYMBOLS = tuple(os.environ.get(
    "CRIPTA_SYMBOLS",
    "1000PEPEUSDT,AAVEUSDT,ADAUSDT,AVAXUSDT,BNBUSDT,BTCUSDT,DOGEUSDT,ETHUSDT,LINKUSDT,LTCUSDT,SOLUSDT,SUIUSDT,UNIUSDT,XRPUSDT",
).split(","))
STATUS = Path(os.environ.get("CRIPTA_CONNECTIVITY_STATUS", "/var/lib/cripta/connectivity/status.json"))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction))]


def atomic_status(value: dict[str, object]) -> None:
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATUS)


def run() -> None:
    event_ms: deque[float] = deque(maxlen=20_000)
    decode_us: deque[float] = deque(maxlen=20_000)
    ping_ms: deque[float] = deque(maxlen=200)
    messages = 0
    reconnects = 0
    connected_since = 0
    last_message_ms = 0
    while True:
        try:
            ws = websocket.create_connection(URL, timeout=10, enable_multithread=False)
            ws.settimeout(1)
            connected_since = int(time.time())
            ws.send(json.dumps({"req_id": "cripta-latency", "op": "subscribe", "args": [f"orderbook.1.{s}" for s in SYMBOLS]}, separators=(",", ":")))
            next_ping = time.monotonic() + 20
            next_status = time.monotonic()
            ping_sent: float | None = None
            while True:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    raw = None
                received_ns = time.time_ns()
                if raw:
                    parse_started = time.perf_counter_ns()
                    message = json.loads(raw)
                    decode_us.append((time.perf_counter_ns() - parse_started) / 1_000)
                    messages += 1
                    last_message_ms = received_ns // 1_000_000
                    if message.get("ret_msg") == "pong" and ping_sent is not None:
                        ping_ms.append((time.monotonic() - ping_sent) * 1_000)
                        ping_sent = None
                    cts = message.get("cts")
                    if isinstance(cts, int):
                        event_ms.append(last_message_ms - cts)
                now = time.monotonic()
                if now >= next_ping:
                    ping_sent = now
                    ws.send('{"req_id":"cripta-ping","op":"ping"}')
                    next_ping = now + 20
                if now >= next_status:
                    ages = list(event_ms)
                    decodes = list(decode_us)
                    rtts = list(ping_ms)
                    atomic_status({
                        "state": "connected",
                        "url": URL,
                        "symbols": len(SYMBOLS),
                        "connected_since_epoch": connected_since,
                        "last_message_epoch_ms": last_message_ms,
                        "messages": messages,
                        "reconnects": reconnects,
                        "sample_size": len(ages),
                        "matching_engine_to_server_ms": {"p50": percentile(ages, .50), "p95": percentile(ages, .95), "p99": percentile(ages, .99)},
                        "json_decode_us": {"p50": percentile(decodes, .50), "p95": percentile(decodes, .95), "p99": percentile(decodes, .99)},
                        "websocket_ping_ms": {"p50": percentile(rtts, .50), "p95": percentile(rtts, .95), "last": rtts[-1] if rtts else None},
                        "updated_at_epoch": int(time.time()),
                    })
                    next_status = now + 1
        except Exception as exc:
            reconnects += 1
            atomic_status({"state": "reconnecting", "url": URL, "error": f"{type(exc).__name__}: {exc}", "reconnects": reconnects, "updated_at_epoch": int(time.time())})
            time.sleep(min(30, reconnects))


if __name__ == "__main__":
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    run()
