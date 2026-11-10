#!/usr/bin/env python3
"""Resumable 2025 P90L90 portfolio: immutable UTC-day checkpoints.

Only orchestration/persistence changes from portfolio_v1. Native tick ordering,
original Entry/Exit transitions, shared wallet, fees, causal prior-7d W7 and
rolling SECOND48 must remain byte-equivalent to the non-checkpointed model.

Each invocation processes 1..10 complete UTC days. Crash recovery uses only
fully published, SHA-chained daily state, never a partial day or an orphan
tool notification. All writes are performed by the approved cripta actor on
the research data disk. No production Strategy/Exchange/DB/LIVE changes.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import gzip
import importlib
import hashlib
import heapq
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict, deque
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_v1 as old  # noqa: E402

ROOT = old.ROOT
PARTS = old.RESULTS / "portfolio_checkpoint_2025_v2"
FINAL = old.RESULTS / "portfolio_full_2025_v2"
SELF_REL = "scripts/research/p90l90_bull2025_portfolio_chunked_v2.py"
PINNED = (*old.unit.PINNED_SOURCE_PATHS, old.SELF_REL, SELF_REL)
TOTAL_DAYS = (old.EVAL_END - old.EVAL_START).days
BOOK_KEYS = (
    "signal_index", "pause_until", "last_stops", "wallet", "reserved",
    "pos", "margin", "open_fee", "trades", "counts", "m", "peak_equity",
    "max_daily_dd_pct", "daily_marks", "max_reserved",
)
STATUS = "QC_PASS_DAY_CHECKPOINT_2025_V2"
KEY_SEP = "__"


def encode(value):
    """JSON-safe exact Decimal, float, deque, Counter and Position roundtrip."""
    if isinstance(value, D):
        return {"$decimal": str(value)}
    if isinstance(value, old.original.Position):
        return {"$position": {
            f.name: encode(getattr(value, f.name))
            for f in dataclasses.fields(old.original.Position)
        }}
    if isinstance(value, float):
        return {"$float": repr(value)}
    if isinstance(value, deque):
        return {"$deque": [encode(v) for v in value]}
    if isinstance(value, Counter):
        return {"$counter": {str(k): encode(v) for k, v in value.items()}}
    if isinstance(value, defaultdict):
        return {"$decimal_defaultdict": {k: encode(v) for k, v in value.items()}}
    if isinstance(value, tuple):
        return {"$tuple": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("checkpoint requires string dictionary keys")
        return {k: encode(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported durable portfolio state: {type(value).__name__}")


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$decimal" in value:
        return D(value["$decimal"])
    if "$float" in value:
        return float(value["$float"])
    if "$tuple" in value:
        return tuple(decode(v) for v in value["$tuple"])
    if "$deque" in value:
        return deque(decode(v) for v in value["$deque"])
    if "$counter" in value:
        return Counter({k: decode(v) for k, v in value["$counter"].items()})
    if "$decimal_defaultdict" in value:
        d = defaultdict(lambda: D(0))
        d.update({k: decode(v) for k, v in value["$decimal_defaultdict"].items()})
        return d
    if "$position" in value:
        return old.original.Position(**{
            k: decode(v) for k, v in value["$position"].items()
        })
    if any(k.startswith("$") for k in value):
        raise ValueError("unknown checkpoint value tag")
    return {k: decode(v) for k, v in value.items()}


def key(case: str, mode: str) -> str:
    return case + KEY_SEP + mode


def new_books(side: str, schedule: dict) -> dict:
    return {
        key(case, mode): old.Book(
            side, case, mode,
            schedule[int(case.rsplit("B", 1)[1])]
            if case.startswith("W7H24") else [],
        )
        for case in old.SCENARIOS for mode in old.CASH_MODES
    }


def freeze_books(books: dict) -> dict:
    return {
        k: {field: getattr(book, field) for field in BOOK_KEYS}
        for k, book in books.items()
    }


def restore_books(books: dict, saved: dict) -> None:
    if set(books) != set(saved):
        raise ValueError("checkpoint scenario/cash-mode keys changed")
    for k, book in books.items():
        if set(saved[k]) != set(BOOK_KEYS):
            raise ValueError("checkpoint book schema mismatch")
        for field in BOOK_KEYS:
            setattr(book, field, saved[k][field])


def day_date(index: int) -> str:
    if not 0 <= index < TOTAL_DAYS:
        raise ValueError("2025 day index out of scope")
    return (old.EVAL_START + timedelta(days=index)).date().isoformat()


def part_name(index: int) -> str:
    return f"day_{index:03d}_{day_date(index)}"


def pin_current(expected: dict[str, str], first_head: str) -> str:
    if set(expected) != set(PINNED):
        raise ValueError("original seven research source pin keys differ")
    end_head = old.unit.verify_source_fast_forward(
        first_head, {p: expected[p] for p in old.unit.PINNED_SOURCE_PATHS}
    )
    for rel in (old.SELF_REL, SELF_REL):
        if old.sha(SOURCE / rel) != expected[rel]:
            raise RuntimeError("2025 portfolio/reader source bytes changed")
    return end_head


def writer_dir(path: Path) -> None:
    if path.is_symlink():
        raise PermissionError("research path must not be symlink")
    if not path.exists():
        path.mkdir(mode=0o2770)
    if path.stat().st_uid != 107 or not os.access(path, os.W_OK | os.X_OK):
        raise PermissionError("research parent lacks cripta write/traverse access")


def fsync_dir(path: Path) -> None:
    # Windows CI can exercise pure resume tests; production writer is Linux.
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_sync(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def commit_bundle(parent: Path, name: str, payloads: dict[str, str]) -> Path:
    """Atomic directory promotion; interrupted .pending dirs never count."""
    dest = parent / name
    if dest.exists() or dest.is_symlink():
        raise FileExistsError("immutable 2025 checkpoint/result already exists")
    tmp = Path(tempfile.mkdtemp(prefix=".pending_", dir=str(parent)))
    for file, content in payloads.items():
        if "/" in file or file.startswith("."):
            raise ValueError("invalid checkpoint filename")
        write_sync(tmp / file, content)
    fsync_dir(tmp)
    if dest.exists() or dest.is_symlink():
        raise FileExistsError("checkpoint publication race")
    tmp.rename(dest)
    fsync_dir(parent)
    return dest


def load_parts(
    folder: Path, data: dict | None = None,
) -> tuple[dict | None, dict | None, str | None]:
    """Verify every immutable manifest chain and the final state SHA."""
    if not folder.exists():
        return None, None, None
    entries = list(folder.iterdir())
    parts = sorted(
        (p for p in entries if p.name.startswith("day_")),
        key=lambda p: p.name,
    )
    unexpected = [
        p.name for p in entries
        if not p.name.startswith("day_")
        and not p.name.startswith(".pending_")
        and p.name != ".writer.lock"
    ]
    if unexpected:
        raise ValueError("unrecognized durable research files: " + repr(unexpected))
    last_manifest = None
    prev_hash = None
    state = None
    first_source = None
    first_head = None
    for i, path in enumerate(parts):
        if i >= TOTAL_DAYS or path.name != part_name(i) or path.is_symlink():
            raise ValueError("missing/reordered/symlink checkpoint day")
        m_path = path / "manifest.json"
        s_path = path / "state.json"
        m = json.loads(m_path.read_text(encoding="utf-8"))
        if (
            m["status"] != STATUS or m["index"] != i
            or m["day"] != day_date(i)
            or m["previous_manifest_sha256"] != prev_hash
            or m["state_sha256"] != old.sha(s_path)
            or (data is not None and m["side"] != folder.name.upper())
        ):
            raise ValueError("corrupt checkpoint identity/chain/state hash")
        if data is not None:
            expected = {
                s: data[s]["manifest"]["inputs"][10 + i]["sha256"]
                for s in old.SYMBOLS
            }
            expected_rows = {
                s: int(data[s]["manifest"]["inputs"][10 + i]["rows"])
                for s in old.SYMBOLS
            }
            if m["day_input_sha256"] != expected or m["day_rows"] != expected_rows:
                raise ValueError("completed-day input hash/row provenance mismatch")
            if first_source is None:
                first_source = m["source_pin_sha256"]
                first_head = m["source_head_first"]
            elif (
                m["source_pin_sha256"] != first_source
                or m["source_head_first"] != first_head
            ):
                raise ValueError("checkpoint chain mixed research source versions")
        prev_hash = old.sha(m_path)
        last_manifest = m
        if i == len(parts) - 1:
            state = decode(json.loads(s_path.read_text(encoding="utf-8")))
            if state["next_day"] != i + 1 or state["ticks"] != m["ticks_total"]:
                raise ValueError("checkpoint cumulative progress mismatch")
    return state, last_manifest, prev_hash


def blank_state(data: dict) -> dict:
    return {
        "next_day": 0, "ticks": 0, "previous_day": None, "prices": {},
        "next_seq": {
            s: sum(int(row["rows"]) for row in data[s]["manifest"]["inputs"][:10])
            for s in old.SYMBOLS
        },
        "seen_events": {s: 0 for s in old.SYMBOLS},
        "shadow_pos": dict.fromkeys(old.SYMBOLS),
        "shadow_rows": {s: [] for s in old.SYMBOLS},
        "books": {},
    }


def native_day(data: dict, index: int, first_seq: int):
    """Read and verify one original gzip UTC-day; keep full global source-order."""
    symbol = data["symbol"]
    row = data["manifest"]["inputs"][10 + index]
    day = day_date(index)
    if row["file"] != f"{symbol}{day}.csv.gz":
        raise ValueError("UTC day/input manifest misaligned")
    path = ROOT / "inputs" / symbol / "public_trades" / row["file"]
    if old.sha(path) != row["sha256"]:
        raise ValueError("original Bybit daily SHA mismatch " + symbol + day)
    day_start = old.START_TS + index * 86400
    seq = first_seq
    count = 0
    last_ts = None
    with gzip.open(path, "rt", newline="") as f:
        for raw in csv.DictReader(f):
            seq += 1
            count += 1
            ts = float(raw["timestamp"])
            if not day_start <= ts < day_start + 86400:
                raise ValueError("native print outside exact UTC day")
            if last_ts is not None and ts < last_ts:
                raise ValueError("original native source order regression")
            last_ts = ts
            price = D(raw["price"])
            events = data["events"].get(seq, ())
            for event in events:
                if (
                    event["trade_id"] != raw["trdMatchID"]
                    or float(event["ts"]) != ts
                    or D(event["price"]) != price
                ):
                    raise ValueError("candidate/native source-order identity mismatch")
            yield ts, seq, price, raw["trdMatchID"]
    if count != int(row["rows"]):
        raise ValueError("2025 daily source row coverage mismatch")
    data["day_rows"] = count
    data["last_seq"] = seq


def replay_day(index: int, state: dict, data: dict, books: dict, side: str) -> dict:
    """Exact v1 same-timestamp ordering and cross-UTC-day position continuity."""
    streams = {s: native_day(data[s], index, state["next_seq"][s]) for s in old.SYMBOLS}
    heap = []
    for priority, symbol in enumerate(old.SYMBOLS):
        tick = next(streams[symbol], None)
        if tick is None:
            raise ValueError("missing native 2025 daily prints " + symbol)
        heapq.heappush(heap, (tick[0], priority, tick))
    day_rows = {s: 0 for s in old.SYMBOLS}
    started = time.monotonic()
    last_beat = started
    while heap:
        ts, priority, (_, seq, price, _) = heapq.heappop(heap)
        symbol = old.SYMBOLS[priority]
        events = data[symbol]["events"].get(seq, ())
        bucket = int(ts // 300) * 300
        geometry = data[symbol]["states"].get(bucket)
        if geometry is None:
            raise ValueError("2025 frozen causal L5-3 state missing")
        utc_day = int(ts // 86400)
        if utc_day != state["previous_day"]:
            if state["previous_day"] is not None:
                for book in books.values():
                    book.mark(state["previous_day"] * 86400 + 86400, state["prices"])
            state["previous_day"] = utc_day
        state["prices"][symbol] = price
        current = state["shadow_pos"][symbol]
        just_closed = False
        if current is not None:
            opposite = geometry[1] if current.side == "LONG" else geometry[0]
            new_position, exit_result = old.original.on_trade(
                current, price, seq, bucket, opposite, "TRAIL035"
            )
            state["shadow_pos"][symbol] = new_position
            if exit_result is not None:
                reason, exit_price, maker = exit_result
                gross, ef, xf, net = old.original.gross_and_net(
                    current, exit_price, maker
                )
                record = old.original.trade_record(
                    symbol, "PREV7D", "TRAIL035", current, seq, ts,
                    exit_price, reason, maker, gross, ef, xf, net
                )
                record["research_direction"] = side.split("_")[0]
                state["shadow_rows"][symbol].append(record)
                just_closed = True
        for event in events:
            if event["side"] != side.split("_")[0] or not event["PREV7D"]:
                continue
            if state["shadow_pos"][symbol] is not None or just_closed:
                continue
            position = old.original.initial_position(
                event, seq, bucket, "PREV7D", "TRAIL035"
            )
            opposite = geometry[1] if position.side == "LONG" else geometry[0]
            if old.original.take_profit_eligible(position.side, price, opposite):
                position.tp, position.tp_live_after_seq = opposite, seq
            state["shadow_pos"][symbol] = position
        for book in books.values():
            if events or book.pos[symbol] is not None:
                book.handle(symbol, ts, seq, price, events, geometry, priority)
        day_rows[symbol] += 1
        state["ticks"] += 1
        state["seen_events"][symbol] += len(events)
        if time.monotonic() - last_beat >= 20:
            elapsed = time.monotonic() - started
            processed = sum(day_rows.values())
            total = sum(int(data[s]["manifest"]["inputs"][10 + index]["rows"])
                        for s in old.SYMBOLS)
            eta = elapsed * (total - processed) / processed if processed else None
            print(json.dumps({
                "stage": "2025_PORTFOLIO_DAY_HEARTBEAT", "side": side,
                "day": day_date(index), "day_index": index + 1,
                "days_total": TOTAL_DAYS,
                "processed_today": processed, "total_today": total,
                "percent_today": round(100 * processed / total, 3),
                "processed_total": state["ticks"],
                "elapsed_sec": round(elapsed, 1),
                "eta_day_sec": round(eta, 1) if eta is not None else None,
            }), flush=True)
            last_beat = time.monotonic()
        nxt = next(streams[symbol], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], priority, nxt))
    if state["next_day"] != index:
        raise ValueError("checkpoint day counter not contiguous")
    for symbol in old.SYMBOLS:
        row = data[symbol]["manifest"]["inputs"][10 + index]
        if (
            data[symbol].get("day_rows") != int(row["rows"])
            or day_rows[symbol] != int(row["rows"])
            or data[symbol]["last_seq"] != state["next_seq"][symbol] + day_rows[symbol]
        ):
            raise ValueError("one-day Bybit row/sequence parity mismatch")
        state["next_seq"][symbol] = data[symbol]["last_seq"]
    state["next_day"] += 1
    state["books"] = freeze_books(books)
    return day_rows


def make_part(
    folder: Path, index: int, state: dict, day_rows: dict,
    data: dict, previous_sha: str, first_head: str, source_pins: dict,
    unit_pins: dict, end_head: str,
) -> tuple[dict, str]:
    state_text = json.dumps(encode(state), sort_keys=True, separators=(",", ":")) + "\n"
    day_input = {
        s: data[s]["manifest"]["inputs"][10 + index]["sha256"]
        for s in old.SYMBOLS
    }
    # The state hash is computed on the exact bytes that will be published.
    state_hash = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
    manifest = {
        "research": "P90L90_BULL2025_PORTFOLIO_CHUNKED_V2",
        "status": STATUS, "index": index, "day": day_date(index),
        "days_done": index + 1, "days_total": TOTAL_DAYS,
        "ticks_total": state["ticks"], "day_rows": day_rows,
        "day_input_sha256": day_input,
        "previous_manifest_sha256": previous_sha,
        "state_sha256": state_hash,
        "source_head_first": first_head,
        "source_head_at_checkpoint": end_head,
        "source_pin_sha256": source_pins,
        "unit_ledger_sha256": unit_pins,
        "side": folder.name.upper(),
        "complete_day": True, "partial_day_resumable": False,
    }
    path = commit_bundle(folder, part_name(index), {
        "state.json": state_text,
        "manifest.json": json.dumps(manifest, sort_keys=True, indent=2) + "\n",
    })
    return manifest, old.sha(path / "manifest.json")


def publish_final(
    side: str, state: dict, data: dict, unit_rows: dict,
    books: dict, schedule: dict, source_pins: dict,
    first_head: str, last_part_sha: str,
) -> dict:
    if state["next_day"] != TOTAL_DAYS:
        raise ValueError("finalization before 183 complete UTC days")
    if sum(state["next_seq"].values()) != 204446371:
        raise ValueError("193-day source row/sequence coverage mismatch")
    for symbol in old.SYMBOLS:
        if state["next_seq"][symbol] != sum(
            int(row["rows"]) for row in data[symbol]["manifest"]["inputs"]
        ):
            raise ValueError("source row coverage mismatch " + symbol)
        if state["seen_events"][symbol] != data[symbol]["manifest"]["events"]:
            raise ValueError("native candidate coverage mismatch " + symbol)
        if state["shadow_rows"][symbol] != unit_rows[symbol]["rows"]:
            raise ValueError("2025 exact per-symbol NO_PAUSE shadow ledger parity " + symbol)
    results = {}
    hashes = {}
    for k, book in books.items():
        results[k] = book.finish(state["prices"], old.END_TS)
    end_head = pin_current(source_pins, first_head)
    writer_dir(FINAL)
    leaf = FINAL / side.lower()
    if leaf.exists() or leaf.is_symlink():
        raise FileExistsError("existing 2025 immutable portfolio result")
    tmp = Path(tempfile.mkdtemp(prefix=".pending_", dir=str(FINAL)))
    for k, book in books.items():
        ledger = tmp / f"ledger_{k}.jsonl"
        with ledger.open("x", encoding="utf-8") as f:
            for item in book.trades:
                f.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        hashes[k] = old.sha(ledger)
        if len(book.trades) != results[k]["ledger_rows"]:
            raise ValueError("final ledger row parity failure")
    snap = tmp / "source_snapshot"
    snap.mkdir(mode=0o2770)
    source_hashes = {}
    for rel in PINNED:
        path = SOURCE / rel
        copy = snap / path.name
        shutil.copyfile(path, copy)
        digest = old.sha(copy)
        if digest != source_pins[rel] or old.sha(path) != digest:
            raise RuntimeError("research Python byte identity changed during publish")
        source_hashes[path.name] = digest
    report = {
        "research": "P90L90_BULL2025_PORTFOLIO_CHUNKED_V2",
        "status": "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
        "evaluation_utc": [old.EVAL_START.isoformat(), old.EVAL_END.isoformat()],
        "evaluation_days": TOTAL_DAYS, "source_input_days": 193,
        "source_rows_including_warmup": 204446371,
        "native_ticks_evaluation": state["ticks"],
        "symbols": list(old.SYMBOLS), "side": side,
        "start_wallet_each_case_usdt": str(old.START_WALLET),
        "fixed_margin_usdt": str(old.FIXED_MARGIN), "leverage": str(old.LEVERAGE),
        "fee_taker": str(old.TAKER), "fee_maker_resting_tp": str(old.MAKER),
        "cash_modes": list(old.CASH_MODES), "cases": list(old.SCENARIOS),
        "compound_margin_rule": "min(20% of current wallet, fee-covered available margin)",
        "source_head_first": first_head, "source_head_at_publication": end_head,
        "source_fastforward_only": True,
        "source_pin_files_sha256": source_pins,
        "source_sha256": source_hashes,
        "unit_shadow_sha256": {s: unit_rows[s]["sha"] for s in old.SYMBOLS},
        "unlimited_no_pause_per_coin_shadow_parity": True,
        "checkpoint_days": TOTAL_DAYS, "checkpoint_last_manifest_sha256": last_part_sha,
        "checkpoint_root": str(PARTS / side.lower()),
        "W7": {
            "thresholds_bps": list(old.THRESHOLDS),
            "min_prior_complete_7d_closes": 20, "pause_hours": 24,
            "trigger_only_new_no_pause_shadow_hardstop": True,
            "number_signals_by_threshold": {str(k): len(v) for k, v in schedule.items()},
        },
        "SECOND48": {
            "trigger": ">=2 actual scenario hardstops in previous rolling 48 hours",
            "embargo": "new entries only 48 hours, no forced flatten",
        },
        "censored_not_forced_closed": True,
        "daily_drawdown_is_eod_sampled_not_intraday": True,
        "unmodeled": [
            "funding", "bid_ask_spread", "slippage", "latency",
            "tick_size_and_lot_size_rounding", "exchange_liquidation",
            "orderbook_queue", "actual_venue_order_fills",
        ],
        "results": results, "ledger_sha256": hashes,
    }
    write_sync(tmp / "result.json", json.dumps(report, sort_keys=True, indent=2) + "\n")
    qc = {
        "status": "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
        "side": side, "native_ticks": state["ticks"],
        "result_sha256": old.sha(tmp / "result.json"),
        "ledger_sha256": hashes,
        "checkpoint_days": TOTAL_DAYS,
        "checkpoint_last_manifest_sha256": last_part_sha,
        "no_pause_shadow_parity": True, "source_head_at_publication": end_head,
        "source_pin_files_sha256": source_pins,
    }
    write_sync(tmp / "qc.json", json.dumps(qc, sort_keys=True, indent=2) + "\n")
    fsync_dir(tmp)
    fsync_dir(snap)
    if leaf.exists() or leaf.is_symlink():
        raise FileExistsError("concurrent result published")
    tmp.rename(leaf)
    fsync_dir(FINAL)
    print(json.dumps({
        "stage": "2025_PORTFOLIO_FULL_INTERNAL_QC",
        "side": side, "native_ticks": state["ticks"],
        "result": str(leaf / "result.json"),
        "independent_qc": "PENDING",
    }), flush=True)
    return report


def run(side: str, source_head: str, max_days: int = 5) -> dict:
    if side not in old.SIDES or not 1 <= max_days <= 10:
        raise ValueError("invalid 2025 side or bounded day count")
    if os.geteuid() != 107 or old.entry.git_head() != source_head:
        raise PermissionError("exact active GitHub main/actor cripta UID107 required")
    if old.sha(SOURCE / SELF_REL) == "":
        raise RuntimeError("reader source pin missing")
    current_pins = {rel: old.sha(SOURCE / rel) for rel in PINNED}
    if len(set(PINNED)) != len(PINNED):
        raise ValueError("duplicate research source pin paths")
    data, unit_rows, _ = old.verify_and_load(side, source_head)
    schedule = old.shadow_schedule(unit_rows, side)
    writer_dir(PARTS)
    folder = PARTS / side.lower()
    writer_dir(folder)
    lock_path = folder / ".writer.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        flock = importlib.import_module("fcntl")  # Linux server only
        try:
            flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another active 2025 portfolio writer owns this side") from exc
        state, last_manifest, last_sha = load_parts(folder, data)
        source_pins = current_pins if last_manifest is None else last_manifest["source_pin_sha256"]
        first_head = source_head if last_manifest is None else last_manifest["source_head_first"]
        if current_pins != source_pins:
            raise RuntimeError("2025 resume rejected: effective research source bytes differ")
        pin_current(source_pins, first_head)
        unit_pins = {s: unit_rows[s]["sha"] for s in old.SYMBOLS}
        if last_manifest is not None and last_manifest["unit_ledger_sha256"] != unit_pins:
            raise RuntimeError("2025 immutable per-symbol UNIT ledger changed")
        books = new_books(side, schedule)
        if state is None:
            state = blank_state(data)
        else:
            restore_books(books, state["books"])
        start_day = state["next_day"]
        if not 0 <= start_day <= TOTAL_DAYS:
            raise ValueError("checkpoint day counter outside 2025 evaluation")
        # No result overwrite: completed checkpoint can only finalize once.
        final_leaf = FINAL / side.lower()
        if final_leaf.exists() or final_leaf.is_symlink():
            raise FileExistsError("2025 full result already exists")
        started = time.monotonic()
        target = min(TOTAL_DAYS, start_day + max_days)
        for i in range(start_day, target):
            rows = replay_day(i, state, data, books, side)
            end_head = pin_current(source_pins, first_head)
            _, last_sha = make_part(
                folder, i, state, rows, data, last_sha,
                first_head, source_pins, unit_pins, end_head,
            )
            if shutil.disk_usage(ROOT).free < 16 * 2**30:
                raise RuntimeError("research data disk fell below 16GiB reserve")
            elapsed = time.monotonic() - started
            total_done = state["next_day"]
            print(json.dumps({
                "stage": "2025_PORTFOLIO_DAY_CHECKPOINT_QC_PASS",
                "side": side, "day": day_date(i), "day_index": total_done,
                "days_total": TOTAL_DAYS,
                "percent": round(total_done / TOTAL_DAYS * 100, 4),
                "elapsed_sec": round(elapsed, 2),
                "committed_manifest_sha256": last_sha,
                "native_ticks_evaluation": state["ticks"],
                "resume_next_day": (
                    day_date(total_done) if total_done < TOTAL_DAYS else None
                ),
            }), flush=True)
        if state["next_day"] == TOTAL_DAYS:
            result = publish_final(
                side, state, data, unit_rows, books,
                schedule, source_pins, first_head, last_sha,
            )
            return {
                "status": result["status"], "side": side,
                "days_completed": TOTAL_DAYS, "ticks": state["ticks"],
                "checkpoint_manifest_sha256": last_sha,
            }
        return {
            "status": "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL",
            "side": side, "days_completed": state["next_day"],
            "days_total": TOTAL_DAYS, "ticks": state["ticks"],
            "checkpoint_manifest_sha256": last_sha,
            "next_day": day_date(state["next_day"]),
            "independent_portfolio_qc": "NOT_AVAILABLE",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", required=True, choices=old.SIDES)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--max-days", type=int, default=5)
    args = parser.parse_args()
    result = run(args.side, args.source_head, args.max_days)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
