#!/usr/bin/env python3
"""Research-only #17: observed 5m signed-flow decay proxy, NOT exhaustion.

As-of at end of current 5m block. A preceding 2-block close direction
and prior/current signed aggressor turnover must agree in sign.
When eligible: abs(current signed flow) / abs(previous signed flow) < 1
describes current lower flow magnitude. No NEXT block or future label
is read for feature admission; no trading implication is inferred.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import pwd
import shutil
import sys
import time
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
REPO = pathlib.Path("/srv/cripta/source_checkout")
MANIFEST = REPO / "research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
OUT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_flow17_proxy_20261023")
FORMULA = "flow17_5m_current_to_prior_signed_notional_ratio_direction_qualified_v1"
COMPLETE = "FLOW17_PROXY_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()


def write_json_new(path, value):
    temp = path.with_name(path.name + ".part")
    with temp.open("x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temp.rename(path)


def set_terminal(path, status, result=None, error=None):
    obj = json.loads(path.read_text())
    if obj["status"] != "RUNNING" or status not in (COMPLETE, "FAILED"):
        raise ValueError("BAD_TERMINAL_TRANSITION")
    if (status == COMPLETE) != (result is not None):
        raise ValueError("BAD_SUCCESS_EVIDENCE")
    if status == "FAILED" and error is None:
        raise ValueError("MISSING_FAILURE")
    obj["status"] = status
    if result:
        obj.update({"rows": result["rows"], "output_sha256": result["output_sha256"],
                    "independent_qc": "PENDING"})
    else:
        obj["error_type"] = type(error).__name__
        obj["error_message"] = str(error)[:220]
    temp = path.with_name(path.name + ".terminal.part")
    with temp.open("x", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def source_files(sym, days):
    rows = json.loads(MANIFEST.read_text())["source_files"]
    same = [r for r in rows if r["symbol"] == sym]
    lookup = {r["date_utc"]: r for r in same}
    if len(same) != 30 or len(lookup) != 30:
        raise ValueError("SOURCE_MANIFEST_30D_MISMATCH")
    output = []
    for i in range(days):
        d = FIRST + dt.timedelta(days=i)
        p = RAW / sym / "public_trades" / (sym + d.isoformat() + ".csv.gz")
        if not p.is_file() or not os.access(p, os.R_OK):
            raise FileNotFoundError("MISSING_SOURCE " + str(p))
        output.append((d, p, lookup[d.isoformat()]))
    return output


def aggregate_day(date, source):
    base = Decimal(int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp()))
    blocks = [{"first": None, "last": None, "flow": Decimal(0), "count": 0}
              for _ in range(288)]
    before = None
    with gzip.open(source, "rt", newline="") as f:
        for row in csv.DictReader(f):
            t = Decimal(row["timestamp"])
            p = Decimal(row["price"])
            n = Decimal(row["foreignNotional"])
            side = row["side"]
            if (not t.is_finite() or not p.is_finite() or p <= 0
                    or not n.is_finite() or n < 0 or side not in ("Buy", "Sell")):
                raise ValueError("BAD_TRADE_FIELD")
            if before is not None and t < before:
                raise ValueError("NONMONOTONE_TRADE_TAPE")
            before = t
            k = int((t - base) // 300)
            if not 0 <= k < 288:
                raise ValueError("OUT_OF_UTC_DATE_TRADE")
            b = blocks[k]
            if b["first"] is None:
                b["first"] = p
            b["last"] = p
            b["flow"] += n if side == "Buy" else -n
            b["count"] += 1
    return blocks


def as_of_feature(pp, pr, cur):
    if pp is None or pr is None:
        return None, None, "INSUFFICIENT_PRECEDING_BLOCKS"
    if not pp["count"] or not pr["count"] or not cur["count"]:
        return None, None, "MISSING_OBSERVED_5M_BLOCK"
    if not pr["flow"] or not cur["flow"]:
        return None, None, "ZERO_SIGNED_FLOW"
    previous_move = pr["last"] - pp["last"]
    if previous_move == 0 or previous_move * cur["flow"] <= 0:
        return None, None, "FLOW_NOT_ALIGNED_WITH_PRIOR_PRICE_MOVE"
    if pr["flow"] * cur["flow"] <= 0:
        return None, None, "FLOW_DIRECTION_CHANGED"
    ratio = abs(cur["flow"]) / abs(pr["flow"])
    decay = ratio < 1
    return ratio, decay, ("ELIGIBLE_FLOW_DECAY" if decay else "ELIGIBLE_FLOW_NONDECAY")


def run(symbol, days):
    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("RESEARCH_ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("UNAPPROVED_SCOPE")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_NOT_WRITABLE")
    sources = source_files(symbol, days)
    dest = OUT / ("physical_" + str(days) + "d") / symbol
    if dest.exists():
        raise FileExistsError("IMMUTABLE_OUTPUT_EXISTS_DO_NOT_OVERWRITE")
    os.umask(0o007)
    OUT.mkdir(mode=0o2770, exist_ok=True)
    dest.parent.mkdir(mode=0o2770, exist_ok=True)
    dest.mkdir(mode=0o2770)
    (dest / "source_snapshot").mkdir(mode=0o2770)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, dest / "source_snapshot/materializer.py")
    code_sha = sha(script)
    commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (dest / "source_commit.txt").write_text(commit + "\n")
    (dest / "source_sha256.txt").write_text(code_sha + "\n")
    (dest / "command.txt").write_text(
        f"python3 -B {script} --symbol {symbol} --days {days}\n"
    )
    manifest = dest / "run_manifest.json"
    write_json_new(manifest, {"status": "RUNNING", "symbol": symbol, "days": days,
                              "actor_uid": os.geteuid(), "source_commit": commit,
                              "program_sha256": code_sha, "formula_version": FORMULA})
    try:
        temp = dest / "features_5m.jsonl.gz.part"
        datafile = dest / "features_5m.jsonl.gz"
        all_blocks = []
        provenance = []
        total_fills = 0
        started = time.monotonic()
        for index, (date, source, expected) in enumerate(sources, 1):
            digest = sha(source)
            if (digest != expected["compressed_sha256"]
                    or source.stat().st_size != expected["compressed_bytes"]):
                raise ValueError("RAW_SOURCE_HASH_OR_SIZE_CHANGED")
            current = aggregate_day(date, source)
            fills = sum(b["count"] for b in current)
            total_fills += fills
            provenance.append({"date_utc": date.isoformat(), "source_path": str(source),
                               "source_sha256": digest,
                               "source_bytes": source.stat().st_size,
                               "fills": fills, "blocks": len(current)})
            all_blocks.extend((date, k, b, digest) for k, b in enumerate(current))
            print(json.dumps({"stage": "FLOW17_SOURCE_PREPARED", "symbol": symbol,
                              "days_done": index, "days_total": days,
                              "elapsed_s": round(time.monotonic()-started, 2),
                              "heartbeat": True}), flush=True)
        # Run cross-midnight lookbacks continuously across the first month.
        rows = 0
        eligible = decays = missing = 0
        with temp.open("xb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding="utf-8") as writer:
                    for ix, (date, block_index, b, digest) in enumerate(all_blocks):
                        pp = all_blocks[ix-2][2] if ix >= 2 else None
                        pr = all_blocks[ix-1][2] if ix >= 1 else None
                        ratio, decay, quality = as_of_feature(pp, pr, b)
                        if ratio is None:
                            missing += 1
                        else:
                            eligible += 1
                            decays += int(decay)
                        start = dt.datetime.combine(
                            date, dt.time(), dt.UTC
                        ) + dt.timedelta(minutes=block_index * 5)
                        row = {"venue_id": "Bybit", "instrument_id": symbol,
                               "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                               "indicator_id": 17, "formula_version": FORMULA,
                               "window_start_utc": start.isoformat(),
                               "derived_window_end_utc": (start+dt.timedelta(minutes=5)).isoformat(),
                               "observed_at_utc": None,
                               "verified_available_after_utc": None,
                               "fill_count": b["count"],
                               "signed_aggressor_quote_usdt": str(b["flow"]),
                               "last_trade_price": str(b["last"]) if b["last"] is not None else None,
                               "previous_block_last_price": str(pr["last"]) if pr and pr["last"] is not None else None,
                               "previous_previous_block_last_price": str(pp["last"]) if pp and pp["last"] is not None else None,
                               "flow_ratio_abs_current_to_prior": str(ratio) if ratio is not None else None,
                               "flow_decay_proxy": decay, "quality": quality,
                               "source_archive_sha256": digest}
                        writer.write(json.dumps(row, sort_keys=True)+"\n")
                        rows += 1
            output.flush()
            os.fsync(output.fileno())
        if rows != days * 288:
            raise ValueError("FIVE_MINUTE_COVERAGE_INCOMPLETE")
        temp.rename(datafile)
        write_json_new(dest / "input_provenance.json", {"days": provenance})
        result = {"status": COMPLETE, "symbol": symbol, "days": days,
                  "rows": rows, "fills": total_fills, "eligible": eligible,
                  "decays": decays, "unavailable": missing,
                  "indicator_id": 17, "formula_version": FORMULA,
                  "source_commit": commit, "program_sha256": code_sha,
                  "source_manifest_sha256": sha(MANIFEST),
                  "output_sha256": sha(datafile), "receiver_clock_verified": False,
                  "source_continuity_verified": False, "research_ready": False,
                  "meaning": "5m observed signed aggressor turnover decay proxy, NOT proof of exhaustion",
                  "future_return_used": False}
        write_json_new(dest / "result.json", result)
        set_terminal(manifest, COMPLETE, result=result)
        print(json.dumps({"status": COMPLETE, "symbol": symbol,
                          "rows": rows, "output_sha256": result["output_sha256"],
                          "output_dir": str(dest)}), flush=True)
    except Exception as e:
        if not (dest / "result.json").exists():
            try:
                set_terminal(manifest, "FAILED", error=e)
            except Exception as other:
                print("TERMINAL_FAILURE " + str(other), file=sys.stderr)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=SYMS, required=True)
    parser.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = parser.parse_args()
    run(args.symbol, args.days)
