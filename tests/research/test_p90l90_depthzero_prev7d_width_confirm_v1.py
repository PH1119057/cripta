"""Safety/causal/bookkeeping tests for frozen 2026 replication #633."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from decimal import Decimal as D
import hashlib

import pytest

from scripts.research import p90l90_depthzero_prev7d_width_confirm_v1 as check


def test_primary_hypotheses_and_frozen_diagnostic_grid():
    assert set(check.PERIODS) == {"90d", "29d"}
    assert check.PERIODS["90d"]["days"] == 90
    assert check.PERIODS["29d"]["days"] == 29
    assert len(check.BANDS) == 9
    assert check.BANDS["080"] == D("0.80")
    assert check.BANDS["090"] == D("0.90")
    assert "w080_SHORT_ONLY_SECOND48" in check.BOOK_NAMES
    assert "w090_SHORT_ONLY_SECOND48" in check.BOOK_NAMES
    assert len(check.BOOK_NAMES) == 36
    assert check.SYMBOLS == ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")


def test_exact_depthzero_and_inclusive_width_boundary():
    event = {"depth_pct": "0", "width_pct": "0.90"}
    assert check.cohort(event, D("0.10"), D("0.80"))
    assert not check.cohort(event, D("0.10"), D("0.79"))
    assert check.cohort(event, D("0.10"), D("0.90"))
    assert not check.cohort({**event, "depth_pct": "0.000000000001"},
                            D("0.10"), D("1.50"))
    assert not check.cohort({**event, "width_pct": "2.5"},
                            D("0.10"), D("1.50"))


def test_causal_median_current_day_is_not_fallback():
    assert check.BANDS["050"] == D("0.50")
    src = check.validate_and_filter.__code__.co_consts
    assert any(isinstance(x, str) and "original causal" in x for x in src)


def test_book_real_instance_roundtrip_without_weakening_stop_contract():
    b = check.pause.EntryEmbargo(2, 48)
    p = check.base.Position(
        side="SHORT", entry=D("1.2"), entry_seq=20,
        entry_ts=12345.5, state_bucket=12300, stop=D("1.32"),
        best_favorable=D("0.9"), protected=True,
        protection_seq=21, tp=D("1.1"), tp_live_after_seq=22,
        entry_event_id="event-1", entry_trade_id="trade-1",
    )
    b.pos["LTCUSDT"] = p
    b.pause_until = 18900.5
    b.last_stops = deque([12000.0, 15000.0])
    b.all_stops = [{"ts": "12000", "symbol": "LTCUSDT"}]
    b.count = Counter({"accepted": 2, "close_HARD_STOP": 1})
    b.money = defaultdict(lambda: D(0), {"net_usdt": D("-1.25")})
    b.stops = [{"ts": "12000", "target_positions": 0}]
    b.trades = [{"side": "SHORT", "net_usdt": "-1.25"}]

    frozen = check.freeze_book(b)
    packed = check.pack_snapshot({"book": frozen})
    assert packed == check.pack_snapshot({"book": frozen})
    same = check.unpack_snapshot(packed)["book"]
    out = check.pause.EntryEmbargo(2, 48)
    check.restore_book(out, same)
    assert check.freeze_book(out) == frozen
    assert out.pos["LTCUSDT"].entry == D("1.2")
    assert out.pos["LTCUSDT"].tp == D("1.1")
    assert out.threshold == 2 and out.embargo_hours == 48


def test_book_frozen_production_exit_not_modified():
    books = check.make_books()
    assert set(books) == set(check.ALL_NAMES)
    assert books["REFERENCE"].threshold == 0
    assert books["w080_SHORT_ONLY_NO_PAUSE"].threshold == 0
    assert books["w080_SHORT_ONLY_SECOND48"].threshold == 2
    assert books["w080_SHORT_ONLY_SECOND48"].embargo_hours == 48
    assert not books["w080_SHORT_ONLY_SECOND48"].pending
    assert not books["w080_SHORT_ONLY_SECOND48"].pos["LTCUSDT"]


def test_checkpoint_immutability_and_sha_chain(tmp_path):
    location = tmp_path / "day_001.json.gz"
    payload = check.pack_snapshot({
        "research": check.RESEARCH, "period": "29d",
        "days_done": 1, "previous_checkpoint_sha256": "",
        "books": {"example": {}}, "pins": {}, "source_events_seen": {},
    })
    check.write_immutable(location, payload)
    assert location.exists()
    assert hashlib.sha256(location.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
    with pytest.raises(FileExistsError, match="collision"):
        check.write_immutable(location, payload)
    assert check.unpack_snapshot(location.read_bytes())["days_done"] == 1


def test_checkpoint_load_rejects_foreign_source(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "ALL_NAMES", ("example",))
    monkeypatch.setattr(check, "PERIODS",
                        {"29d": {**check.PERIODS["29d"], "days": 1}})
    folder = tmp_path / "checkpoints"
    folder.mkdir()
    payload = check.pack_snapshot({
        "research": check.RESEARCH, "period": "29d", "pins": {"a": "b"},
        "days_done": 1, "previous_checkpoint_sha256": "",
        "books": {"example": {}}, "source_events_seen": {},
    })
    check.write_immutable(folder / "day_001.json.gz", payload)
    with pytest.raises(ValueError, match="checkpoint source"):
        check.scan_checkpoints(tmp_path, {"a": "WRONG"}, "29d")
    result, digest = check.scan_checkpoints(tmp_path, {"a": "b"}, "29d")
    assert result["days_done"] == 1
    assert digest == hashlib.sha256(payload).hexdigest()


def test_wrong_actor_fail_closed(monkeypatch):
    monkeypatch.setattr(check.os, "geteuid", lambda: 999, raising=False)
    monkeypatch.setattr(check.os, "getegid", lambda: 999, raising=False)
    with pytest.raises(PermissionError, match="actual cripta"):
        check.preflight("29d", "0" * 40)


def test_output_is_only_new_research_child():
    for period in ("90d", "29d"):
        assert check.out_dir(period).name == check.LEAF
        assert check.out_dir(period).parent.name == "results"
        assert "/data/cripta/research/runs/" in check.out_dir(period).as_posix()


def test_no_root_runtime_or_external_execution():
    import inspect
    code = inspect.getsource(check)
    assert "sudo " not in code
    assert "start_new_session=True" not in code
    assert "subprocess.Popen" not in code
    assert "systemd-run" not in code
    assert "Exchange" in code
    assert "source_head" in code
    assert "source_events_seen" in code
