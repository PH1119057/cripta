#!/usr/bin/env python3
"""#633: frozen PREV7D depth=0 width replication on 2026 90d and 29d.

RESEARCH ONLY. No Strategy/DB/Exchange/Production/LIVE mutation.

Owner/actor/path contract (DEV 5.1 and 19):
  EFFECTIVE_ACTOR: cripta UID107:GID109
  READ: versioned detached source tree; immutable 90d/29d P90L90 Entry,
        L5 geometry, source-native Bybit public trade archives, Exit references
  WRITE/CREATE: only the new version-named result child for the selected
        period, checkpoints, status/ledger/QC/source_snapshot within that child
  RENAME: only temporary private checkpoint/status into own result child
  DELETE: none; never overwrite source archives or historical research
  SYSTEMD: separately supervised transient unit, own cgroup, User=cripta,
        ProtectSystem=strict, ReadWritePaths scoped to two results parents
  DB_ROLE/EXCHANGE: none.

Frozen comparison includes nine widths, both isolated directions, NO_PAUSE
and SECOND48. Primary hypotheses are SHORT_ONLY SECOND48 width ±0.80/±0.90;
no future window can alter that pre-registration in Issue #633.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
pause = importlib.import_module("scripts.research.p90l90_entry_only_hardstop_embargo_v1")
previous = pause.previous
sep = pause.sep
base = pause.base

RESEARCH = "P90L90_DEPTHZERO_PREV7D_WIDTH_EXTERNAL_REPLICATION_2026_V1"
LEAF = "p90l90_depthzero_prev7d_width_confirm_20261112_v1"
BANDS = {
    "010": D("0.10"), "020": D("0.20"), "025": D("0.25"),
    "050": D("0.50"), "075": D("0.75"), "080": D("0.80"),
    "090": D("0.90"), "100": D("1.00"), "150": D("1.50"),
}
SIDES = ("LONG_ONLY", "SHORT_ONLY")
MODES = ("NO_PAUSE", "SECOND48")
SYMBOLS = tuple(pause.SYMBOLS)
PERIODS = {
    "90d": {"root": previous.ROOT, "date": "2026-05-18", "days": 90,
            "source_days": 100, "source_start": "2026-05-08"},
    "29d": {"root": pause.sources29.ROOT, "date": "2026-09-21", "days": 29,
            "source_days": 39, "source_start": "2026-09-11"},
}
RESERVE = 5 * 2**30
SELF_REL = "scripts/research/p90l90_depthzero_prev7d_width_confirm_v1.py"
PINNED = (
    SELF_REL,
    "scripts/research/p90l90_entry_only_hardstop_embargo_v1.py",
    "scripts/research/p90l90_portfolio_pause_v1.py",
    "scripts/research/p90l90_29d_exit_replay_v1.py",
    "scripts/research/p90l90_29d_sources_v1.py",
    "scripts/research/p90l90_entry_sep29_v1.py",
    "scripts/research/p90l90_entry_90d_v2.py",
    "scripts/research/p90l90_exit_90d_v2.py",
)
POSITION_DECIMAL_FIELDS = {"entry", "stop", "best_favorable", "tp"}
POSITION_FIELDS = frozenset(base.Position.__dataclass_fields__)
BOOK_NAMES = tuple(
    "w" + width + "_" + side + "_" + mode
    for width in BANDS for side in SIDES for mode in MODES
)
ALL_NAMES = ("REFERENCE",) + BOOK_NAMES


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(2**20), b""):
            h.update(block)
    return h.hexdigest()


def out_dir(period: str) -> Path:
    return PERIODS[period]["root"] / "results" / LEAF


def head() -> str:
    p = subprocess.run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=15, check=True)
    return p.stdout.strip()


def source_identity(expected_head: str) -> dict:
    if len(expected_head) != 40 or head() != expected_head:
        raise ValueError("exact versioned source HEAD mismatch")
    return {rel: sha(SOURCE / rel) for rel in PINNED}


def load_original(period: str) -> tuple[dict, dict]:
    """Use existing 2026 frozen readers, not the 2025 source's 183d geometry."""
    if period == "90d":
        data = {s: previous.inputs(s, previous.RAW_DEFAULT,
                                   previous.FROZEN_DEFAULT) for s in SYMBOLS}
        refs = {s: data[s]["orig_rows"] for s in SYMBOLS}
    else:
        data = {s: sep.read_inputs(s) for s in SYMBOLS}
        refs, _result = pause.read_reference_29()
    return data, refs


