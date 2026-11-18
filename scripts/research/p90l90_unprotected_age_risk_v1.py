#!/usr/bin/env python3
"""#839 causal age-risk snapshots of the ORIGINAL P90L90 native-tick books.

Replays immutable #646 reference driver with TIME exits disabled; retains its
exact source ingestion, Entry/Exit, SECOND48, original parity, and checkpoint.
Records attributes only while an original position is still open after the
native Exit decision on the first print at/after ages 3,6,12,24 hours.
H9 metadata is ENTRY-time only; CURRENT L53 is causal 5-minute closed state.
Future outcome is attached only after replay, as a LABEL, never as a feature.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
from collections import Counter
from decimal import Decimal as D

ROOT = pathlib.Path.cwd().resolve()
AGES_H = (3, 6, 12, 24)
FEE = D("0.055")
INPUT_DRIVER = {
    "183d": (
        "/data/cripta/jobs/completed/C2-R646-H25-NEG-20261114/"
        "bundle/source_snapshot/p90l90_holding_time_2025_v1.py",
        "d198e4784dd28036f3a33a9356ffd2118b7c3585ec8b0a9ffee1d089741f9fa5",
    ),
    "90d": (
        "/data/cripta/jobs/completed/C2-R646-H90-NEG-20261114/"
        "bundle/source_snapshot/p90l90_holding_time_2026_v1.py",
        "a9be1df523b48a3368a10fef62f2fedd052be770280aa032b0e7d167bdcdd511",
    ),
    "29d": (
        "/data/cripta/jobs/completed/C2-R646-H29-NEG-20261114/"
        "bundle/source_snapshot/p90l90_holding_time_2026_v1.py",
        "a9be1df523b48a3368a10fef62f2fedd052be770280aa032b0e7d167bdcdd511",
    ),
}
OBS_ROWS: list[dict] = []
OBS_KEYS: set[str] = set()
ENTRY_META: dict[str, dict] = {}


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_entry_metadata(data: dict) -> None:
    ENTRY_META.clear()
    for symbol, record in data.items():
        for events in record["events"].values():
            for event in events:
                if not event.get("PREV7D"):
                    continue
                key = event["event_id"]
                if key in ENTRY_META:
                    raise ValueError("duplicate immutable Entry metadata")
                ENTRY_META[key] = {
                    field: str(event.get(field, "")) for field in (
                        "depth_pct", "raw_h9_union_span",
                        "h9_5m_high", "h9_5m_low",
                        "h9_15m_high", "h9_15m_low",
                        "l53_high_raw", "l53_low_raw",
                    )
                }


def observe(book, symbol: str, stamp: float, seq: int, price: D, state) -> None:
    pos = book.pos[symbol]
    if pos is None:
        return
    if state is None:
        raise ValueError("current causal L53 missing")
    meta = ENTRY_META.get(pos.entry_event_id)
    if meta is None:
        raise ValueError("entry-time H9 source metadata missing")
    signed = book.side == "LONG"
    gross_pct = (price / pos.entry - D(1)) * D(100) * (D(1) if signed else D(-1))
    mark_net_pct = gross_pct - FEE - price / pos.entry * FEE
    if pos.protected != (pos.best_favorable >= D("0.50")):
        raise ValueError("protected state does not agree with causal MFE")
    for age in AGES_H:
        if seq <= pos.entry_seq or stamp < pos.entry_ts + age * 3600:
            continue
        key = pos.entry_event_id + "|" + str(age)
        if key in OBS_KEYS:
            continue
        OBS_KEYS.add(key)
        OBS_ROWS.append({
            "entry_event_id": pos.entry_event_id,
            "symbol": symbol, "side": pos.side,
            "entry_ts": repr(pos.entry_ts), "entry_price": str(pos.entry),
            "age_gate_h": age, "decision_ts": repr(stamp),
            "decision_source_seq": seq, "decision_price": str(price),
            "gross_favorable_pct": str(gross_pct),
            "net_taker_taker_if_closed_now_pct": str(mark_net_pct),
            "mfe_before_or_at_decision_pct": str(pos.best_favorable),
            "protected_by_original_trail": bool(pos.protected),
            "unprotected_negative": not pos.protected and mark_net_pct <= 0,
            "current_closed5m_l53_low_raw": str(state[0]),
            "current_closed5m_l53_high_raw": str(state[1]),
            "entry_time_h9": meta,
        })


def load_original(period: str):
    path_text, expect = INPUT_DRIVER[period]
    path = pathlib.Path(path_text)
    if not path.is_file() or path.is_symlink() or digest(path) != expect:
        raise RuntimeError("original #646 immutable source SHA changed")
    spec = importlib.util.spec_from_file_location("r839_original_"+period, path)
    if spec is None or spec.loader is None:
        raise ImportError("immutable original module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(module, period: str, wrapper_path: pathlib.Path) -> None:
    prefix = {
        "183d": "C2-R839-A25-",
        "90d": "C2-R839-A90-",
        "29d": "C2-R839-A29-",
    }[period]
    expected_root = pathlib.Path("/data/cripta/jobs/running") / os.environ.get(
        "CRIPTA_JOB_ID", ""
    ) / "bundle"
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("research actor must be cripta uid107/gid109")
    if not os.environ.get("CRIPTA_JOB_ID", "").startswith(prefix):
        raise ValueError("wrong immutable job identity")
    if ROOT != expected_root or ROOT.is_symlink() or ROOT.stat().st_uid != 107:
        raise PermissionError("outside approved research spool")
    if os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize < 5 * 2**30:
        raise RuntimeError("research disk reserve below 5GiB")
    source = ROOT / "source_snapshot" / "p90l90_unprotected_age_risk_v1.py"
    if source.resolve() != wrapper_path.resolve() or source.is_symlink():
        raise RuntimeError("wrapper source identity mismatch")
    if module.base.on_trade is not module.ORIGINAL_ON_TRADE:
        raise RuntimeError("original Exit callback already changed")
    if module.base.take_profit_eligible is not module.ORIGINAL_TP_ELIGIBILITY if period != "183d" else module.base.take_profit_eligible is not module.ORIGINAL_ELIGIBLE:
        raise RuntimeError("original structural TP callback changed")


def export_snapshot(period: str, module, result_dir: pathlib.Path) -> dict:
    results = json.loads((result_dir / "result.json").read_text())
    if not results["baseline_exact_ledger_parity"]:
        raise RuntimeError("original baseline exact ledger parity did not pass")
    if results["status"] != "RESEARCH_INTERNAL_QC_PASS":
        raise RuntimeError("original native replay internal QC failed")
    future = {}
    for direction in ("LONG_ONLY", "SHORT_ONLY"):
        original = results["scenarios"][direction + "|NO_CAP"]
        filename = result_dir / (direction.lower() + "_no_cap.jsonl")
        if digest(filename) != original["ledger_sha256"]:
            raise RuntimeError("baseline original ledger sha mismatch")
        for record in (json.loads(line) for line in filename.read_text().splitlines() if line):
            ident = record["candidate_event_id"]
            if ident in future:
                raise ValueError("duplicate baseline trade ID")
            future[ident] = (record["exit_reason"], record["net_usdt"], record["exit_ts"])
    counters = Counter()
    for row in OBS_ROWS:
        label = future.get(row["entry_event_id"])
        reason, original_net, exit_ts = label if label else ("CENSORED", None, None)
        row["FUTURE_ONLY_label_exit_reason"] = reason
        row["FUTURE_ONLY_label_net_usdt"] = original_net
        row["FUTURE_ONLY_label_exit_ts"] = exit_ts
        age = str(row["age_gate_h"])
        counters["AT_RISK_H"+age] += 1
        if not row["protected_by_original_trail"]:
            counters["UNPROTECTED_H"+age] += 1
        if row["unprotected_negative"]:
            key = "UNPROTECTED_NEG_H"+age
            counters[key] += 1
            if reason == "HARD_STOP":
                counters[key+"_FUTURE_HS"] += 1
            elif reason == "CENSORED":
                counters[key+"_CENSORED"] += 1
            else:
                counters[key+"_FUTURE_OTHER_EXIT"] += 1
    dest = result_dir / "age_risk_snapshots.jsonl"
    with dest.open("x", encoding="utf-8") as out:
        for row in OBS_ROWS:
            out.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        out.flush()
        os.fsync(out.fileno())
    report = {
        "status": "RESEARCH_INTERNAL_QC_PASS",
        "research": "P90L90_UNPROTECTED_AGE_RISK_V1",
        "period": period,
        "original_comparator_job": results["job_id"],
        "causal_original_baseline_parity": True,
        "snapshots": len(OBS_ROWS),
        "snapshot_file": dest.name,
        "snapshot_ledger_sha256": digest(dest),
        "counts": dict(counters),
        "H9_scope": "ENTRY_TIME_H9_STATIC_ONLY; current H9 not established",
        "current_L53": "causal original closed 5-minute RAW geometry",
        "future_labels": "retrospective outcome ONLY; forbidden as causal input",
        "execution_model": "original untouched Entry/Exit; no new trades or forced exits",
    }
    with (result_dir / "risk_summary.json").open("x", encoding="utf-8") as out:
        json.dump(report, out, sort_keys=True, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    return report


def run(period: str, smoke_hours: int, pilot_days: int) -> dict:
    wrapper = pathlib.Path(__file__)
    module = load_original(period)
    prepare(module, period, wrapper)
    OBS_ROWS.clear()
    OBS_KEYS.clear()
    ENTRY_META.clear()
    module.RESEARCH = "P90L90_UNPROTECTED_AGE_RISK_V1"
    module.PHASES = ("SNAP",)
    # None means original NO_CAP. Keep other books for exact original
    # engine's invariant and checkpoint schema; disable ALL time exits.
    module.should_force = lambda *args: False if period == "183d" else None
    if period != "183d":
        module.force_time_exit = lambda *args: False
    if period == "183d":
        original_inputs = module.sources

        def wrapped_inputs():
            data = original_inputs()
            add_entry_metadata(data)
            return data

        module.sources = wrapped_inputs
        original_save_day = module.save_day
        original_resume = module.resume

        def wrapped_save(day, state, sha):
            state["_r839_snapshots"] = OBS_ROWS
            return original_save_day(day, state, sha)

        def wrapped_resume(books, sha, data):
            state, start = original_resume(books, sha, data)
            OBS_ROWS[:] = state.get("_r839_snapshots", [])
            OBS_KEYS.update(
                row["entry_event_id"] + "|" + str(row["age_gate_h"])
                for row in OBS_ROWS
            )
            state["_r839_snapshots"] = OBS_ROWS
            return state, start

        module.save_day = wrapped_save
        module.resume = wrapped_resume
        old_handle = module.ORIGINAL_HANDLE

        def observed_handle(self, symbol, stamp, seq, price, events, state, priority):
            outcome = module.timed_handle(
                self, symbol, stamp, seq, price, events, state, priority
            )
            if self.cap_h is None:
                observe(self, symbol, stamp, seq, price, state)
            return outcome

        # Driver saves/restores Book.handle itself, so wrapping that assigned
        # handler by changing timed_handle is required to retain snapshots.
        module.timed_handle = observed_handle
        # Avoid recursion: observed_handle delegates to the original timed
        # handle implementation captured above.
        # The immutable original handle is separately pinned and restored.
        module._r839_unused = old_handle
        result = module.run("SNAP", pilot_days)
        sub = "2025"
    else:
        original_inputs = module.inputs

        def wrapped_inputs(period_arg):
            data, streams, start, end, references, job = original_inputs(period_arg)
            add_entry_metadata(data)
            return data, streams, start, end, references, job

        module.inputs = wrapped_inputs
        old_handle = module.TimeBook.handle

        def observed_handle(self, symbol, stamp, seq, price, candidates, state):
            outcome = old_handle(self, symbol, stamp, seq, price, candidates, state)
            if self.cap is None:
                observe(self, symbol, stamp, seq, price, state)
            return outcome

        module.TimeBook.handle = observed_handle
        result = module.run(period, "SNAP", smoke_hours)
        sub = period
    if pilot_days or smoke_hours:
        if not OBS_ROWS:
            raise RuntimeError("real-native smoke contains no risk snapshots")
        return {"status":"SMOKE_PASS_NO_WRITES","period":period,
                "native_replay":result, "risk_snapshot_rows":len(OBS_ROWS)}
    return export_snapshot(period, module, ROOT / sub)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=tuple(INPUT_DRIVER), required=True)
    parser.add_argument("--smoke-hours", type=int, default=0)
    parser.add_argument("--pilot-days", type=int, default=0)
    args = parser.parse_args()
    if args.period == "183d":
        if args.smoke_hours != 0 or args.pilot_days not in (0, 1):
            raise ValueError("2025 supports 1-day pilot only")
    elif args.pilot_days != 0 or args.smoke_hours not in (0, 6):
        raise ValueError("2026 supports 6-hour native smoke only")
    result = run(args.period, args.smoke_hours, args.pilot_days)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
