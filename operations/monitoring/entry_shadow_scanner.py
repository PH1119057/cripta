from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from bybit_workbench.app.config import AppSettings
from bybit_workbench.domain.types import AppMode
from bybit_workbench.entry_bot.config import EntryBotConfig
from bybit_workbench.entry_bot.runtime import EntryBotRuntime
from entry_dispatcher_shadow import consume_for_signal, prepare_database


STATE_PATH = Path(os.environ.get("CRIPTA_ENTRY_SHADOW_STATE", "/var/lib/cripta/entry_shadow/status.json"))
DATABASE_PATH = Path(os.environ.get("CRIPTA_ENTRY_SHADOW_DB", "/var/lib/cripta/entry_shadow/workbench.db"))
CALIBRATION_PATH = Path(os.environ.get("CRIPTA_ENTRY_CALIBRATION", "/var/lib/cripta/entry_shadow/entry_bot_calibration.json"))


def json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    raise TypeError(type(value).__name__)


def write_state(runtime: EntryBotRuntime) -> None:
    snapshot = runtime.snapshot()
    payload = {
        "updated_at_epoch": int(time.time()),
        "state": snapshot.state.value,
        "running": snapshot.running,
        "detail": snapshot.detail,
        "execution_mode": "Только наблюдение; отправка заявок невозможна",
        "audit_event_count": snapshot.audit_event_count,
        "assets": [asdict(item) for item in snapshot.assets],
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, default=json_value), encoding="utf-8")
    temporary.replace(STATE_PATH)


def persist_signals(connection: psycopg.Connection, runtime: EntryBotRuntime, horizon_seconds: int) -> int:
    inserted = 0
    now_ms = int(time.time() * 1000)
    with connection.transaction():
        for item in runtime.drain_signals():
            cursor = connection.execute(
                """INSERT INTO monitoring.opportunities(
                    signal_id,bot_id,strategy_version,symbol,direction,signal_at_epoch_ms,
                    signal_price,decision,decision_reason,traffic_light,horizon_seconds,state,
                    created_at_epoch_ms
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,'shadow',%s,'green',%s,'tracking',%s)
                ON CONFLICT(signal_id) DO NOTHING""",
                (
                    item.signal_id,
                    "entry-v1-shadow",
                    item.strategy_version,
                    item.symbol,
                    item.direction.lower(),
                    int(item.touch_at.timestamp() * 1000),
                    float(item.entry_price),
                    "Условия Entry V1 выполнены; виртуальный вход без отправки заявки",
                    horizon_seconds,
                    now_ms,
                ),
            )
            inserted += cursor.rowcount
            zone_rows = []
            for zone in item.geometry:
                protective = "support" if item.direction == "Long" else "resistance"
                for role in ("support", "resistance"):
                    lower = getattr(zone, f"{role}_bottom")
                    upper = getattr(zone, f"{role}_top")
                    zone_identity = (
                        f"{item.signal_id}|{zone.timeframe}|{role}|{lower}|{upper}"
                    )
                    zone_rows.append(
                        {
                            "zone_id": "ZONE-" + hashlib.sha256(
                                zone_identity.encode("utf-8")
                            ).hexdigest()[:24],
                            "role": "PROTECTIVE" if role == protective else "OBSTACLE",
                            "kind": role,
                            "lower_boundary": lower,
                            "upper_boundary": upper,
                            "timeframe": zone.timeframe,
                            "source_candle_closed_at": zone.observed_at,
                            "regime_reset_at": zone.regime_reset_at,
                        }
                    )
            geometry_payload = {
                "signal_id": item.signal_id,
                "strategy_id": item.strategy_id,
                "strategy_version": item.strategy_version,
                "symbol": item.symbol,
                "side": item.direction,
                "signal_at": item.touch_at.isoformat(),
                "geometry_observed_at": max(
                    (zone.observed_at for zone in item.geometry), default=item.candidate_bar_at
                ).isoformat(),
                "geometry_version": item.geometry_version,
                "zones": zone_rows,
            }
            canonical = json.dumps(
                geometry_payload, ensure_ascii=False, default=json_value,
                sort_keys=True, separators=(",", ":"),
            )
            geometry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            geometry_handoff_id = f"GH-{geometry_hash[:32]}"
            config_fingerprint = hashlib.sha256(
                json.dumps(
                    asdict(runtime.config), default=json_value, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                """INSERT INTO monitoring.entry_geometry_handoffs(
                    geometry_handoff_id,signal_id,strategy_id,strategy_version,
                    entry_fingerprint,symbol,side,signal_at,geometry_observed_at,
                    geometry_version,config_fingerprint,geometry_hash,payload,provenance)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(signal_id) DO NOTHING""",
                (
                    geometry_handoff_id, item.signal_id, item.strategy_id,
                    item.strategy_version, config_fingerprint, item.symbol,
                    item.direction, item.touch_at,
                    max((zone.observed_at for zone in item.geometry), default=item.candidate_bar_at),
                    item.geometry_version,
                    config_fingerprint,
                    geometry_hash,
                    canonical,
                    json.dumps({"source": "entry_shadow_scanner", "causal": True}),
                ),
            )
            consume_for_signal(
                connection,
                signal_id=item.signal_id,
                symbol=item.symbol,
                direction=item.direction,
                signal_at=item.touch_at,
            )
    return inserted


def main() -> None:
    settings = AppSettings(
        mode=AppMode.LIVE,
        database_path=DATABASE_PATH,
        allow_live_trading=False,
        enable_testnet_execution=False,
        rest_url_override="https://api.bybit.kz",
    )
    monitored = (
        "UNIUSDT", "LINKUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT",
        "BNBUSDT", "AVAXUSDT", "SUIUSDT", "AAVEUSDT", "LTCUSDT",
        "BTCUSDT", "ETHUSDT",
        "APTUSDT", "ARBUSDT", "BCHUSDT", "DOTUSDT", "HBARUSDT",
        "INJUSDT", "OPUSDT", "TRXUSDT",
    )
    config = replace(
        EntryBotConfig(),
        working_symbols=monitored,
        reference_symbols=(),
        candidate_outcome_horizon_minutes=24 * 60,
        candidate_cooldown_minutes=0,
        require_oi_calibration=False,
        monitoring_only_expanded_universe=True,
    )
    runtime = EntryBotRuntime(settings, config=config, calibration_path=CALIBRATION_PATH)
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        runtime.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    try:
        prepare_database(connection)
        connection.execute("""CREATE TABLE IF NOT EXISTS monitoring.entry_geometry_handoffs(
            geometry_handoff_id TEXT PRIMARY KEY,
            signal_id TEXT UNIQUE NOT NULL,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            entry_fingerprint TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_at TIMESTAMPTZ NOT NULL,
            geometry_observed_at TIMESTAMPTZ NOT NULL,
            geometry_version TEXT NOT NULL,
            config_fingerprint TEXT NOT NULL,
            geometry_hash TEXT UNIQUE NOT NULL,
            payload JSONB NOT NULL,
            provenance JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK(geometry_observed_at<=signal_at))""")
        connection.commit()
        runtime.start()
        next_state_write = 0.0
        while not stopping:
            persist_signals(connection, runtime, config.candidate_outcome_horizon_minutes * 60)
            now = time.monotonic()
            if now >= next_state_write:
                write_state(runtime)
                next_state_write = now + 2
            time.sleep(0.25)
    finally:
        runtime.close()
        connection.close()
        write_state(runtime)


if __name__ == "__main__":
    main()