def cohort(event: dict, median: D, band: D) -> bool:
    return (D(str(event["depth_pct"])) == 0
            and abs(D(str(event["width_pct"])) - median) <= band)


def validate_and_filter(data: dict, period: str) -> tuple[dict, dict]:
    spec = PERIODS[period]
    beg = datetime.fromisoformat(spec["date"]).replace(tzinfo=UTC)
    first_input = datetime.fromisoformat(spec["source_start"]).replace(tzinfo=UTC)
    eligible_by_symbol = {}
    counts = {width: {side: 0 for side in SIDES} for width in BANDS}
    for symbol in SYMBOLS:
        source = data[symbol]
        manifest = source["manifest"]
        if (manifest.get("symbol") != symbol
                or manifest.get("PREV7D_WIDTH_LOOKAHEAD") is not False
                or not manifest.get("complete_90d_input", manifest.get("complete_29d_input"))
                or len(manifest["inputs"]) != spec["source_days"]
                or len(manifest["medians_PREV7D"]) != spec["days"]):
            raise ValueError("missing exact source/causal median coverage " + symbol)
        medians = {day: D(str(val))
                   for day, val in manifest["medians_PREV7D"].items()}
        days = [(beg + timedelta(days=i)).date().isoformat()
                for i in range(spec["days"])]
        if set(medians) != set(days):
            raise ValueError("incomplete prior-seven-day timeline " + symbol)
        for i, info in enumerate(manifest["inputs"]):
            day = (first_input + timedelta(days=i)).date().isoformat()
            if day not in info["file"] or not int(info["rows"]) > 0:
                raise ValueError("noncontiguous source-day manifest " + symbol)
        filt = {}
        for seq, events in source["events"].items():
            selected = defaultdict(list)
            for event in events:
                if event["symbol"] != symbol or event["side"] not in ("LONG", "SHORT"):
                    raise ValueError("foreign 2026 Entry candidate")
                at = datetime.fromtimestamp(int(event["state_effective_ts"]), UTC)
                day = at.date().isoformat()
                if day not in medians:
                    raise ValueError("candidate outside evaluation/warmup boundary")
                w = D(str(event["width_pct"]))
                m = medians[day]
                depth = D(str(event["depth_pct"]))
                if not w.is_finite() or not m.is_finite() or not depth.is_finite():
                    raise ValueError("invalid width/depth")
                dist = abs(w - m)
                if bool(event["PREV7D"]) != (dist <= BANDS["050"]):
                    raise ValueError("original causal ±0.50 PREV7D parity FAILED")
                if depth != 0:
                    continue
                side = event["side"] + "_ONLY"
                for key, width in BANDS.items():
                    if dist <= width:
                        counts[key][side] += 1
                        for mode in MODES:
                            selected["w" + key + "_" + side + "_" + mode].append(
                                {**event, "PREV7D": True}
                            )
            if selected:
                filt[int(seq)] = {k: tuple(v) for k, v in selected.items()}
        eligible_by_symbol[symbol] = filt
    for side in SIDES:
        prior = -1
        for key in BANDS:
            count = counts[key][side]
            if count < prior:
                raise ValueError("nested width cohorts not monotone")
            prior = count
    return eligible_by_symbol, counts


