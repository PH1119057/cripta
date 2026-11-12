#!/usr/bin/env python3
"""#638: identical unified five-symbol P90L90 capital-constrained 2025/2026 replay.

Compare only exact H9 RAW relevant-side union depth == 0 vs original [0,15]%.
Use original PREV7D +/-0.50pp labels, original original.Exit.on_trade,
original 2025 Book margin/wallet accounting, and *ONE shared* LONG/SHORT portfolio
with one position per symbol and a single causal SECOND48 pause queue.

Per period independent 2 depths x 2 capital models x NO_PAUSE/SECOND48 = 8
books, all native Bybit tick-order path-dependent. Three frozen historical
periods: 29d,90d,183d. RESEARCH ONLY: no runtime, DB, Exchange, Strategy mutation.

Exact writer actor cripta UID107:GID109. READ only detached GitHub source, 3
immutable original research datasets (source raw/Entry/geometry + reference
ledgers). CREATE/WRITE only the unique versioned research results leaf for the
chosen period under /data/cripta/research/runs/*/results. RENAME only temporary
checkpoint/status in own leaf; DELETE none. systemd User=cripta, independent
cgroup, ProtectSystem=strict, ReadWritePaths exact three result parents.
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
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_v1 as old  # noqa: E402
from scripts.research import p90l90_depthzero_prev7d_full_matrix_v2 as prev  # noqa: E402
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as pause  # noqa: E402

base = old.original
RESEARCH = "P90L90_UNIFIED_DEPTH0_VS_0TO15_THREE_PERIODS_CAPITAL_V1"
LEAF = "p90l90_unified_depth0_vs_0to15_capital_20261112_v1"
SYMBOLS = tuple(old.SYMBOLS)
WINDOW = {
    "29d": {"root": prev.PERIODS["29d"]["root"], "start": "2026-09-21", "input_start": "2026-09-11", "days": 29, "manifest_days": 39},
    "90d": {"root": prev.PERIODS["90d"]["root"], "start": "2026-05-18", "input_start": "2026-05-08", "days": 90, "manifest_days": 100},
    "183d": {"root": old.ROOT, "start": "2025-04-01", "input_start": "2025-03-22", "days": 183, "manifest_days": 193},
}
DEPTHS = {"ZERO": "0", "ORIGINAL_0TO15": "0-15"}
CASH = ("fixed", "compound_capped")
PAUSE = ("NO_PAUSE", "SECOND48")
BOOK_NAMES = tuple(f"{depth}__{cash}__{mode}" for depth in DEPTHS for cash in CASH for mode in PAUSE)
RESERVE_GIB = 5
PINNED = (
    "scripts/research/p90l90_unified_depth_three_period_v1.py",
    "scripts/research/p90l90_unified_depth_three_period_qc_v1.py",
    "scripts/research/p90l90_bull2025_portfolio_v1.py",
    "scripts/research/p90l90_bull2025_entry_v1.py",
    "scripts/research/p90l90_depthzero_prev7d_full_matrix_v2.py",
    "scripts/research/p90l90_entry_only_hardstop_embargo_v1.py",
    "scripts/research/p90l90_portfolio_pause_v1.py",
    "scripts/research/p90l90_exit_90d_v2.py",
    "scripts/research/p90l90_29d_exit_replay_v1.py",
    "scripts/research/p90l90_29d_sources_v1.py",
    "scripts/research/p90l90_entry_sep29_v1.py",
    "scripts/research/p90l90_entry_90d_v2.py",
)
POSITION_DECIMAL = {"entry", "stop", "best_favorable", "tp"}
START_WALLET = D("1000")
FIXED_MARGIN = D("200")
LEVERAGE = D("5")
DYNAMIC_SHARE = D("0.2")


def sha(path: Path) -> str:
    return prev.sha(path)


def source_head() -> str:
    return subprocess.run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True, timeout=15).stdout.strip()


def output(period: str) -> Path:
    return Path(WINDOW[period]["root"]) / "results" / LEAF


def source_pins(commit: str) -> dict:
    if (len(commit) != 40 or source_head() != commit
            or old.START_WALLET != START_WALLET or old.FIXED_MARGIN != FIXED_MARGIN
            or old.LEVERAGE != LEVERAGE or old.TAKER != D("0.00055")
            or old.MAKER != D("0.0002") or tuple(SYMBOLS) != tuple(pause.SYMBOLS)):
        raise ValueError("source/economics identity differs from original 2025 contract")
    # The original 2025 source uses the operational mirror for module imports;
    # require same Git identity AND same bytes, instead of silently relying on it.
    mirror = Path("/srv/cripta/source_checkout")
    mirror_sha = subprocess.run(["git", "-C", str(mirror), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    if mirror_sha != commit:
        raise ValueError("source mirror differs from GitHub exact research source")
    pins = {rel: sha(SOURCE / rel) for rel in PINNED}
    for rel in PINNED:
        if sha(mirror / rel) != pins[rel]:
            raise ValueError("read-only original module SHA diverges from pinned source: " + rel)
    return pins


def filter_event(event: dict, depth: str) -> bool:
    val = D(str(event["depth_pct"]))
    if not val.is_finite() or not D(0) <= val <= D(15):
        raise ValueError("candidate outside frozen original H9 union depth [0,15]")
    return event["PREV7D"] is True and (depth == "ORIGINAL_0TO15" or val == 0)


class UnifiedBook(old.Book):
    """Reuse *original 2025 Book.handle* with context-specific side selection.

    Original old.Book tracks one side across all five symbols. Instead of
    changing its Exit, wallet and SECOND48 code, select physical side from
    the already-held position (or single new Entry) at each native tick.
    A foreign-side Entry during an occupied coin counts as busy, not a fill.
    """
    def __init__(self, depth: str, mode: str, pause_mode: str):
        super().__init__("LONG_ONLY", pause_mode, mode, [])
        self.depth = depth
        self.side = "BOTH"
        self.max_slots = 0
        self.capital_denials = 0

    def handle_unified(self, symbol: str, t: float, seq: int, px: D,
                       raw_events: tuple, state: tuple, priority: int) -> bool:
        events = tuple(ev for ev in raw_events if filter_event(ev, self.depth))
        if len(events) > 1:
            raise ValueError("two opposite events on one native tick are not frozen semantics")
        position = self.pos[symbol]
        selected = position.side if position is not None else (
            events[0]["side"] if events else None
        )
        if selected is None:
            return False
        self.side = selected
        foreign = sum(ev["side"] != selected for ev in events)
        before_block = self.counts["capital_blocked_candidate"]
        used = super().handle(symbol, t, seq, px, events, state, priority)
        # For a held opposite-side slot, Book.handle intentionally ignores
        # opposite Entry; it must still be counted as rejected by occupancy.
        if foreign:
            if position is None:
                raise ValueError("foreign candidate without an existing slot")
            self.counts["qualified_candidate"] += foreign
            self.counts["busy_or_same_tick"] += foreign
        self.capital_denials += self.counts["capital_blocked_candidate"] - before_block
        self.max_slots = max(self.max_slots, sum(x is not None for x in self.pos.values()))
        if self.max_slots > 5:
            raise ValueError("unified one-position-per-coin invariant failed")
        return used

    def mark(self, timestamp: float, last_prices: dict) -> D:
        equity = self.wallet
        for symbol, pos in self.pos.items():
            if pos is None:
                continue
            if symbol not in last_prices:
                raise ValueError("open symbol has no causal last-price mark")
            nominal = self.margin[symbol] * LEVERAGE
            equity += nominal * (last_prices[symbol] / pos.entry - 1) * (
                D(1) if pos.side == "LONG" else D(-1))
        self.peak_equity = max(self.peak_equity, equity)
        dd = (self.peak_equity - equity) / self.peak_equity * D(100) if self.peak_equity > 0 else D(0)
        self.max_daily_dd_pct = max(self.max_daily_dd_pct, dd)
        self.daily_marks.append((str(timestamp), str(equity)))
        return equity

    def finish_unified(self, last_prices: dict, at: float) -> dict:
        open_positions = []
        unrealized = D(0)
        for symbol, pos in self.pos.items():
            if pos is None:
                continue
            nominal = self.margin[symbol] * LEVERAGE
            floating = old.money(nominal * (last_prices[symbol] / pos.entry - 1) * (
                D(1) if pos.side == "LONG" else D(-1)))
            unrealized += floating
            open_positions.append({
                "symbol": symbol, "side": pos.side,
                "entry_ts": repr(pos.entry_ts),
                "entry_price": str(pos.entry),
                "entry_event_id": pos.entry_event_id,
                "last_price": str(last_prices[symbol]),
                "margin": str(self.margin[symbol]),
                "notional": str(nominal),
                "unrealized_gross_usdt": str(floating),
                "entry_fee_already_debited": str(self.open_fee[symbol]),
            })
        self.counts["censored_open"] = len(open_positions)
        if self.counts["qualified_candidate"] != (
            self.counts["accepted"] + self.counts["busy_or_same_tick"]
            + self.counts["paused_candidate"] + self.counts["capital_blocked_candidate"]
        ):
            raise ValueError("unified Entry candidate reconciliation failed")
        if self.counts["accepted"] != self.counts["closed"] + len(open_positions):
            raise ValueError("unified accepted / closed / censored mismatch")
        if self.counts["closed"] != len(self.trades):
            raise ValueError("unified closed count/ledger mismatch")
        if (self.m["gross_usdt"] - self.m["closed_entry_fee_usdt"] -
                self.m["exit_fee_usdt"] != self.m["closed_net_usdt"]
                or sum((D(t["net_usdt"]) for t in self.trades), D(0))
                   != self.m["closed_net_usdt"]
                or self.m["positive_closed_net_usdt"] + self.m["negative_closed_net_usdt"]
                   != self.m["closed_net_usdt"]):
            raise ValueError("unified financial gross/fees/net mismatch")
        if (self.m["all_entry_fee_usdt"] != self.m["closed_entry_fee_usdt"]
                + sum(self.open_fee.values(), D(0))
                or self.wallet - START_WALLET != self.m["gross_usdt"] -
                self.m["all_entry_fee_usdt"] - self.m["exit_fee_usdt"]
                or self.reserved != sum(self.margin.values(), D(0))):
            raise ValueError("wallet/censored/margin reconciliation failed")
        final_equity = self.mark(at, last_prices)
        return {
            "counts": dict(self.counts), "cash_mode": self.mode,
            "depth": self.depth, "scenario": self.scenario,
            "wallet_usdt": str(self.wallet), "wallet_net_usdt": str(self.wallet - START_WALLET),
            "modeled_return_pct": str((self.wallet - START_WALLET) / START_WALLET * 100),
            "equity_with_open_unrealized_gross_usdt": str(final_equity),
            "equity_return_pct": str((final_equity - START_WALLET) / START_WALLET * 100),
            "open_unrealized_gross_usdt": str(unrealized),
            "reserved_margin_usdt": str(self.reserved),
            "max_reserved_margin_usdt": str(self.max_reserved),
            "max_simultaneous_slots": self.max_slots,
            "open_censored": open_positions,
            "daily_mark_drawdown_pct": str(self.max_daily_dd_pct),
            "money": {k: str(v) for k, v in self.m.items()},
            "hardstop_count": self.counts.get("exit_HARD_STOP", 0),
            "pause_trigger_count": self.counts.get("second48_pause_triggers", 0),
            "capital_blocked_candidate_count": self.counts.get("capital_blocked_candidate", 0),
            "closed": len(self.trades),
            "after_fees": True,
            "modeled_fill_not_exchange": True,
        }


def serialize_position(pos) -> dict | None:
    if pos is None:
        return None
    return {k: str(v) if isinstance(v, D) else v for k, v in asdict(pos).items()}


def freeze(book: UnifiedBook) -> dict:
    return {
        "depth": book.depth, "mode": book.mode, "scenario": book.scenario,
        "side": book.side, "pos": {s: serialize_position(p) for s, p in book.pos.items()},
        "margin": {s: str(v) for s, v in book.margin.items()},
        "open_fee": {s: str(v) for s, v in book.open_fee.items()},
        "wallet": str(book.wallet), "reserved": str(book.reserved),
        "pause_until": repr(book.pause_until), "last_stops": list(book.last_stops),
        "trades": book.trades, "counts": dict(book.counts),
        "money": {s: str(v) for s, v in book.m.items()},
        "peak_equity": str(book.peak_equity),
        "max_daily_dd_pct": str(book.max_daily_dd_pct),
        "daily_marks": book.daily_marks, "max_reserved": str(book.max_reserved),
        "max_slots": book.max_slots, "capital_denials": book.capital_denials,
    }


def restore(book: UnifiedBook, saved: dict) -> None:
    if (saved["depth"], saved["mode"], saved["scenario"]) != (
            book.depth, book.mode, book.scenario):
        raise ValueError("foreign checkpoint book identity")
    book.side = saved["side"]
    book.pos = {}
    for s, rec in saved["pos"].items():
        if rec is None:
            book.pos[s] = None
        else:
            book.pos[s] = base.Position(**{
                k: D(v) if k in POSITION_DECIMAL and v is not None else v
                for k, v in rec.items()
            })
    if set(book.pos) != set(SYMBOLS):
        raise ValueError("checkpoint coin universe changed")
    book.margin = {s: D(v) for s, v in saved["margin"].items()}
    book.open_fee = {s: D(v) for s, v in saved["open_fee"].items()}
    book.wallet = D(saved["wallet"])
    book.reserved = D(saved["reserved"])
    book.pause_until = float(saved["pause_until"])
    book.last_stops = deque(saved["last_stops"])
    book.trades = saved["trades"]
    book.counts = Counter(saved["counts"])
    book.m = defaultdict(lambda: D(0), {k: D(v) for k, v in saved["money"].items()})
    book.peak_equity = D(saved["peak_equity"])
    book.max_daily_dd_pct = D(saved["max_daily_dd_pct"])
    book.daily_marks = [tuple(x) for x in saved["daily_marks"]]
    book.max_reserved = D(saved["max_reserved"])
    book.max_slots = int(saved["max_slots"])
    book.capital_denials = int(saved["capital_denials"])


def load_data(period: str) -> dict:
    if period in ("90d", "29d"):
        data, _refs = prev.load_original(period)
    else:
        data = {}
        root = Path(WINDOW[period]["root"]) / "results"
        for symbol in SYMBOLS:
            folder = root / "entry_full_2025_v1" / symbol
            m = json.loads((folder / "manifest.json").read_text())
            candidate = folder / ("candidates_" + symbol + ".jsonl")
            states_file = root / "l53_geometry_full_2025_v1" / "parts" / ("states_" + symbol + ".csv.gz")
            if (m["research"] != "P90L90_ENTRY_BULL2025_V1" or
                    m["symbol"] != symbol or not m["complete_window_input"] or
                    sha(candidate) != m["events_sha256"] or
                    sha(states_file) != m["frozen_l53_sha256"]):
                raise ValueError("frozen 2025 native Entry/geometry source mismatch")
            states, _, med7, meta = old.entry.load_frozen_period(
                states_file, old.EVAL_START, old.EVAL_END)
            if (len(states) != 183*288 or len(med7) != 183
                    or meta["prior_complete_days"] != 7):
                raise ValueError("2025 fully closed causal PREV7D geometry missing")
            events, counts = base.read_events(candidate, symbol)
            if counts["raw_candidates"] != m["events"]:
                raise ValueError("2025 candidate ledger row drift")
            data[symbol] = {"manifest": m, "states": states, "events": events,
                            "symbol": symbol}
    spec = WINDOW[period]
    start = datetime.fromisoformat(spec["start"]).replace(tzinfo=UTC)
    warm = datetime.fromisoformat(spec["input_start"]).replace(tzinfo=UTC)
    for symbol in SYMBOLS:
        m = data[symbol]["manifest"]
        if (m["symbol"] != symbol or len(m["inputs"]) != spec["manifest_days"]
                or len(m["medians_PREV7D"]) != spec["days"]
                or m.get("PREV7D_WIDTH_LOOKAHEAD") is not False):
            raise ValueError("incomplete causal source window " + symbol)
        med = {k: D(str(v)) for k, v in m["medians_PREV7D"].items()}
        expected_days = [(start + timedelta(days=i)).date().isoformat() for i in range(spec["days"])]
        if set(med) != set(expected_days):
            raise ValueError("PREV7D UTC-day median coverage incomplete")
        for i, item in enumerate(m["inputs"]):
            if ((warm + timedelta(days=i)).date().isoformat() not in item["file"]
                    or int(item["rows"]) < 1):
                raise ValueError("native daily source manifest gap")
        observed = Counter()
        for seq, events in data[symbol]["events"].items():
            if len(events) > 1:
                raise ValueError("ambiguous simultaneous opposite-side native Entry")
            for ev in events:
                if (ev["symbol"] != symbol or ev["side"] not in ("LONG", "SHORT")
                        or not D(str(ev["depth_pct"])).is_finite()):
                    raise ValueError("foreign/invalid H9 candidate")
                day = datetime.fromtimestamp(int(ev["state_effective_ts"]), UTC).date().isoformat()
                diff = abs(D(str(ev["width_pct"])) - med[day])
                if bool(ev["PREV7D"]) != (diff <= D("0.50")):
                    raise ValueError("frozen PREV7D +/-0.50 exact parity mismatch")
                for depth in DEPTHS:
                    if filter_event(ev, depth):
                        observed[depth] += 1
        data[symbol]["depth_counts"] = dict(observed)
        # Confirm every warmup source archive byte identity before the first write.
        for item in m["inputs"][:10]:
            file = native_path(period, data[symbol], symbol, item)
            if sha(file) != item["sha256"]:
                raise ValueError("frozen warmup source archive SHA drift")
    return data


def native_path(period: str, data: dict, symbol: str, item: dict) -> Path:
    if period == "183d":
        parent = Path(WINDOW[period]["root"]) / "inputs"
    elif period == "90d":
        parent = data["raw_root"]
    else:
        parent = Path(WINDOW[period]["root"]) / "inputs"
    return parent / symbol / "public_trades" / item["file"]


def day_ticks(period: str, data: dict, symbol: str, day: int):
    spec = WINDOW[period]
    manifest = data["manifest"]
    idx = 10 + day
    item = manifest["inputs"][idx]
    offset = sum(int(v["rows"]) for v in manifest["inputs"][:idx])
    p = native_path(period, data, symbol, item)
    if sha(p) != item["sha256"]:
        raise ValueError("native Bybit public-trades source SHA mismatch " + symbol)
    day0 = datetime.fromisoformat(spec["start"]).replace(tzinfo=UTC)
    first_ts = int((day0 + timedelta(days=day)).timestamp())
    count = 0
    last = float("-inf")
    with gzip.open(p, "rt", newline="") as fh:
        rows = csv.DictReader(fh)
        if not {"timestamp", "symbol", "price", "trdMatchID"}.issubset(rows.fieldnames or []):
            raise ValueError("Bybit public native trade schema drift")
        for record in rows:
            count += 1
            t = float(record["timestamp"])
            if not first_ts <= t < first_ts + 86400 or t < last or record["symbol"] != symbol:
                raise ValueError("native tick chronological continuity/source-day drift")
            last = t
            seq = offset + count
            px = D(record["price"])
            for e in data["events"].get(seq, ()):
                if (e["trade_id"] != record["trdMatchID"] or
                        float(e["ts"]) != t or D(e["price"]) != px):
                    raise ValueError("Entry candidate not from this exact source trade")
            yield t, seq, px
    if count != int(item["rows"]):
        raise ValueError("native Bybit source row-count mismatch")


def preflight(period: str, commit: str) -> tuple[dict, dict]:
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("research must run as actual cripta UID107:GID109")
    target = output(period)
    if (target.is_symlink() or target.parent.is_symlink()
            or not target.parent.is_dir() or target.parent.stat().st_uid != 107
            or not os.access(target.parent, os.W_OK | os.X_OK)):
        raise PermissionError("research output parent is not writable as cripta")
    if shutil.disk_usage(target.parent).free < RESERVE_GIB * 2**30:
        raise RuntimeError("data disk below 5 GiB safety reserve")
    pins = source_pins(commit)
    dataset = load_data(period)
    return pins, dataset


def baseline_booknames(period: str) -> tuple:
    return ("REFERENCE_LONG", "REFERENCE_SHORT") if period == "183d" else (
        "REFERENCE_NO_PAUSE", "REFERENCE_SECOND48")


def make_books(period: str) -> tuple[dict, dict]:
    books = {
        f"{depth}__{cash}__{mode}": UnifiedBook(depth, cash, mode)
        for depth in DEPTHS for cash in CASH for mode in PAUSE
    }
    refs = ({
        "REFERENCE_LONG": pause.EntryEmbargo(0, None),
        "REFERENCE_SHORT": pause.EntryEmbargo(0, None),
    } if period == "183d" else {
        "REFERENCE_NO_PAUSE": pause.EntryEmbargo(0, None),
        "REFERENCE_SECOND48": pause.EntryEmbargo(2, 48),
    })
    return books, refs


def freeze_checkpoint(books: dict, refs: dict, pins: dict, period: str,
                      day: int, prices: dict, seen: dict, prior_sha: str) -> dict:
    return {
        "research": RESEARCH, "period": period, "days_done": day + 1,
        "source_pins": pins, "previous_checkpoint_sha256": prior_sha,
        "last_native_prices": {k: str(v) for k, v in prices.items()},
        "source_seen": dict(seen),
        "books": {k: freeze(b) for k, b in books.items()},
        "reference_books": {k: prev.freeze_book(b) for k, b in refs.items()},
    }


def load_checkpoint(root: Path, period: str, pins: dict) -> tuple[dict | None, str]:
    parent = root / "checkpoints"
    prior = ""
    latest = None
    for day in range(1, WINDOW[period]["days"] + 1):
        f = parent / f"day_{day:03d}.json.gz"
        if not f.exists():
            break
        if f.is_symlink():
            raise PermissionError("symlink checkpoint not allowed")
        blob = f.read_bytes()
        snap = json.loads(gzip.decompress(blob))
        if (snap["research"] != RESEARCH or snap["period"] != period
                or snap["source_pins"] != pins or snap["days_done"] != day
                or snap["previous_checkpoint_sha256"] != prior
                or set(snap["books"]) != set(BOOK_NAMES)
                or set(snap["reference_books"]) != set(baseline_booknames(period))):
            raise ValueError("wrong source/hash/book daily checkpoint")
        prior = hashlib.sha256(blob).hexdigest()
        latest = snap
    if any(int(p.name[4:7]) > (latest["days_done"] if latest else 0)
           for p in parent.glob("day_???.json.gz")):
        raise ValueError("gapped daily checkpoint series")
    return latest, prior


def references_expected(period: str) -> dict:
    result = {}
    if period == "183d":
        p = Path(WINDOW[period]["root"]) / "results" / "exit_unit_2025_v1"
        for side in ("LONG", "SHORT"):
            result["REFERENCE_" + side] = {}
            for symbol in SYMBOLS:
                unit = p / symbol
                qc = json.loads((unit / "qc.json").read_text())
                f = unit / ("trade_ledger_" + side.lower() + ".jsonl")
                if sha(f) != qc["ledgers"][side]:
                    raise ValueError("2025 independently QC'd unit ledger SHA drift")
                result["REFERENCE_" + side][symbol] = [
                    {k: v for k, v in json.loads(line).items() if k != "research_direction"}
                    for line in f.open()
                ]
    else:
        basename = "entry_only_embargo_90d_20261026_v1" if period == "90d" else "entry_only_embargo_29d_20261026_v1"
        p = Path(WINDOW[period]["root"]) / "results" / basename
        report = json.loads((p / "result.json").read_text())
        ledger = p / "trade_ledger.jsonl"
        if sha(ledger) != report["ledger_sha256"]:
            raise ValueError("2026 reference entire source ledger SHA drift")
        result = {name: [] for name in baseline_booknames(period)}
        for line in ledger.open():
            row = json.loads(line)
            name = {"BASELINE": "REFERENCE_NO_PAUSE", "SECOND_48H": "REFERENCE_SECOND48"}.get(
                row["portfolio_scenario"])
            if name is not None:
                result[name].append({k: v for k, v in row.items() if k != "portfolio_scenario"})
    return result


def compare_reference(period: str, books: dict) -> dict:
    expected = references_expected(period)
    checks = {}
    for name, book in books.items():
        if period == "183d":
            for symbol in SYMBOLS:
                rows = [r for r in book.trades if r["symbol"] == symbol]
                # Source native ordering matches the earlier independently
                # published 2025 single-coin + single-direction unit trade tape.
                if rows != expected[name][symbol]:
                    raise ValueError("exact 2025 unit Entry/Exit trade parity failed " + name + symbol)
            checks[name] = {"status": "EXACT_2025_UNIT_TRADE_PARITY", "trades": len(book.trades)}
        else:
            if book.trades != expected[name]:
                raise ValueError("original 2026 single unified native portfolio trade parity failed " + name)
            checks[name] = {"status": "EXACT_2026_COMBINED_TRADE_PARITY", "trades": len(book.trades)}
    return checks


def replay_day(period: str, day: int, data: dict, books: dict,
               refs: dict, seen: dict, previous_prices: dict,
               smoke_seconds: int = 0) -> dict:
    streams = {s: day_ticks(period, data[s], s, day) for s in SYMBOLS}
    heap = []
    for i, s in enumerate(SYMBOLS):
        tick = next(streams[s], None)
        if tick is None:
            raise ValueError("empty native tick stream for symbol/day " + s)
        heapq.heappush(heap, (tick[0], i, tick))
    begin = datetime.fromisoformat(WINDOW[period]["start"]).replace(tzinfo=UTC)
    limit = (begin + timedelta(days=day)).timestamp() + smoke_seconds if smoke_seconds else float("inf")
    prices = dict(previous_prices)
    n = 0
    t_started = time.monotonic()
    last_report = t_started
    while heap:
        t, priority, (_, seq, px) = heapq.heappop(heap)
        if t >= limit:
            return {"ticks": n, "prices": prices, "smoke_partial": True}
        s = SYMBOLS[priority]
        prices[s] = px
        events = data[s]["events"].get(seq, ())
        if events:
            seen[s] += len(events)
        active = any(b.pos[s] is not None for b in books.values())
        ref_active = any(b.pos[s] is not None for b in refs.values())
        if active or ref_active or events:
            state = data[s]["states"].get(int(t // 300) * 300)
            if state is None:
                raise ValueError("missing native causal L53 state for Entry/Exit")
            for b in books.values():
                if b.pos[s] is not None or events:
                    b.handle_unified(s, t, seq, px, events, state, priority)
            for name, b in refs.items():
                ev = events
                if period == "183d":
                    side = "LONG" if name == "REFERENCE_LONG" else "SHORT"
                    ev = tuple(e for e in events if e["side"] == side)
                if b.pos[s] is not None or ev:
                    b.handle(s, t, seq, px, ev, state)
        n += 1
        if n % 300000 == 0 and time.monotonic() - last_report > 20:
            last_report = time.monotonic()
            print(json.dumps({"stage": "HEARTBEAT", "period": period,
                              "day": day + 1, "ticks": n,
                              "sec_elapsed": round(last_report - t_started, 1)}), flush=True)
        nxt = next(streams[s], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], priority, nxt))
    for b in books.values():
        b.mark((begin + timedelta(days=day + 1)).timestamp(), prices)
    return {"ticks": n, "prices": prices, "smoke_partial": False}


def independent_qc(period: str, root: Path, commit: str) -> dict:
    qc = importlib.import_module(
        "scripts.research.p90l90_unified_depth_three_period_qc_v1")
    return qc.audit(period, root, commit)


def perform(period: str, commit: str, mode: str, smoke_minutes: int) -> dict:
    pins, data = preflight(period, commit)
    if mode == "preflight":
        return {
            "stage": "READ_ONLY_PREPARED", "period": period, "source_head": commit,
            "source_pins": pins,
            "candidate_counts": {s: data[s]["depth_counts"] for s in SYMBOLS},
            "disk_free_gib": round(shutil.disk_usage(output(period).parent).free/2**30, 3),
        }
    books, refs = make_books(period)
    seen = {s: 0 for s in SYMBOLS}
    prices = {}
    if mode == "smoke":
        r = replay_day(period, 0, data, books, refs, seen, prices, smoke_minutes * 60)
        return {
            "stage": "SMOKE_PASS_NO_MUTATION", "period": period, "minutes": smoke_minutes,
            "ticks": r["ticks"], "events_seen": seen, "books": len(books),
            "book_max_slots": {n: b.max_slots for n, b in books.items()},
        }
    if mode != "run":
        raise ValueError("unsupported research mode")
    root = output(period)
    if not root.exists():
        root.mkdir(mode=0o2770)
        (root / "checkpoints").mkdir(mode=0o2770)
        snap = root / "source_snapshot"
        snap.mkdir(mode=0o2770)
        for rel, digest in pins.items():
            dst = snap / Path(rel).name
            with dst.open("xb") as out:
                out.write((SOURCE / rel).read_bytes())
            if sha(dst) != digest:
                raise ValueError("source snapshot checksum mismatch")
        (root / "manifest.json").write_text(json.dumps({
            "research": RESEARCH, "source_head": commit, "source_pins": pins,
            "period": period, "evaluation_days": WINDOW[period]["days"],
            "source_raw_root": str(WINDOW[period]["root"]),
            "cash_models": list(CASH), "pauses": list(PAUSE),
            "depths": DEPTHS, "wallet_start_usdt": str(START_WALLET),
            "fixed_margin_usdt": str(FIXED_MARGIN),
            "dynamic_fraction": str(DYNAMIC_SHARE),
            "leverage": str(LEVERAGE),
            "candidate_counts": {s: data[s]["depth_counts"] for s in SYMBOLS},
        }, sort_keys=True, indent=2) + "\n")
    else:
        if root.is_symlink() or not (root / "manifest.json").is_file():
            raise ValueError("unexpected output existing root")
        m = json.loads((root / "manifest.json").read_text())
        if (m.get("source_head") != commit or m.get("research") != RESEARCH
                or m.get("period") != period or m.get("source_pins") != pins):
            raise ValueError("research source/identity changed, no blind resume")
        if (root / "result.json").exists():
            raise FileExistsError("research already finalized; no duplicate execution")
    fcntl = importlib.import_module("fcntl")
    with (root / ".writer.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        latest, prev_sha = load_checkpoint(root, period, pins)
        start_day = 0
        if latest:
            start_day = latest["days_done"]
            seen = latest["source_seen"]
            prices = {s: D(v) for s, v in latest["last_native_prices"].items()}
            for name, rec in latest["books"].items():
                restore(books[name], rec)
            for name, rec in latest["reference_books"].items():
                prev.restore_book(refs[name], rec)
        for day in range(start_day, WINDOW[period]["days"]):
            if shutil.disk_usage(root).free < RESERVE_GIB * 2**30:
                raise RuntimeError("research safety reserve 5GiB reached")
            print(json.dumps({"stage": "DAY_START", "period": period,
                              "day": day + 1, "days": WINDOW[period]["days"]}), flush=True)
            r = replay_day(period, day, data, books, refs, seen, prices)
            prices = r["prices"]
            item = freeze_checkpoint(books, refs, pins, period, day, prices, seen, prev_sha)
            blob = gzip.compress(json.dumps(item, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode(), compresslevel=5, mtime=0)
            dst = root / "checkpoints" / f"day_{day + 1:03d}.json.gz"
            prev.write_immutable(dst, blob)
            prev_sha = hashlib.sha256(blob).hexdigest()
            prev.atomic_status(root, {
                "stage": "RUNNING", "source_head": commit, "period": period,
                "days_done": day + 1, "days_total": WINDOW[period]["days"],
                "last_checkpoint_sha256": prev_sha,
                "free_gib": round(shutil.disk_usage(root).free/2**30, 3),
            })
            print(json.dumps({"stage": "DAY_COMMITTED", "period": period,
                              "day": day + 1, "days_total": WINDOW[period]["days"],
                              "ticks": r["ticks"], "checkpoint_sha256": prev_sha}), flush=True)
        for symbol in SYMBOLS:
            expected = sum(map(len, data[symbol]["events"].values()))
            if seen[symbol] != expected:
                raise ValueError("not all native source candidate events consumed " + symbol)
        controls = compare_reference(period, refs)
        if set(prices) != set(SYMBOLS):
            raise ValueError("missing final native tick marks")
        result = {name: b.finish_unified(prices, (
            datetime.fromisoformat(WINDOW[period]["start"]).replace(tzinfo=UTC)
            + timedelta(days=WINDOW[period]["days"])).timestamp())
                  for name, b in books.items()}
        if set(result) != set(BOOK_NAMES):
            raise ValueError("research incomplete matrix")
        all_rows = root / "trade_ledger.jsonl"
        with all_rows.open("x") as f:
            for name in BOOK_NAMES:
                for row in books[name].trades:
                    f.write(json.dumps({**row, "research_case": name}, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        out = {
            "status": "COMPLETE_PENDING_INDEPENDENT_QC",
            "research": RESEARCH, "period": period,
            "source_head": commit, "source_pins": pins,
            "dates_utc": [WINDOW[period]["start"], (
                datetime.fromisoformat(WINDOW[period]["start"]) +
                timedelta(days=WINDOW[period]["days"])).date().isoformat()],
            "days": WINDOW[period]["days"], "symbols": list(SYMBOLS),
            "cash_model": {
                "initial_wallet_usdt": "1000", "fixed_margin_usdt": "200",
                "compound_fraction_of_current_wallet": "0.2",
                "leverage": "5", "taker_fee": str(old.TAKER),
                "resting_maker_fee": str(old.MAKER), "capital_gate": True,
            },
            "only_physical_change": "H9 RAW relevant-side union depth 0 vs [0,15]",
            "other_entry": "PREV7D L53 width absolute deviation <=0.50pp from preceding 7 fully closed UTC days; original first native crossing",
            "other_exit": "original TRAIL035 after 0.50% favorable, opposite RAW L53 dynamic TP, gross -10% hardstop",
            "pause": "NO_PAUSE vs second own actual hardstop in trailing 48h -> new Entry embargo 48h, no flatten",
            "one_combined_portfolio": True, "one_slot_per_coin": True,
            "case_count": 8, "reference_trade_by_trade_parity": controls,
            "candidate_counts": {s: data[s]["depth_counts"] for s in SYMBOLS},
            "source_events_seen": seen,
            "last_native_prices": {k: str(v) for k, v in prices.items()},
            "final_checkpoint_sha256": prev_sha,
            "ledger_sha256": sha(all_rows),
            "cases": result,
            "unmodeled": ["spread", "slippage", "funding", "latency", "actual_exchange_fills"],
            "not_pristine_blinded_oos": True, "research_only": True,
        }
        f = root / "result.json"
        with f.open("x") as stream:
            json.dump(out, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        prev.atomic_status(root, {
            "stage": "COMPLETE_PENDING_INDEPENDENT_QC",
            "source_head": commit, "period": period,
            "days_done": WINDOW[period]["days"], "days_total": WINDOW[period]["days"],
            "result_sha256": sha(f), "ledger_sha256": sha(all_rows),
        })
        qc = independent_qc(period, root, commit)
        return {"period": period, "stage": qc["status"], "qc_sha256": sha(root / "qc.json"),
                "cases": {name: result[name]["wallet_net_usdt"] for name in BOOK_NAMES}}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source-head", required=True)
    p.add_argument("--period", choices=("29d", "90d", "183d", "all"), required=True)
    p.add_argument("--mode", choices=("preflight", "smoke", "run"), required=True)
    p.add_argument("--smoke-minutes", type=int, default=45)
    a = p.parse_args()
    if not 1 <= a.smoke_minutes <= 120:
        raise ValueError("smoke must be 1..120 minutes")
    for period in ("29d", "90d", "183d") if a.period == "all" else (a.period,):
        print(json.dumps(perform(period, a.source_head, a.mode,
                                 a.smoke_minutes), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
