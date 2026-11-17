"""#835: direct entry +0.5 before -10 causal and identity gates."""
import csv
import gzip
import hashlib
import json

from scripts.research import p90l90_direct_entry_noexit_first_hardstop_mfe05_v1 as sut


def fixture(tmp_path, monkeypatch, strict0=False):
    period = "183d" if strict0 else "90d"
    start = "2025-04-01" if strict0 else "2026-05-18"
    finish = "2025-10-01" if strict0 else "2026-08-16"
    raw = tmp_path / "inputs" / "LTCUSDT" / "public_trades"
    raw.mkdir(parents=True)
    events = (
        tmp_path / "results" / "entry_full_2025_v1" / "LTCUSDT"
        if strict0 else tmp_path / "results" / "entry_LTC_FULL90"
    )
    events.mkdir(parents=True)
    filename = "LTCUSDT" + start + ".csv.gz"
    prices = ["100", "100.5", "90", "100", "110", "100", "100.51"]
    csv_path = raw / filename
    with gzip.open(csv_path, "wt", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("timestamp", "symbol", "side", "size", "price",
                         "tickDirection", "trdMatchID"))
        for i, price in enumerate(prices, 1):
            writer.writerow((1_000_000_000 + i, "LTCUSDT", "Buy",
                             "1", price, "PlusTick", "trade-" + str(i)))

    es = []
    for seq, side, ident, depth in (
            (1, "LONG", "A", "0"),
            (4, "SHORT", "B", "0"),
            (6, "LONG", "C", "0"),
            (6, "SHORT", "FILTER", "10")):
        es.append({
            "candidate_only": True, "symbol": "LTCUSDT",
            "source_order": seq, "ts": str(1_000_000_000 + seq),
            "price": prices[seq-1], "trade_id": "trade-" + str(seq),
            "event_id": ident, "side": side, "PREV7D": True,
            "depth_pct": depth,
        })
    # Approved historical raw roots may be stable symlinks to /data/cripta.
    actual = raw / (filename + ".actual")
    csv_path.rename(actual)
    csv_path.symlink_to(actual)
    events_file = events / "candidates_LTCUSDT.jsonl"
    events_file.write_text("".join(json.dumps(x) + "\n" for x in es))
    manifest = {
        "symbol": "LTCUSDT",
        "candidate_only": True,
        "events_sha256": hashlib.sha256(events_file.read_bytes()).hexdigest(),
        "inputs": [
            {"file": filename, "rows": len(prices),
             "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()}
        ],
    }
    if strict0:
        manifest["events"] = len(es)
    else:
        manifest["counts"] = {"raw_crossing": len(es)}
    (events / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sut, "baseline_hardstops", lambda cfg, symbol: {"B"})
    config = {
        "root": str(tmp_path),
        "entry_sub": ("results/entry_full_2025_v1"
                      if strict0 else "results"),
        "eval_start": start, "eval_end": finish,
        "strict0": strict0,
    }
    out = tmp_path / "output"
    out.mkdir()
    return period, config, out


def test_direct_stop_after_half_percent_is_real(monkeypatch, tmp_path):
    period, cfg, out = fixture(tmp_path, monkeypatch)
    r = sut.simulate_symbol(period, "LTC", cfg, 0, out)
    assert r["status"] == "PASS"
    assert r["counters"]["OPENED"] == 4
    assert r["counters"]["HARD_STOP"] == 2
    assert r["counters"]["HS_AFTER_05"] == 1
    assert r["counters"]["HS_NEVER_05"] == 1
    assert r["counters"]["ORIGINAL_HS_CLOSED"] == 1
    assert r["counters"]["CENSORED"] == 2
    rows = [json.loads(x) for x in
            (out / "LTCUSDT_direct_virtual_trades.jsonl").read_text().splitlines()]
    a = next(r for r in rows if r["candidate_event_id"] == "A")
    b = next(r for r in rows if r["candidate_event_id"] == "B")
    assert a["terminal"] == "HARD_STOP"
    assert a["hit_plus0p5_before_stop"] is True
    assert b["terminal"] == "HARD_STOP"
    assert b["hit_plus0p5_before_stop"] is False
    assert b["original_trail035_hardstop"] is True


def test_strict0_2025_filters_depth(monkeypatch, tmp_path):
    period, cfg, out = fixture(tmp_path, monkeypatch, strict0=True)
    r = sut.simulate_symbol(period, "LTC", cfg, 0, out)
    assert r["counters"]["OPENED"] == 3
    assert r["counters"]["HARD_STOP"] == 2
    assert r["counters"]["HS_AFTER_05"] == 1
    assert r["counters"]["CENSORED"] == 1
    assert r["counters"]["ORIGINAL_HS_CLOSED"] == 1


def test_zero_threshold_gating_is_nonstrategy():
    assert sut.SPEC["183d"]["strict0"] is True
    assert sut.SPEC["90d"]["strict0"] is False
    assert sut.SPEC["29d"]["strict0"] is False
    assert sut.SPEC["183d"]["expected_entry"] == 5446
    assert sut.SPEC["90d"]["expected_entry"] == 3959
    assert sut.SPEC["29d"]["expected_entry"] == 886