def preflight(period: str, expected_head: str) -> tuple[dict, dict, dict, dict, dict]:
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("research requires actual cripta UID107:GID109")
    if period not in PERIODS:
        raise ValueError("unknown period")
    p = out_dir(period)
    parent = p.parent
    if (parent.is_symlink() or not parent.is_dir()
            or parent.stat().st_uid != 107
            or not os.access(parent, os.W_OK | os.X_OK)):
        raise PermissionError("research results parent not writable by cripta")
    if p.is_symlink():
        raise PermissionError("research output symlink denied")
    if shutil.disk_usage(parent).free < RESERVE:
        raise RuntimeError("data disk below owner-approved 5GiB reserve")
    pins = source_identity(expected_head)
    data, references = load_original(period)
    filters, counts = validate_and_filter(data, period)
    return pins, data, references, filters, counts


def make_books() -> dict:
    books = {"REFERENCE": pause.EntryEmbargo(0, None)}
    for code in BANDS:
        for side in SIDES:
            for mode in MODES:
                books["w" + code + "_" + side + "_" + mode] = (
                    pause.EntryEmbargo(0, None) if mode == "NO_PAUSE"
                    else pause.EntryEmbargo(2, 48)
                )
    return books


def freeze_book(book) -> dict:
    pos = {}
    for symbol, p in book.pos.items():
        if p is None:
            pos[symbol] = None
        else:
            pos[symbol] = {
                k: (str(v) if isinstance(v, D) else v)
                for k, v in asdict(p).items()
            }
    return {
        "pos": pos, "pending": sorted(book.pending),
        "pause_until": repr(book.pause_until),
        "count": dict(book.count),
        "money": {k: str(v) for k, v in book.money.items()},
        "trades": book.trades, "stops": book.stops,
        "last_stops": list(book.last_stops), "all_stops": book.all_stops,
    }


def restore_book(book, saved: dict) -> None:
    if set(saved["pos"]) != set(SYMBOLS):
        raise ValueError("checkpoint symbol/position identity mismatch")
    restored = {}
    for symbol, rec in saved["pos"].items():
        if rec is None:
            restored[symbol] = None
        else:
            if set(rec) != POSITION_FIELDS:
                raise ValueError("checkpoint Position schema mismatch")
            val = {k: (D(v) if k in POSITION_DECIMAL_FIELDS and v is not None else v)
                   for k, v in rec.items()}
            restored[symbol] = base.Position(**val)
    book.pos = restored
    book.pending = set(saved["pending"])
    book.pause_until = float(saved["pause_until"])
    book.count = Counter(saved["count"])
    book.money = defaultdict(lambda: D(0),
                             {k: D(v) for k, v in saved["money"].items()})
    book.trades = saved["trades"]
    book.stops = saved["stops"]
    book.last_stops = deque(saved["last_stops"])
    book.all_stops = saved["all_stops"]


def pack_snapshot(snapshot: dict) -> bytes:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    return gzip.compress(payload, compresslevel=5, mtime=0)


def unpack_snapshot(raw: bytes) -> dict:
    return json.loads(gzip.decompress(raw))


