from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.mayak.research.historical_signal_backfill import (
    PRE_ROLL_SECONDS,
    BlockSpec,
    Signal,
    archive_content_manifest_fingerprint,
    build_blocks,
    load_plus_010_outcomes,
    run_block,
    write_outputs,
)


def _signal(symbol: str, direction: str, when: datetime, price: float = 100.0) -> Signal:
    stamp = when.astimezone(UTC)
    return Signal(
        symbol=symbol,
        direction=direction,
        touch_at=stamp.isoformat(),
        touch_epoch=stamp.timestamp(),
        original_entry_price=price,
    )


def _write_trade_archive(path: Path, rows: list[tuple[float, str, str, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "symbol", "side", "size", "price"])
        for event_at, symbol, side, size, price in rows:
            writer.writerow([event_at, symbol, side, size, price])


def test_content_manifest_fingerprint_uses_declared_sha256_and_size(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    raw_file = root / "BTCUSDT" / "public_trades" / "BTCUSDT2026-05-18.csv.gz"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"exact-archive")
    import hashlib

    digest = hashlib.sha256(raw_file.read_bytes()).hexdigest()
    manifest = root / "MANIFEST.sha256.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": "BTCUSDT/public_trades/BTCUSDT2026-05-18.csv.gz",
                        "bytes": raw_file.stat().st_size,
                        "sha256": digest,
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    fingerprint, manifest_sha, rows = archive_content_manifest_fingerprint(
        [raw_file], root, manifest
    )
    assert len(fingerprint) == 64
    assert manifest_sha == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert rows == [
        {
            "path": "BTCUSDT/public_trades/BTCUSDT2026-05-18.csv.gz",
            "bytes": len(b"exact-archive"),
            "sha256": digest,
        }
    ]


def test_build_blocks_has_two_hour_causal_preroll(tmp_path: Path) -> None:
    touch = datetime(2026, 5, 18, 0, 45, tzinfo=UTC)
    signal = _signal("BTCUSDT", "Long", touch)
    blocks = build_blocks(
        [signal],
        symbols=("BTCUSDT",),
        raw_root=tmp_path / "raw",
        output_dir=tmp_path / "out",
        source_commit="a" * 40,
        replay_code_sha256="c" * 64,
        block_days=7,
        force=False,
    )
    assert len(blocks) == 1
    midnight = datetime(2026, 5, 18, 0, 0, tzinfo=UTC).timestamp()
    assert PRE_ROLL_SECONDS == 7200
    assert blocks[0].start_epoch == midnight - 7200
    assert blocks[0].end_epoch == signal.touch_epoch


def test_plus_010_mapping_keeps_reached_stop_and_unresolved_distinct(tmp_path: Path) -> None:
    base = [
        _signal("BTCUSDT", "Long", datetime(2026, 5, 18, 1, 0, tzinfo=UTC)),
        _signal("ETHUSDT", "Short", datetime(2026, 5, 18, 2, 0, tzinfo=UTC)),
        _signal("ADAUSDT", "Long", datetime(2026, 5, 18, 3, 0, tzinfo=UTC)),
    ]
    audit = tmp_path / "p47j.csv"
    with audit.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["symbol", "touch_at", "activation_at", "outcome"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "BTCUSDT",
                "touch_at": base[0].touch_at,
                "activation_at": "2026-05-18T01:01:00+00:00",
                "outcome": "floor_minus_0p10",
            }
        )
        writer.writerow(
            {
                "symbol": "ETHUSDT",
                "touch_at": base[1].touch_at,
                "activation_at": "",
                "outcome": "data_end_no_activation",
            }
        )
    result = load_plus_010_outcomes(audit, base)
    assert result[base[0].key]["plus_010_vs_minus_100"] == (
        "reached_plus_0p10_before_minus_1p00"
    )
    assert result[base[1].key]["plus_010_vs_minus_100"] == (
        "data_end_before_plus_0p10_or_minus_1p00"
    )
    assert result[base[2].key]["plus_010_vs_minus_100"] == (
        "hit_minus_1p00_before_plus_0p10"
    )


