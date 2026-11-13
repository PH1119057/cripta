#!/usr/bin/env python3
"""#638: read-only, native ledger positional-overlap / hardstop cluster forensic.

One exact shared LONG+SHORT portfolio, five coins, real native event timestamps
and recorded V2 modeled cash/margin semantics; NOT a new strategy or replay.

ACTOR/PATH CONTRACT:
  effective actor: cripta UID107/GID109, python3 -B;
  READ only three exact approved V2 research result leaves on /data/cripta,
    status/result/ledger/qc and full immutable chained daily checkpoints;
  WRITE/CREATE/RENAME/DELETE: NONE. JSON report to stdout only;
  DB/Exchange/systemd/trading runtime/Strategy/LIVE: NONE.
  Source: isolated detached, clean, exact verified GitHub main commit.

Stop overlap and daily clusters are different:
  1) interval overlap: both positions were simultaneously open according to
     native exact Entry/Exit time, including terminal censored positions;
  2) daily group: two+ Hard Stops EXIT on same UTC calendar day, whether their
     positions overlapped or not.
The first group's transitive overlap components are explicitly labelled as
such; a connected component need not have all positions open simultaneously.
No counterfactual "saved profit" is inferred. All money after modeled fees.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.research import p90l90_unified_portfolio_forensic_v1 as audit  # noqa: E402

CASE = audit.FOCUS
SYMBOLS = frozenset(("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"))
UTC = timezone.utc


def rounded_number(value: D, digits: int = 4) -> float:
    return round(float(value), digits)


def day(ts: D) -> str:
    return datetime.fromtimestamp(float(ts), UTC).date().isoformat()


def normalize(
    closed: list[dict], censored: list[dict], end_ts: D,
) -> list[dict]:
    """All accepted positions, including censored end-of-window positions."""
    out = []
    used = set()
    for ix, raw in enumerate(closed):
        uid = str(raw["entry_trade_id"])
        item = {
            "id": uid, "symbol": raw["symbol"], "side": raw["side"],
            "start": D(raw["entry_ts"]), "end": D(raw["exit_ts"]),
            "margin": D(raw["margin_usdt"]),
            "notional": D(raw["notional_usdt"]),
            "closed": True, "stop": raw["exit_reason"] == "HARD_STOP",
            "exit_reason": raw["exit_reason"], "net": D(raw["net_usdt"]),
        }
        out.append(item)
    for ix, raw in enumerate(censored):
        uid = "CENSORED:" + str(raw["entry_event_id"])
        item = {
            "id": uid, "symbol": raw["symbol"], "side": raw["side"],
            "start": D(raw["entry_ts"]), "end": end_ts,
            "margin": D(raw["margin"]), "notional": D(raw["notional"]),
            "closed": False, "stop": False,
            "exit_reason": "OPEN_CENSORED", "net": None,
        }
        out.append(item)
    for x in out:
        if (x["id"] in used or x["symbol"] not in SYMBOLS
                or x["side"] not in ("LONG", "SHORT")
                or not D(0) < x["start"] < x["end"] <= end_ts
                or x["margin"] <= 0 or x["notional"] <= 0
                or x["notional"] < x["margin"]
                or (x["closed"] and x["net"] is None)):
            raise ValueError("invalid/duplicate native accepted position interval")
        used.add(x["id"])
    return out


def sweep(positions: list[dict], period_start: D, period_end: D) -> dict:
    """Exact half-open [Entry, Exit) occupancy; Exit precedes Entry at ties."""
    changes = []
    for p in positions:
        changes.extend(((p["start"], 1, p), (p["end"], 0, p)))
    changes.sort(key=lambda x: (x[0], x[1], x[2]["id"]))
    active = {}
    seconds = {str(i): D(0) for i in range(6)}
    seconds_same_side_3plus = D(0)
    peak_slots = peak_same = 0
    peak_notional = peak_same_notional = D(0)
    prev = period_start
    i = 0
    while i < len(changes):
        t = changes[i][0]
        if t < prev or t > period_end:
            raise ValueError("events outside of exact evaluation period")
        duration = t - prev
        seconds[str(len(active))] += duration
        by_side = {"LONG": [], "SHORT": []}
        for p in active.values():
            by_side[p["side"]].append(p)
        if any(len(x) >= 3 for x in by_side.values()):
            seconds_same_side_3plus += duration
        j = i
        while j < len(changes) and changes[j][0] == t:
            _, kind, p = changes[j]
            if kind == 0:
                if p["id"] not in active:
                    raise ValueError("closing a position that is not active")
                del active[p["id"]]
            else:
                if p["id"] in active or any(
                    a["symbol"] == p["symbol"] for a in active.values()
                ):
                    raise ValueError("same-symbol simultaneous position contract violated")
                active[p["id"]] = p
            j += 1
        if len(active) > 5:
            raise ValueError("more than five physical simultaneous slots")
        peak_slots = max(peak_slots, len(active))
        peak_notional = max(peak_notional, sum(
            (p["notional"] for p in active.values()), D(0)
        ))
        by_side = {"LONG": [], "SHORT": []}
        for p in active.values():
            by_side[p["side"]].append(p)
        peak_same = max(peak_same, *(len(x) for x in by_side.values()))
        peak_same_notional = max(peak_same_notional, *(
            sum((p["notional"] for p in x), D(0)) for x in by_side.values()
        ))
        prev = t
        i = j
    if active:
        raise ValueError("positions not closed/censored at period end")
    seconds["0"] += period_end - prev
    total = period_end - period_start
    if sum(seconds.values(), D(0)) != total:
        raise ValueError("occupancy duration not reconciled")
    return {
        "max_simultaneous_slots": peak_slots,
        "max_same_side_slots": peak_same,
        "max_total_notional_usdt": audit.money(peak_notional),
        "max_same_side_notional_usdt": audit.money(peak_same_notional),
        "time_fraction_pct_by_active_slots": {
            n: rounded_number(D(100) * dur / total)
            for n, dur in seconds.items()
        },
        "time_fraction_pct_with_3plus_same_side": rounded_number(
            D(100) * seconds_same_side_3plus / total
        ),
        "avg_active_slots_time_weighted": rounded_number(
            sum(D(k) * sec for k, sec in seconds.items()) / total
        ),
    }


def peers_at(positions: list[dict], point: D, own_id: str) -> list[dict]:
    """Peers *strictly* open at time; no ambiguous same-timestamp Entry/Exit."""
    return [
        x for x in positions if x["id"] != own_id
        and x["start"] < point < x["end"]
    ]


def exposure(p: dict, positions: list[dict], at: str) -> dict:
    t = p["start"] if at == "entry" else p["end"]
    peers = peers_at(positions, t, p["id"])
    same = [x for x in peers if x["side"] == p["side"]]
    return {
        "all": 1 + len(peers),
        "same": 1 + len(same),
        "opposite": len(peers) - len(same),
        "same_notional": p["notional"] + sum(
            (x["notional"] for x in same), D(0)
        ),
        "total_notional": p["notional"] + sum(
            (x["notional"] for x in peers), D(0)
        ),
    }


def stop_overlap_components(stops: list[dict]) -> list[dict]:
    """Transitive components where each graph edge is *actual* interval overlap."""
    n = len(stops)
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i: int, j: int) -> None:
        a, b = root(i), root(j)
        if a != b:
            parent[b] = a

    for i in range(n):
        for j in range(i + 1, n):
            a, b = stops[i], stops[j]
            if max(a["start"], b["start"]) < min(a["end"], b["end"]):
                join(i, j)
    groups = defaultdict(list)
    for i, p in enumerate(stops):
        groups[root(i)].append(p)
    out = []
    for group in groups.values():
        if len(group) < 2:
            continue
        first_stop = min(x["end"] for x in group)
        first_open = [
            x for x in group
            if x["start"] < first_stop and x["end"] >= first_stop
        ]
        # Peak can be smaller than component size if overlap is transitive.
        times = sorted({t for p in group for t in (p["start"], p["end"])})
        peak_victims = max(
            sum(p["start"] <= t < p["end"] for p in group)
            for t in times
        )
        out.append({
            "first_stop_utc": datetime.fromtimestamp(
                float(first_stop), UTC
            ).isoformat(),
            "component_stop_positions": len(group),
            "peak_stop_victims_concurrently_open": peak_victims,
            "already_open_victims_before_first_stop": len(first_open),
            "first_stop_victim_sides": sorted(x["side"] for x in first_open),
            "all_stop_victim_symbols_side": sorted(
                x["symbol"] + ":" + x["side"] for x in group
            ),
            "net_hardstop_loss_after_fees_usdt": audit.money(
                sum((x["net"] for x in group), D(0))
            ),
            "all_victims_same_side": len({x["side"] for x in group}) == 1,
        })
    return sorted(out, key=lambda x: x["first_stop_utc"])


def utc_day_groups(closed: list[dict]) -> list[dict]:
    stops = defaultdict(list)
    all_exits = defaultdict(list)
    for x in closed:
        d = day(x["end"])
        all_exits[d].append(x)
        if x["stop"]:
            stops[d].append(x)
    out = []
    for d, group in sorted(stops.items()):
        if len(group) < 2:
            continue
        first = min(x["end"] for x in group)
        preexisting = [x for x in group if x["start"] < first]
        stopped_loss = sum((x["net"] for x in group), D(0))
        out.append({
            "utc_exit_day": d,
            "hardstops_on_day": len(group),
            "same_day_stops_all_preexisting_at_first": len(preexisting) == len(group),
            "stop_victims_preexisting_before_first_stop": len(preexisting),
            "preexisting_victims_same_side": len({
                x["side"] for x in preexisting
            }) == 1 if preexisting else False,
            "hardstop_net_after_fees_usdt": audit.money(stopped_loss),
            "preexisting_hardstop_net_after_fees_usdt": audit.money(
                sum((x["net"] for x in preexisting), D(0))
            ),
            "whole_utc_day_closed_net_after_fees_usdt": audit.money(
                sum((x["net"] for x in all_exits[d]), D(0))
            ),
            "symbol_side": sorted(x["symbol"] + ":" + x["side"] for x in group),
        })
    return out


def stop_exposure_by_entry(closed: list[dict], positions: list[dict]) -> dict:
    bins = {str(k): [] for k in range(1, 6)}
    for p in closed:
        exp = exposure(p, positions, "entry")
        if exp["all"] > 5 or exp["same"] > 5:
            raise ValueError("Entry exposure exceeds five symbols")
        bins[str(exp["same"])].append(p)
    return {
        label: {
            "closed": len(rows),
            "hardstops": sum(x["stop"] for x in rows),
            "hardstop_rate_pct": (
                rounded_number(D(100) * sum(x["stop"] for x in rows) / len(rows))
                if rows else None
            ),
            "closed_net_after_fees_usdt": audit.money(
                sum((x["net"] for x in rows), D(0))
            ),
        } for label, rows in bins.items()
    }


def stop_exposure_by_exit(stops: list[dict], positions: list[dict]) -> dict:
    bins = {str(k): [] for k in range(1, 6)}
    for x in stops:
        exp = exposure(x, positions, "exit")
        if exp["all"] > 5 or exp["same"] > 5:
            raise ValueError("Stop exposure exceeds five physical slots")
        bins[str(exp["same"])].append(x)
    return {
        label: {
            "hardstops": len(rows),
            "hardstop_net_after_fees_usdt": audit.money(
                sum((x["net"] for x in rows), D(0))
            ),
        } for label, rows in bins.items()
    }


def analyze(periods: tuple[str, ...]) -> dict:
    if os.name != "posix" or (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("this reader requires cripta UID107:GID109")
    report = {}
    for period in periods:
        status, result, qc, ledger, daily = audit.read_verified(period)
        original = result["cases"][CASE]
        rows = ledger[CASE]
        end = datetime.combine(
            audit.START_UTC[period] + timedelta(days=int(period[:-1])),
            datetime.min.time(), UTC
        )
        start = datetime.combine(audit.START_UTC[period], datetime.min.time(), UTC)
        start_ts, end_ts = D(str(start.timestamp())), D(str(end.timestamp()))
        positions = normalize(rows, original["open_censored"], end_ts)
        if (len(positions) != int(original["counts"]["accepted"])
                or len(rows) != int(original["counts"]["closed"])):
            raise ValueError("native accepted vs closed/censored mismatch")
        if any(p["start"] < start_ts for p in positions):
            raise ValueError("Entry outside evaluation interval")
        occupancy = sweep(positions, start_ts, end_ts)
        if occupancy["max_simultaneous_slots"] != int(
            original["max_simultaneous_slots"]
        ):
            raise ValueError("native max simultaneous slots QC mismatch")
        stops = [x for x in positions if x["stop"]]
        closed = [x for x in positions if x["closed"]]
        if len(stops) != int(original["hardstop_count"]):
            raise ValueError("native HARD_STOP count QC mismatch")
        if sum((x["net"] for x in closed), D(0)) != D(
            original["money"]["closed_net_usdt"]
        ):
            raise ValueError("native ledger monetary QC mismatch")
        comps = stop_overlap_components(stops)
        cluster_ids = set()
        for component in comps:
            names = set(component["all_stop_victim_symbols_side"])
            # Stop positions on same coin may occur at different times; do NOT
            # identify components via symbol/side. Monetary sum below is from
            # actual components returned by independently computed function.
        # Independently re-evaluate component money (all stopped members are
        # disjoint across connected components); numeric aggregate is in comps.
        component_loss = sum(
            (D(c["net_hardstop_loss_after_fees_usdt"]) for c in comps), D(0)
        )
        all_stop_loss = sum((x["net"] for x in stops), D(0))
        day_bursts = utc_day_groups(closed)
        report[period] = {
            "source_head": status["source_head"],
            "source_ledger_sha256": status["ledger_sha256"],
            "days_chain_qc": status["days_done"],
            "accepted_positions": len(positions),
            "closed_trades": len(closed),
            "terminal_open_censored": len(positions) - len(closed),
            "hardstops": len(stops),
            "all_hardstop_net_after_fees_usdt": audit.money(all_stop_loss),
            "all_closed_net_after_fees_usdt": audit.money(
                sum((x["net"] for x in closed), D(0))
            ),
            "occupation": occupancy,
            "same_side_at_entry_including_own": stop_exposure_by_entry(
                closed, positions
            ),
            "same_side_still_open_just_before_stop_including_own":
                stop_exposure_by_exit(stops, positions),
            "overlapping_hardstop_victim_components": comps,
            "overlap_components_count": len(comps),
            "stops_in_overlap_components": sum(
                x["component_stop_positions"] for x in comps
            ),
            "stop_loss_in_overlap_components_after_fees_usdt": (
                audit.money(component_loss)
            ),
            "share_of_hardstop_loss_from_overlap_components_pct": (
                rounded_number(D(100) * component_loss / all_stop_loss)
                if all_stop_loss < 0 else None
            ),
            "multi_stop_utc_exit_days": day_bursts,
            "multi_stop_utc_exit_days_count": len(day_bursts),
            "all_preexisting_same_day_stop_bursts": sum(
                g["same_day_stops_all_preexisting_at_first"]
                for g in day_bursts
            ),
            "second48_pause_trigger_count": original["pause_trigger_count"],
            "second48_paused_candidate_count": (
                original["counts"].get("paused_candidate", 0)
            ),
        }
    return {
        "research": "P90L90_NATIVE_POSITION_OVERLAP_HARDSTOP_FORENSIC_V1",
        "source": "independently QC-PASS V2 3×8 native research books",
        "case": CASE,
        "position_semantics": "half-open [Entry,Exit), terminal censored open until period boundary",
        "stop_snapshot": "subject stop included; other positions strictly open at stop timestamp",
        "overlap_group_semantics": (
            "transitive connected components of actual concurrent stop-victim"
            " position intervals; peak simultaneous subset reported separately"
        ),
        "periods": report,
        "limitations": [
            "Historical overlapping positions are modeled fills, not real Exchange positions.",
            "Same-day burst != simultaneous positions; same UTC timestamp has unknown cross-symbol tick ordering.",
            "After-fees amounts are ORIGINAL modeled native outcomes, NOT causal savings or counterfactual PnL.",
            "Only five symbols; no BTC/ETH or whole-market exposure in this calculation.",
            "No spread, slippage, funding, execution latency or final open-position exit fees.",
            "Simultaneous concentration is not an approved new entry cap/stop/Strategy rule.",
            "Three previously studied periods are NOT independent prospective OOS.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--period", choices=("29d", "90d", "183d", "all"), default="183d"
    )
    args = parser.parse_args()
    periods = ("29d", "90d", "183d") if args.period == "all" else (args.period,)
    print(json.dumps(
        analyze(periods), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ))


if __name__ == "__main__":
    main()