def write_immutable(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("immutable checkpoint collision: " + str(path))
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError("orphaned checkpoint temp, stop for forensic")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise FileExistsError("checkpoint destination race")
    temporary.rename(path)


def atomic_status(root: Path, status: dict) -> None:
    target = root / "status.json"
    tmp = root / "status.json.tmp"
    if tmp.exists() or tmp.is_symlink():
        raise FileExistsError("unreconciled status temporary file")
    with tmp.open("x", encoding="utf-8") as fh:
        json.dump(status, fh, sort_keys=True, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(target)


def checkpoint_root(root: Path) -> Path:
    return root / "checkpoints"


def scan_checkpoints(root: Path, pins: dict, period: str) -> tuple[dict | None, str]:
    parent = checkpoint_root(root)
    if not parent.exists():
        return None, ""
    previous_hash = ""
    latest = None
    for day in range(1, PERIODS[period]["days"] + 1):
        file = parent / ("day_%03d.json.gz" % day)
        if not file.exists():
            if any(parent.glob("day_*.json.gz")):
                if any(int(x.stem.split(".")[0].split("_")[1]) > day
                       for x in parent.glob("day_*.json.gz")):
                    raise ValueError("gapped checkpoint chain")
            break
        if file.is_symlink():
            raise PermissionError("checkpoint symlink denied")
        raw = file.read_bytes()
        item = unpack_snapshot(raw)
        if (item["research"] != RESEARCH or item["period"] != period
                or item["pins"] != pins or item["days_done"] != day
                or item["previous_checkpoint_sha256"] != previous_hash
                or set(item["books"]) != set(ALL_NAMES)):
            raise ValueError("checkpoint source/day/hash chain corrupt")
        previous_hash = hashlib.sha256(raw).hexdigest()
        latest = item
    return latest, previous_hash


def day_native_ticks(period: str, data: dict, symbol: str, day: int):
    """Iterate exactly ONE evaluation UTC day with original global source order."""
    spec = PERIODS[period]
    item_index = 10 + day
    manifest = data["manifest"]
    item = manifest["inputs"][item_index]
    offset = sum(int(x["rows"]) for x in manifest["inputs"][:item_index])
    if period == "90d":
        root = data["raw_root"]
    else:
        root = PERIODS[period]["root"] / "inputs"
    file = root / symbol / "public_trades" / item["file"]
    if sha(file) != item["sha256"]:
        raise ValueError("native source day SHA mismatch " + str(file))
    begin = datetime.fromisoformat(spec["date"]).replace(tzinfo=UTC)
    ts_begin = int((begin + timedelta(days=day)).timestamp())
    counter = 0
    last_ts = float("-inf")
    with gzip.open(file, "rt", newline="") as stream:
        source = csv.DictReader(stream)
        required = {"timestamp", "symbol", "price", "trdMatchID"}
        if not required.issubset(set(source.fieldnames or [])):
            raise ValueError("source Bybit CSV schema mismatch")
        for record in source:
            counter += 1
            seq = offset + counter
            t = float(record["timestamp"])
            if (not ts_begin <= t < ts_begin + 86400
                    or record["symbol"] != symbol or t < last_ts):
                raise ValueError("out-of-day/source chronology mismatch")
            last_ts = t
            px = D(record["price"])
            original = data["events"].get(seq, ())
            for ev in original:
                if (ev["trade_id"] != record["trdMatchID"] or
                        float(ev["ts"]) != t or D(ev["price"]) != px):
                    raise ValueError("candidate/source tick identity mismatch")
            yield t, seq, px
    if counter != int(item["rows"]):
        raise ValueError("source daily row count drift")


def replay_day(period: str, day: int, data: dict, filters: dict,
               books: dict, seen: dict, smoke_seconds: int = 0) -> dict:
    spec = PERIODS[period]
    streams = {symbol: day_native_ticks(period, data[symbol], symbol, day)
               for symbol in SYMBOLS}
    queue = []
    for priority, symbol in enumerate(SYMBOLS):
        tick = next(streams[symbol], None)
        if tick is None:
            raise ValueError("empty source daily symbol: " + symbol)
        heapq.heappush(queue, (tick[0], priority, tick))
    active = {symbol: {name for name, book in books.items()
                       if book.pos[symbol] is not None}
              for symbol in SYMBOLS}
    ticks = 0
    started = time.monotonic()
    recent = started
    start_ts = int(datetime.fromisoformat(spec["date"]).replace(tzinfo=UTC).timestamp())
    while queue:
        t, priority, (timestamp, seq, px) = heapq.heappop(queue)
        if smoke_seconds and t >= start_ts + smoke_seconds:
            return {"ticks": ticks, "smoke_partial": True}
        symbol = SYMBOLS[priority]
        raw_events = data[symbol]["events"].get(seq, ())
        cand = filters[symbol].get(seq, {})
        interested = active[symbol] | set(cand)
        if raw_events:
            interested.add("REFERENCE")
            seen[symbol] += len(raw_events)
        if interested:
            state = data[symbol]["states"].get(int(t // 300) * 300)
            for name in interested:
                events = raw_events if name == "REFERENCE" else cand.get(name, ())
                book = books[name]
                book.handle(symbol, t, seq, px, events, state)
                if book.pos[symbol] is not None:
                    active[symbol].add(name)
                else:
                    active[symbol].discard(name)
        ticks += 1
        if ticks % 200000 == 0 and time.monotonic() - recent >= 20:
            recent = time.monotonic()
            print(json.dumps({"stage": "HEARTBEAT", "period": period,
                              "day_index": day + 1, "ticks_day": ticks,
                              "elapsed_sec": round(recent - started, 1)}), flush=True)
        nxt = next(streams[symbol], None)
        if nxt is not None:
            heapq.heappush(queue, (nxt[0], priority, nxt))
    return {"ticks": ticks, "smoke_partial": False}


def report_and_qc(period: str, root: Path, books: dict, data: dict,
                  references: dict, counts: dict, seen: dict, pins: dict,
                  last_checkpoint_sha: str) -> dict:
    """Complete historical replay and write only after full source/reference QC."""
    if not all(seen[s] == sum(map(len, data[s]["events"].values())) for s in SYMBOLS):
        raise ValueError("not all frozen Entry events consumed by native replay")
    control = books["REFERENCE"]
    reference_rows = {symbol: [] for symbol in SYMBOLS}
    for row in control.trades:
        reference_rows[row["symbol"]].append(row)
    if reference_rows != references:
        raise ValueError("REFERENCE original 2026 trade-by-trade parity FAILED")
    result = {}
    money = {}
    for name, book in books.items():
        details = book.finish()
        if name != "REFERENCE":
            _, band, side, mode = name.split("_", 3)
            # side is either LONG_ONLY / SHORT_ONLY, cannot naively split on "_".
            # Parse fixed names instead below.
            band = name[1:4]
            side = "LONG_ONLY" if "_LONG_ONLY_" in name else "SHORT_ONLY"
            if details["counts"].get("qualified_candidate", 0) != counts[band][side]:
                raise ValueError("qualified Entry count does not match frozen cohort")
        rows = book.trades
        gross = sum((D(row["gross_usdt"]) for row in rows), D(0))
        fees_entry = sum((D(row["entry_fee_usdt"]) for row in rows), D(0))
        fees_exit = sum((D(row["exit_fee_usdt"]) for row in rows), D(0))
        net = sum((D(row["net_usdt"]) for row in rows), D(0))
        if (gross - fees_entry - fees_exit != net
                or net != D(details["money"]["net_usdt"])
                or len(rows) != details["counts"].get("closed", 0)):
            raise ValueError("independent modeled fee/ledger reconciliation failed")
        tt_extra = sum((
            (base.NOMINAL * D(row["exit_price"]) / D(row["entry_price"])
             * (base.TAKER - base.MAKER)).quantize(
                base.MONEY_QUANTUM, rounding=ROUND_HALF_EVEN
            )
            for row in rows if row["maker_exit"]
        ), D(0))
        net_tt = net - tt_extra
        by_coin = {}
        for symbol in SYMBOLS:
            sr = [x for x in rows if x["symbol"] == symbol]
            by_coin[symbol] = {
                "closed": len(sr),
                "net_after_modeled_fees_usdt": str(sum(
                    (D(x["net_usdt"]) for x in sr), D(0))),
                "hardstops": sum(x["exit_reason"] == "HARD_STOP" for x in sr),
            }
        result[name] = {
            "counts": details["counts"], "money": details["money"],
            "closed_trades": len(rows), "censored": details["censored"],
            "net_after_modeled_fees_usdt": str(net),
            "net_taker_taker_sensitivity_usdt": str(net_tt),
            "by_coin": by_coin,
        }
        money[name] = str(net)
    baseline = result["REFERENCE"]
    result["REFERENCE"]["original_trade_by_trade_parity"] = True
    report = {
        "status": "COMPLETE_PENDING_EXTERNAL_INDEPENDENT_QC",
        "research": RESEARCH, "period": period,
        "dates_utc": [PERIODS[period]["date"],
                      (datetime.fromisoformat(PERIODS[period]["date"]) +
                       timedelta(days=PERIODS[period]["days"])).date().isoformat()],
        "days": PERIODS[period]["days"], "symbols": list(SYMBOLS),
        "bands_pp": {k: str(v) for k, v in BANDS.items()},
        "primary_hypotheses": ["w080_SHORT_ONLY_SECOND48",
                               "w090_SHORT_ONLY_SECOND48"],
        "entry": "frozen P90L90 PREV7D first crossing; RAW H9 intended-side depth exactly 0; width deviation <= band pp",
        "exit": "original dynamic RAW L5-3 opposite TP, TRAIL035, hard stop -10%; second hard stop within previous 48h -> new Entry only 48h pause",
        "notional_per_symbol_usdt": "10",
        "portfolio_notional_usdt": "50",
        "fees": {"taker": str(base.TAKER), "maker_resting_tp": str(base.MAKER)},
        "execution": "original modeled native Bybit source tick; marketable taker and resting maker only",
        "unmodeled": ["spread", "slippage", "funding", "latency", "real fills"],
        "post_holdout_replicative_not_pristine_blinded_oos": True,
        "source_head": head(), "pinned_code_sha256": pins,
        "candidate_source_sha256_by_symbol": {
            s: data[s]["manifest"]["events_sha256"] for s in SYMBOLS},
        "cohort_candidate_counts": counts,
        "source_events_seen": seen,
        "checkpoint_last_sha256": last_checkpoint_sha,
        "reference_baseline_trade_by_trade_parity": baseline["original_trade_by_trade_parity"],
        "cases": result,
    }
    ledger = root / "trade_ledger.jsonl"
    if ledger.exists():
        raise FileExistsError("immutable ledger collision")
    with ledger.open("x", encoding="utf-8") as fh:
        for name in ALL_NAMES:
            for row in books[name].trades:
                fh.write(json.dumps({**row, "replication_scenario": name},
                                    sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    report["ledger_sha256"] = sha(ledger)
    file = root / "result.json"
    if file.exists():
        raise FileExistsError("immutable result already exists")
    with file.open("x", encoding="utf-8") as fh:
        json.dump(report, fh, sort_keys=True, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {"status": report["status"], "period": period,
            "result_path": str(file), "ledger_sha256": report["ledger_sha256"],
            "primary_net": {k: money[k] for k in report["primary_hypotheses"]}}


def run_period(period: str, expected_head: str, mode: str,
               smoke_hours: int = 2) -> dict:
    pins, data, references, filters, counts = preflight(period, expected_head)
    if mode == "preflight":
        return {"stage": "PREPARED_READ_ONLY", "period": period,
                "source_head": expected_head, "source_sha256": pins,
                "eligible_candidates": counts,
                "free_gib": round(shutil.disk_usage(out_dir(period).parent).free / 2**30, 3)}
    books = make_books()
    seen = {s: 0 for s in SYMBOLS}
    root = out_dir(period)
    if mode == "smoke":
        result = replay_day(period, 0, data, filters, books, seen,
                            smoke_seconds=smoke_hours * 3600)
        return {"stage": "SMOKE_PASS_NO_MUTATION", "period": period,
                "hours": smoke_hours, "source_ticks": result["ticks"],
                "source_events_seen": seen, "source_head": expected_head}
    if mode != "run":
        raise ValueError("unsupported mode")
    if not root.exists():
        root.mkdir(mode=0o2770)
        checkpoint_root(root).mkdir(mode=0o2770)
        snap = root / "source_snapshot"
        snap.mkdir(mode=0o2770)
        for rel in PINNED:
            src = SOURCE / rel
            target = snap / src.name
            with target.open("xb") as fh:
                fh.write(src.read_bytes())
            if sha(target) != pins[rel]:
                raise ValueError("source snapshot digest mismatch")
        (root / "manifest.json").write_text(json.dumps({
            "status": "SOURCE_FROZEN", "research": RESEARCH,
            "period": period, "source_head": expected_head, "pins": pins,
            "candidate_counts": counts, "planned_days": PERIODS[period]["days"],
            "data_root": str(PERIODS[period]["root"]),
            "owner": "cripta", "minimum_free_gib": 5,
        }, sort_keys=True, indent=2) + "\n")
    else:
        if root.is_symlink() or not (root / "manifest.json").is_file():
            raise ValueError("unrecognized existing research run root")
        manifest = json.loads((root / "manifest.json").read_text())
        if (manifest.get("research") != RESEARCH
                or manifest.get("source_head") != expected_head
                or manifest.get("pins") != pins
                or manifest.get("candidate_counts") != counts):
            raise ValueError("resume source/contract identity mismatch")
        if (root / "result.json").exists():
            raise FileExistsError("already finalized; no duplicate research writer")
    # O_EXCL nonblocking flock prevents duplicate services writing one result root.
    fcntl = importlib.import_module("fcntl")
    with (root / ".writer.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("existing worker owns result lock") from exc
        previous_checkpoint, last_hash = scan_checkpoints(root, pins, period)
        start_day = 0
        if previous_checkpoint is not None:
            start_day = previous_checkpoint["days_done"]
            if len(previous_checkpoint["books"]) != len(books):
                raise ValueError("checkpoint scenario count drift")
            for name, book in books.items():
                restore_book(book, previous_checkpoint["books"][name])
            seen = previous_checkpoint["source_events_seen"]
        for day in range(start_day, PERIODS[period]["days"]):
            disk = shutil.disk_usage(root)
            if disk.free < RESERVE:
                raise RuntimeError("5 GiB data disk reserve reached, checkpoint preserved")
            print(json.dumps({"stage": "DAY_START", "period": period, "day": day + 1,
                              "days_total": PERIODS[period]["days"]}), flush=True)
            result = replay_day(period, day, data, filters, books, seen)
            snapshot = {
                "research": RESEARCH, "period": period, "days_done": day + 1,
                "pins": pins, "previous_checkpoint_sha256": last_hash,
                "source_events_seen": seen,
                "books": {name: freeze_book(book) for name, book in books.items()},
            }
            blob = pack_snapshot(snapshot)
            target = checkpoint_root(root) / ("day_%03d.json.gz" % (day + 1))
            write_immutable(target, blob)
            last_hash = hashlib.sha256(blob).hexdigest()
            atomic_status(root, {
                "stage": "RUNNING", "research": RESEARCH, "period": period,
                "source_head": expected_head, "days_done": day + 1,
                "days_total": PERIODS[period]["days"],
                "last_checkpoint_sha256": last_hash,
                "disk_free_gib": round(shutil.disk_usage(root).free / 2**30, 3),
            })
            print(json.dumps({"stage": "DAY_COMMITTED", "period": period,
                              "day": day + 1, "days_total": PERIODS[period]["days"],
                              "ticks_day": result["ticks"],
                              "checkpoint_sha256": last_hash}), flush=True)
        finished = report_and_qc(period, root, books, data, references,
                                 counts, seen, pins, last_hash)
        atomic_status(root, {
            "stage": "COMPLETE_PENDING_EXTERNAL_INDEPENDENT_QC",
            "source_head": expected_head, "period": period,
            "days_done": PERIODS[period]["days"],
            "days_total": PERIODS[period]["days"],
            "last_checkpoint_sha256": last_hash,
            "result_sha256": sha(root / "result.json"),
            "ledger_sha256": sha(root / "trade_ledger.jsonl"),
        })
        return finished


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--period", required=True, choices=("90d", "29d", "both"))
    parser.add_argument("--mode", required=True, choices=("preflight", "smoke", "run"))
    parser.add_argument("--smoke-hours", type=int, default=2)
    a = parser.parse_args()
    if not 1 <= a.smoke_hours <= 6:
        raise ValueError("smoke hours must be 1..6")
    periods = ("29d", "90d") if a.period == "both" else (a.period,)
    for period in periods:
        print(json.dumps(run_period(period, a.source_head, a.mode,
                                    a.smoke_hours), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