def test_run_block_uses_only_market_events_at_or_before_signal(tmp_path: Path) -> None:
    symbols = ("BTCUSDT", "ETHUSDT")
    touch = datetime(2026, 5, 18, 0, 45, tzinfo=UTC)
    signal = _signal("BTCUSDT", "Long", touch)
    start = datetime(2026, 5, 17, 22, 0, tzinfo=UTC).timestamp()
    target = touch.timestamp()
    raw = tmp_path / "raw"

    for symbol, prior_price, current_price in (
        ("BTCUSDT", 100.0, 101.0),
        ("ETHUSDT", 200.0, 202.0),
    ):
        _write_trade_archive(
            raw / symbol / "public_trades" / f"{symbol}2026-05-17.csv.gz",
            [(target - 7000, symbol, "Buy", 1.0, prior_price)],
        )
        _write_trade_archive(
            raw / symbol / "public_trades" / f"{symbol}2026-05-18.csv.gz",
            [
                (target - 100, symbol, "Buy", 2.0, current_price),
                # Future trade must remain unread by the snapshot at target.
                (target + 1, symbol, "Sell", 9999.0, current_price * 0.5),
            ],
        )

    spec = BlockSpec(
        block_id="smoke",
        start_epoch=start,
        end_epoch=target + 60,
        signals=(signal,),
        symbols=symbols,
        raw_root=str(raw),
        output_dir=str(tmp_path / "out"),
        source_commit="b" * 40,
        replay_code_sha256="d" * 64,
        force=True,
    )
    block = run_block(spec)
    assert block["processed_events"] == 4
    assert len(block["signals"]) == 1
    row = block["signals"][0]
    serialized = json.dumps(row, ensure_ascii=False).lower()
    assert "outcome" not in serialized
    derivatives = row["mayak"]["coin_context"]["payload"]["money"]["derivatives"]
    assert derivatives["60m"]["prior_turnover_usd"] == 100.0
    assert derivatives["60m"]["turnover_usd"] == 202.0
    assert derivatives["60m"]["net_usd"] == 202.0
    assert row["mayak"]["coin_context"]["provenance"]["trading_command"] is False


def test_outcomes_are_joined_only_after_objective_context_is_built(tmp_path: Path) -> None:
    signal = _signal("BTCUSDT", "Long", datetime(2026, 5, 18, 0, 45, tzinfo=UTC))
    coin_context = {
        "coin_context_id": "CMC-test",
        "data_quality": "INSUFFICIENT",
        "payload": {
            "money": {
                "spot": {"5m": {"status": "NO_DATA"}},
                "derivatives": {
                    horizon: {
                        "buy_usd": 1.0,
                        "sell_usd": 0.0,
                        "net_usd": 1.0,
                        "turnover_usd": 1.0,
                        "net_share": 1.0,
                        "speed_usd_per_min": 1.0,
                        "acceleration_usd_per_min2": None,
                        "large_trade_share": 0.0,
                        "turnover_ratio_to_prior": None,
                    }
                    for horizon in ("1m", "5m", "15m", "30m", "60m")
                },
            },
            "liquidations": {"status": "NO_DATA"},
            "relative_strength": {
                horizon: {
                    "coin_return_pct": 0.0,
                    "panel_median_return_pct": 0.0,
                    "relative_to_panel_pct": 0.0,
                    "relative_to_btc_pct": 0.0,
                    "relative_to_eth_pct": 0.0,
                }
                for horizon in ("1m", "5m", "15m", "60m")
            },
        },
    }
    objective = {
        "signal": {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "touch_at": signal.touch_at,
            "touch_epoch": signal.touch_epoch,
            "original_entry_price": signal.original_entry_price,
        },
        "signal_key": signal.key,
        "mayak": {
            "observed_at": signal.touch_at,
            "engine_version": "mayak-v2.2",
            "feature_version": "objective-coin-context-v2",
            "state": "спокойный рынок",
            "confidence": 0.2,
            "price_breadth": {
                "median_return_pct": 0.0,
                "up_share": 0.5,
                "down_share": 0.5,
            },
            "money_breadth": {},
            "direction_synchronization": {"agreement": 0.5},
            "coin_context": coin_context,
            "dispatcher_handoff": {},
        },
        "replay_source_coverage": {},
    }
    block = {
        "signals": [objective],
        "processed_events": 2,
        "reused": False,
    }
    plus_010 = {
        signal.key: {
            "plus_010_vs_minus_100": "reached_plus_0p10_before_minus_1p00",
            "plus_010_activation_at": signal.touch_at,
        }
    }
    plus_110 = {
        signal.key: {
            "plus_110_vs_minus_100": "reached_plus_1p10",
            "plus_110_event_at": signal.touch_at,
            "plus_110_complete_horizon": "True",
        }
    }
    write_outputs(
        output_dir=tmp_path,
        blocks=[block],
        selected_signals=[signal],
        plus_010=plus_010,
        plus_110=plus_110,
        manifest={"outcomes_used_by_replay": False},
    )
    context_text = (tmp_path / "MAYAK_SIGNAL_CONTEXTS.jsonl").read_text(encoding="utf-8")
    assert "plus_010_vs_minus_100" not in context_text
    assert "plus_110_vs_minus_100" not in context_text
    correlation = (tmp_path / "MAYAK_ENTRY_CORRELATION.csv").read_text(encoding="utf-8-sig")
    assert "reached_plus_0p10_before_minus_1p00" in correlation
    assert "reached_plus_1p10" in correlation
