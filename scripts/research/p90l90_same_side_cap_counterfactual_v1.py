#!/usr/bin/env python3
"""#638: full native tick path-dependent five-coin unified same-side cap replay.

Research ONLY. Original Entry/Exit/state/data/one-position-per-coin, original
shared 1000 USDT wallet and fixed200 margin ×5 leverage, SECOND48 stop
pause. Physical research change: same-side cap in {2,3,4,5}. Each cap owns a
fully independent capital/position/exit/SECOND48 trajectory.

Economics: conservative TAKER/TAKER cost BASELINE and original taker Entry,
eligible maker TP SENSITIVITY, each with fully separate wallet and admission.
No synthetic subtraction of old ledger losses. Exact cap5 original-fee
PORTFOLIO/LEDGER parity against independently QC-PASS frozen 29/90/183 V2.

Owner intent is RESEARCH, NOT approval of risk/Strategy/Execution change.
ACTOR cripta UID107:GID109; python3 -B; READ immutable source/data on Data,
source mirror original code pinned to authoritative GitHub. CREATE/WRITE only
unique new versioned period result leaf on /data/cripta/research/runs, atomic
checkpoint/status rename there; DELETE none. Separate QC fresh Python process.
No PostgreSQL, Exchange, trading runtime, deployed code or LIVE calls.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_unified_depth_three_period_v1 as engine  # noqa: E402
from scripts.research import p90l90_unified_portfolio_forensic_v1 as oldqc  # noqa: E402

RESEARCH = "P90L90_NATIVE_SAME_SIDE_CAP_COUNTERFACTUAL_V1"
LEAF = "p90l90_unified_sidecap_2_3_4_5_takertaker_20261114_v1"
CAPS = (5, 4, 3, 2)
FEES = ("TAKER_TAKER", "ORIGINAL_MAKER_TP")
CASES = tuple(f"CAP{cap}__{fee}" for fee in FEES for cap in CAPS)
NATIVE_CASE = "ORIGINAL_0TO15__fixed__SECOND48"
MIN_FREE_GIB = 5
ORIGINAL_MAKER = D("0.0002")
TAKER = D("0.00055")
NEW_PINNED = (
    "scripts/research/p90l90_same_side_cap_counterfactual_v1.py",
    "scripts/research/p90l90_unified_portfolio_forensic_v1.py",
)


def output(period: str) -> Path:
    return engine.output(period).parent / LEAF


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(2 ** 20), b""):
            h.update(block)
    return h.hexdigest()


def pins_for(commit: str) -> dict:
    if engine.old.MAKER != ORIGINAL_MAKER or engine.old.TAKER != TAKER:
        raise ValueError("parent native model fee constants drift")
    pins = engine.source_pins(commit)
    for path in NEW_PINNED:
        pins[path] = digest(SOURCE / path)
    if set(pins) != set(engine.PINNED) | set(NEW_PINNED):
        raise ValueError("research/source pin coverage drift")
    return pins


def asof_actor() -> None:
    if os.name != "posix" or (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("only cripta actor UID107:GID109 allowed")
    if engine.source_head() != EXPECTED_COMMIT:
        raise ValueError("unexpected research checkout HEAD")


EXPECTED_COMMIT = ""  # set exclusively by explicit audited CLI, not a default


class CapBook(engine.UnifiedBook):
    def __init__(self, cap: int, fees: str):
        if cap not in CAPS or fees not in FEES:
            raise ValueError("unknown cap or fee variant")
        super().__init__("ORIGINAL_0TO15", "fixed", "SECOND48")
        self.cap = cap
        self.fees = fees
        self.max_same_side_slots = 0

    def handle_unified(self, symbol: str, t: float, seq: int, px: D,
                       raw_events: tuple, state: tuple, priority: int) -> bool:
        filtered = tuple(
            e for e in raw_events if engine.filter_event(e, self.depth)
        )
        if len(filtered) > 1:
            raise ValueError("multiple opposite candidates on one physical tick")
        if filtered and self.pos[symbol] is None:
            side = filtered[0]["side"]
            same = sum(
                p is not None and p.side == side for p in self.pos.values()
            )
            if same >= self.cap and t >= self.pause_until:
                # Existing native parent counts rejected-candidate occupancy
                # in busy_or_same_tick; expose the exact new cap subset as a
                # separate counter. This preserves full candidate parity
                # without misstating it as a margin-capital rejection.
                self.counts["qualified_candidate"] += 1
                self.counts["busy_or_same_tick"] += 1
                self.counts["same_side_cap_blocked_candidate"] += 1
                return False
        # Explicit reversible *single-process, single-thread* fee-mode switch.
        # Only native old.Book.handle reads old.MAKER to charge eligible TP.
        # Original module bytes, sourced data and all other logic unchanged.
        before = engine.old.MAKER
        try:
            engine.old.MAKER = (
                TAKER if self.fees == "TAKER_TAKER" else ORIGINAL_MAKER
            )
            used = super().handle_unified(
                symbol, t, seq, px, raw_events, state, priority
            )
        finally:
            engine.old.MAKER = before
        same = Counter(
            p.side for p in self.pos.values() if p is not None
        )
        peak = max(same.values(), default=0)
        if peak > self.cap:
            raise ValueError("same-side cap exceeded by accepted native position")
        self.max_same_side_slots = max(self.max_same_side_slots, peak)
        return used

    def finish_unified(self, prices: dict, at: float) -> dict:
        item = super().finish_unified(prices, at)
        blocked = self.counts.get("same_side_cap_blocked_candidate", 0)
        if blocked < 0 or blocked > item["counts"].get("busy_or_same_tick", 0):
            raise ValueError("bad cap vs generic busy counter")
        item["same_side_cap"] = self.cap
        item["fee_model"] = self.fees
        item["max_same_side_slots"] = self.max_same_side_slots
        item["same_side_cap_blocked_candidate_count"] = blocked
        item["actual_same_symbol_or_same_tick_busy_count"] = (
            item["counts"].get("busy_or_same_tick", 0) - blocked
        )
        return item


def freeze(book: CapBook) -> dict:
    data = engine.freeze(book)
    data["same_side_cap"] = book.cap
    data["fee_model"] = book.fees
    data["max_same_side_slots"] = book.max_same_side_slots
    return data


def restore(book: CapBook, obj: dict) -> None:
    if (obj.get("same_side_cap"), obj.get("fee_model")) != (
        book.cap, book.fees
    ):
        raise ValueError("cap/fee changed across daily checkpoint")
    engine.restore(book, obj)
    book.max_same_side_slots = int(obj["max_same_side_slots"])
    if book.max_same_side_slots > book.cap:
        raise ValueError("checkpoint already exceeded same-side cap")


def make_books(period: str) -> tuple[dict, dict]:
    books = {
        f"CAP{cap}__{fees}": CapBook(cap, fees)
        for fees in FEES for cap in CAPS
    }
    _, references = engine.make_books(period)
    return books, references


def checkpoint(period: str, day: int, pins: dict, seen: dict,
               prices: dict, books: dict, refs: dict, prior: str) -> dict:
    return {
        "research": RESEARCH, "period": period, "days_done": day,
        "source_pins": pins, "previous_checkpoint_sha256": prior,
        "source_seen": dict(seen),
        "last_native_prices": {k: str(v) for k, v in prices.items()},
        "books": {k: freeze(v) for k, v in books.items()},
        "reference_books": {
            k: engine.prev.freeze_book(v) for k, v in refs.items()
        },
    }


def scan_checkpoints(root: Path, period: str, pins: dict) -> tuple[dict | None, str]:
    prior = ""
    latest = None
    for i in range(1, engine.WINDOW[period]["days"] + 1):
        p = root / "checkpoints" / f"day_{i:03d}.json.gz"
        if not p.is_file():
            break
        if p.is_symlink():
            raise ValueError("symlink checkpoint")
        raw = p.read_bytes()
        blob = json.loads(gzip.decompress(raw))
        if (
            blob.get("research") != RESEARCH or blob.get("period") != period
            or blob.get("days_done") != i
            or blob.get("source_pins") != pins
            or blob.get("previous_checkpoint_sha256") != prior
            or set(blob.get("books", {})) != set(CASES)
            or set(blob.get("reference_books", {}))
            != set(engine.baseline_booknames(period))
        ):
            raise ValueError("invalid immutable checkpoint chain")
        prior = hashlib.sha256(raw).hexdigest()
        latest = blob
    if any(int(p.name[4:7]) > (latest["days_done"] if latest else 0)
           for p in (root / "checkpoints").glob("day_???.json.gz")):
        raise ValueError("gapped historical checkpoint chain")
    return latest, prior


def readonly_preflight(period: str, commit: str) -> tuple[dict, dict]:
    global EXPECTED_COMMIT
    EXPECTED_COMMIT = commit
    asof_actor()
    root = output(period)
    parent = root.parent
    if (root.is_symlink() or parent.is_symlink()
            or not parent.is_dir() or parent.stat().st_uid != 107
            or not os.access(parent, os.W_OK | os.X_OK)
            or shutil.disk_usage(parent).free < MIN_FREE_GIB * 2**30):
        raise PermissionError("unique Data research destination not prepared")
    pins = pins_for(commit)
    data = engine.load_data(period)
    return pins, data


def provenance(period: str) -> tuple[dict, dict, list[dict]]:
    # Exact original 8-book V2 QC PASS; full 29+90+183 day chain verified.
    status, result, qc, rows, daily = oldqc.read_verified(period)
    return status, result, rows[NATIVE_CASE]


def write_json_immutable(path: Path, value: dict) -> None:
    data = json.dumps(value, sort_keys=True, ensure_ascii=False,
                      indent=2, allow_nan=False) + "\n"
    with path.open("x") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def qc_main(period: str, commit: str) -> dict:
    global EXPECTED_COMMIT
    EXPECTED_COMMIT = commit
    asof_actor()
    root = output(period)
    status = json.loads((root / "status.json").read_text())
    result = json.loads((root / "result.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    if (root.is_symlink() or status["stage"] != "COMPLETE_PENDING_INDEPENDENT_QC"
            or status["source_head"] != commit
            or manifest.get("source_head") != commit
            or manifest.get("source_pins") != result.get("source_pins")
            or set(result.get("cases", {})) != set(CASES)
            or (root / "qc.json").exists()):
        raise ValueError("QC requires exact immutable pending producer")
    original_pins = pins_for(commit)
    if original_pins != result["source_pins"]:
        raise ValueError("source blob changed before independent review")
    if (digest(root / "result.json") != status["result_sha256"]
            or digest(root / "trade_ledger.jsonl") != status["ledger_sha256"]
            or result["ledger_sha256"] != status["ledger_sha256"]):
        raise ValueError("producer result/ledger sha mismatch")
    import fcntl
    with (root / ".writer.lock").open("r") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        last, chain = scan_checkpoints(root, period, original_pins)
        if last is None or last["days_done"] != engine.WINDOW[period]["days"]:
            raise ValueError("not all daily immutable native checkpoints")
        if result["final_checkpoint_sha256"] != chain:
            raise ValueError("last daily checkpoint SHA mismatch")
        ledgers = {n: [] for n in CASES}
        for line in (root / "trade_ledger.jsonl").open():
            row = json.loads(line)
            name = row.pop("research_case")
            if name not in ledgers:
                raise ValueError("foreign portfolio scenario ledger row")
            ledgers[name].append(row)
        _, frozen, reference = provenance(period)
        case_qc = {}
        for name, item in result["cases"].items():
            cap = int(item["same_side_cap"])
            fee = item["fee_model"]
            if name != f"CAP{cap}__{fee}":
                raise ValueError("wrong scenario name/fee model")
            accepted, closed = item["counts"]["accepted"], item["counts"]["closed"]
            if closed != len(ledgers[name]) or accepted != (
                closed + len(item["open_censored"])
            ):
                raise ValueError("native accepted/closed/censored parity")
            if item["max_same_side_slots"] > cap:
                raise ValueError("reported cap exceeded")
            by_times = []
            for row in ledgers[name]:
                start, end = D(row["entry_ts"]), D(row["exit_ts"])
                if not start < end:
                    raise ValueError("negative/zero holding period")
                by_times.extend(((start, 1, row["side"], row["symbol"]),
                                 (end, 0, row["side"], row["symbol"])))
                nominal = D(row["notional_usdt"])
                entry_fee = engine.old.money(nominal * TAKER)
                exit_rate = TAKER if fee == "TAKER_TAKER" else (
                    ORIGINAL_MAKER if row["maker_exit"] else TAKER
                )
                exit_fee = engine.old.money(
                    nominal * D(row["exit_price"]) / D(row["entry_price"])
                    * exit_rate
                )
                if (D(row["entry_fee_usdt"]) != entry_fee
                        or D(row["exit_fee_usdt"]) != exit_fee
                        or D(row["gross_usdt"]) - entry_fee - exit_fee
                        != D(row["net_usdt"])):
                    raise ValueError("per-trade actual fee/net mismatch")
            for row in item["open_censored"]:
                by_times.append((D(row["entry_ts"]), 1, row["side"],
                                 row["symbol"]))
            by_times.sort(key=lambda x: (x[0], x[1], x[3]))
            running = {}
            maxsame = 0
            for _, kind, side, symbol in by_times:
                if kind == 0:
                    if symbol not in running:
                        raise ValueError("close without currently open position")
                    del running[symbol]
                else:
                    if symbol in running:
                        raise ValueError("duplicate same symbol overlap")
                    running[symbol] = side
                same = Counter(running.values())
                maxsame = max(maxsame, *same.values(), 0)
                if maxsame > cap:
                    raise ValueError("actual ledger intervals violate native cap")
            if len(running) != len(item["open_censored"]):
                raise ValueError("open positions at end do not reconcile")
            actual_net = sum(
                (D(row["net_usdt"]) for row in ledgers[name]), D(0)
            )
            fees_total = sum(
                (D(row["entry_fee_usdt"]) + D(row["exit_fee_usdt"])
                 for row in ledgers[name]), D(0)
            )
            m = item["money"]
            if (actual_net != D(m["closed_net_usdt"])
                    or D(m["gross_usdt"]) - D(m["closed_entry_fee_usdt"])
                        - D(m["exit_fee_usdt"]) != actual_net
                    or D(item["wallet_usdt"]) - engine.START_WALLET
                        != D(m["gross_usdt"]) - D(m["all_entry_fee_usdt"])
                        - D(m["exit_fee_usdt"])
                    or item["counts"]["qualified_candidate"] != (
                        item["counts"]["accepted"]
                        + item["counts"].get("busy_or_same_tick", 0)
                        + item["counts"].get("paused_candidate", 0)
                        + item["counts"].get("capital_blocked_candidate", 0))
                    or item["same_side_cap_blocked_candidate_count"]
                        > item["counts"].get("busy_or_same_tick", 0)
                    or fees_total != D(m["closed_entry_fee_usdt"]) + D(m["exit_fee_usdt"])):
                raise ValueError("independent cash/fees/candidate count reconciliation")
            if name == "CAP5__ORIGINAL_MAKER_TP":
                if ledgers[name] != reference:
                    raise ValueError("CAP5 original native frozen ledger differ")
                original = frozen["cases"][NATIVE_CASE]
                if (item["money"] != original["money"]
                        or item["wallet_usdt"] != original["wallet_usdt"]
                        or item["open_censored"] != original["open_censored"]
                        or item["equity_return_pct"] != original["equity_return_pct"]):
                    raise ValueError("CAP5 original source result parity drift")
            case_qc[name] = {
                "cap_respected": True, "max_recomputed_same_side": maxsame,
                "fee_model": fee, "closed": closed,
                "native_money_parity": True, "native_cost_parity": True,
                "blocked_by_side_cap": item["same_side_cap_blocked_candidate_count"],
            }
        proof = {
            "status": "INDEPENDENT_QC_PASS_P90L90_SAME_SIDE_CAP_V1",
            "period": period, "producer_head": commit,
            "cases": case_qc, "case_count": len(case_qc),
            "full_daily_checkpoint_chain_sha256": chain,
            "result_sha256": status["result_sha256"],
            "ledger_sha256": status["ledger_sha256"],
            "native_frozen_cap5_original_full_parity": True,
            "research_only": True,
        }
        write_json_immutable(root / "qc.json", proof)
        engine.prev.atomic_status(root, {
            **status, "stage": "COMPLETED_INDEPENDENT_QC_PASS",
            "qc_sha256": digest(root / "qc.json"),
        })
        return proof


def run(period: str, commit: str, mode: str, smoke_minutes: int) -> dict:
    pins, input_data = readonly_preflight(period, commit)
    if mode == "preflight":
        return {
            "stage": "READ_ONLY_PREPARED",
            "period": period, "head": commit,
            "source_pins": pins,
            "candidate_counts": {s: input_data[s]["depth_counts"]
                                 for s in engine.SYMBOLS},
            "free_gib": round(shutil.disk_usage(output(period).parent).free / 2**30, 3),
            "output_leaf_absent": not output(period).exists(),
        }
    books, refs = make_books(period)
    seen = {s: 0 for s in engine.SYMBOLS}
    prices = {}
    if mode == "smoke":
        r = engine.replay_day(
            period, 0, input_data, books, refs, seen, prices,
            smoke_minutes * 60
        )
        return {
            "stage": "SMOKE_PASS_NO_MUTATION", "period": period,
            "ticks": r["ticks"], "books": len(books),
            "maxsame": {k: b.max_same_side_slots for k, b in books.items()},
            "fee_restored": engine.old.MAKER == ORIGINAL_MAKER,
        }
    if mode != "run":
        raise ValueError("unknown execution mode")
    root = output(period)
    if not root.exists():
        root.mkdir(mode=0o2770)
        (root / "checkpoints").mkdir(mode=0o2770)
        (root / "source_snapshot").mkdir(mode=0o2770)
        for rel, digest_expected in pins.items():
            dst = root / "source_snapshot" / Path(rel).name
            with dst.open("xb") as f:
                f.write((SOURCE / rel).read_bytes())
            if digest(dst) != digest_expected:
                raise ValueError("immutable producer source snapshot mismatch")
        write_json_immutable(root / "manifest.json", {
            "research": RESEARCH, "source_head": commit,
            "source_pins": pins, "period": period,
            "evaluation_days": engine.WINDOW[period]["days"],
            "native_data_root": str(engine.WINDOW[period]["root"]),
            "cases": list(CASES), "caps": list(CAPS), "fee_modes": list(FEES),
            "old_original_case": NATIVE_CASE,
            "research_only": True,
        })
    else:
        if root.is_symlink() or not (root / "manifest.json").is_file():
            raise ValueError("unexpected existing research output directory")
        m = json.loads((root / "manifest.json").read_text())
        if (m.get("research") != RESEARCH or m.get("period") != period
                or m.get("source_head") != commit or m.get("source_pins") != pins):
            raise ValueError("foreign producer/source output cannot be resumed")
        if (root / "result.json").exists():
            raise FileExistsError("native result already completed, no replay")
    import fcntl
    with (root / ".writer.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        last, chain = scan_checkpoints(root, period, pins)
        start = 0
        if last is not None:
            start = last["days_done"]
            seen = last["source_seen"]
            prices = {s: D(v) for s, v in last["last_native_prices"].items()}
            for name, saved in last["books"].items():
                restore(books[name], saved)
            for name, saved in last["reference_books"].items():
                engine.prev.restore_book(refs[name], saved)
        for day in range(start, engine.WINDOW[period]["days"]):
            if shutil.disk_usage(root).free < MIN_FREE_GIB * 2**30:
                raise RuntimeError("Data-disk reserve reached (<5 GiB)")
            print(json.dumps({
                "stage": "DAY_START", "period": period,
                "day": day + 1, "of": engine.WINDOW[period]["days"],
            }), flush=True)
            r = engine.replay_day(period, day, input_data, books,
                                  refs, seen, prices)
            prices = r["prices"]
            item = checkpoint(
                period, day + 1, pins, seen, prices, books, refs, chain
            )
            raw = gzip.compress(
                json.dumps(item, sort_keys=True, separators=(",", ":"),
                           allow_nan=False).encode(),
                compresslevel=5, mtime=0
            )
            path = root / "checkpoints" / f"day_{day + 1:03d}.json.gz"
            engine.prev.write_immutable(path, raw)
            chain = hashlib.sha256(raw).hexdigest()
            engine.prev.atomic_status(root, {
                "stage": "RUNNING", "source_head": commit,
                "period": period, "days_done": day + 1,
                "days_total": engine.WINDOW[period]["days"],
                "last_checkpoint_sha256": chain,
                "free_gib": round(shutil.disk_usage(root).free / 2**30, 3),
            })
            print(json.dumps({
                "stage": "DAY_COMMITTED", "period": period,
                "days_done": day + 1,
                "days_total": engine.WINDOW[period]["days"],
                "ticks": r["ticks"], "checkpoint_sha256": chain,
            }), flush=True)
        # Bind no global reference to input_data for the resulting publisher.
        for sym in engine.SYMBOLS:
            expected = sum(map(len, input_data[sym]["events"].values()))
            if seen[sym] != expected:
                raise ValueError("source tick candidate coverage drift")
        # Engine reference controls were replayed under original source.
        controls = engine.compare_reference(period, refs)
        frozen_status, original, original_rows = provenance(period)
        if books["CAP5__ORIGINAL_MAKER_TP"].trades != original_rows:
            raise ValueError("CAP5 exact historical original ledger parity fails")
        finish_ts = (
            datetime.fromisoformat(engine.WINDOW[period]["start"]).replace(tzinfo=UTC)
            + timedelta(days=engine.WINDOW[period]["days"])
        ).timestamp()
        cases = {name: book.finish_unified(prices, finish_ts)
                 for name, book in books.items()}
        oldcase = original["cases"][NATIVE_CASE]
        baseline = cases["CAP5__ORIGINAL_MAKER_TP"]
        for k in ("wallet_usdt", "wallet_net_usdt",
                  "equity_with_open_unrealized_gross_usdt",
                  "reserved_margin_usdt", "open_censored", "money",
                  "hardstop_count", "max_simultaneous_slots"):
            if baseline[k] != oldcase[k]:
                raise ValueError("original CAP5 frozen full-result mismatch " + k)
        ledger_path = root / "trade_ledger.jsonl"
        with ledger_path.open("x") as f:
            for name in CASES:
                for row in books[name].trades:
                    f.write(json.dumps({
                        **row, "research_case": name
                    }, sort_keys=True, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        result = {
            "status": "COMPLETE_PENDING_INDEPENDENT_QC",
            "research": RESEARCH, "period": period,
            "source_head": commit, "source_pins": pins,
            "cases": cases, "case_count": len(cases),
            "original_cap5_trade_by_trade_parity": True,
            "original_reference_trade_parity": controls,
            "original_frozen_ledger_sha256": original["ledger_sha256"],
            "original_frozen_result_sha256": frozen_status["result_sha256"],
            "last_source_prices": {s: str(v) for s, v in prices.items()},
            "source_events_seen": seen,
            "final_checkpoint_sha256": chain,
            "ledger_sha256": digest(ledger_path),
            "fee_baseline": "TAKER_TAKER",
            "fee_sensitivity": "ORIGINAL_MAKER_TP",
            "shared_wallet_initial": "1000",
            "one_slot_per_coin": True,
            "native_execution_not_real": True,
            "model_exclusions": ["spread", "slippage", "funding", "latency",
                                 "actual_exchange_fills",
                                 "future_exit_fees_of_open_terminal_positions"],
            "research_only": True,
            "not_pristine_blinded_oos": True,
        }
        write_json_immutable(root / "result.json", result)
        engine.prev.atomic_status(root, {
            "stage": "COMPLETE_PENDING_INDEPENDENT_QC",
            "source_head": commit, "period": period,
            "days_done": engine.WINDOW[period]["days"],
            "days_total": engine.WINDOW[period]["days"],
            "result_sha256": digest(root / "result.json"),
            "ledger_sha256": digest(ledger_path),
        })
    # Writer lock released, independent audit gets fresh Python/import state.
    command = [sys.executable, "-B", str(Path(__file__).resolve()),
               "--period", period, "--source-head", commit, "--mode", "qc"]
    try:
        child = subprocess.run(
            command, cwd=str(SOURCE), text=True,
            capture_output=True, timeout=1800, check=False
        )
        if child.returncode != 0:
            raise RuntimeError("QC exit code " + str(child.returncode)
                               + "\n" + child.stderr[-6000:])
        report = json.loads(child.stdout.strip().splitlines()[-1])
        status = json.loads((root / "status.json").read_text())
        if (report["status"] != "INDEPENDENT_QC_PASS_P90L90_SAME_SIDE_CAP_V1"
                or status["stage"] != "COMPLETED_INDEPENDENT_QC_PASS"
                or status["qc_sha256"] != digest(root / "qc.json")):
            raise ValueError("QC did not publish verifiable successful artifacts")
    except Exception as exc:
        try:
            write_json_immutable(root / "qc_failure.json", {
                "stage": "INDEPENDENT_QC_FAILED", "source_head": commit,
                "period": period, "kind": type(exc).__name__,
                "exception": str(exc)[-12000:],
            })
        except FileExistsError:
            pass
        raise
    return {"period": period, "stage": "COMPLETED_INDEPENDENT_QC_PASS",
            "source_head": commit, "cases": len(cases),
            "qc_sha256": status["qc_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--period", choices=("29d", "90d", "183d"),
                        required=True)
    parser.add_argument("--mode", choices=("preflight", "smoke", "run", "qc"),
                        required=True)
    parser.add_argument("--smoke-minutes", type=int, default=5)
    a = parser.parse_args()
    if len(a.source_head) != 40 or any(
        c not in "0123456789abcdef" for c in a.source_head
    ):
        raise ValueError("exact 40-hex GitHub source HEAD required")
    if a.mode == "qc":
        output_item = qc_main(a.period, a.source_head)
    else:
        if not 1 <= a.smoke_minutes <= 120:
            raise ValueError("smoke is 1..120 minutes")
        output_item = run(
            a.period, a.source_head, a.mode, a.smoke_minutes
        )
    print(json.dumps(output_item, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
