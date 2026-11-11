"""Research-only owner-approved 2025 P90L90 PREV7D tolerance sweep."""
from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_prev7d_width_sweep_v1 as sweep


def event(width: str, depth: str = "0", side: str = "LONG"):
    return {
        "symbol": "LTCUSDT",
        "side": side,
        "depth_pct": depth,
        "width_pct": width,
        "state_effective_ts": 1743465600,
        "PREV7D": True,
    }


def medians():
    return {"LTCUSDT": {"2025-04-01": D("1.00")}}


def test_utc_day_is_exact_and_uses_closed_bucket():
    value = sweep.day_of(event("1.00"))
    assert value == "2025-04-01"
    assert value == datetime.fromtimestamp(1743465600, UTC).date().isoformat()


@pytest.mark.parametrize(
    ("width", "band", "passes"),
    [
        ("1.25", "0.25", True),
        ("0.75", "0.25", True),
        ("1.250000000000000000000001", "0.25", False),
        ("0.74999999999999999999999", "0.25", False),
        ("1.50", "0.50", True),
        ("1.5000000000000000000000000001", "0.50", False),
        ("1.75", "0.75", True),
        ("0.25", "0.75", True),
        ("1.7500000000000000000000000001", "0.75", False),
    ],
)
def test_inclusive_decimal_band_without_rounding(width, band, passes):
    assert sweep.eligible(event(width), "LONG", D(band), medians()) is passes


def test_direction_depth_and_causal_median_is_required():
    assert not sweep.eligible(event("1", "0.00000000000001"), "LONG", D("0.75"), medians())
    assert not sweep.eligible(event("1", "0", "SHORT"), "LONG", D("0.75"), medians())
    with pytest.raises(KeyError):
        sweep.eligible(
            event("1"),
            "LONG",
            D("0.75"),
            {"LTCUSDT": {"2025-03-31": D("1")}},
        )


def test_strict_cohorts_nested_for_every_depthzero_event():
    med = medians()
    for i in range(0, 201):
        e = event(str(D(i) / D(100)))
        admitted = [sweep.eligible(e, "LONG", sweep.WIDTHS[code], med)
                    for code in ("025", "050", "075")]
        assert admitted[0] <= admitted[1] <= admitted[2]


def test_only_two_new_bands_replayed_and_baseline_reused():
    assert sweep.NEW == ("025", "075")
    width_bands = sweep.WIDTHS
    assert width_bands == {"025": D("0.25"), "050": D("0.50"), "075": D("0.75")}
    assert sweep.EXPECTED_050 == {"LONG_ONLY": 2583, "SHORT_ONLY": 2863}
    assert sweep.CASES == ("NO_PAUSE", "SECOND48")
    assert sweep.SIDES == ("LONG_ONLY", "SHORT_ONLY")


def test_source_and_outputs_new_and_scoped():
    assert sweep.SELF_REL.startswith("scripts/research/")
    assert sweep.OUT != sweep.BASE
    assert sweep.BASE.name == "portfolio_depthzero_2025_v1"
    assert sweep.OUT.parent == sweep.BASE.parent
    assert sweep.target("025").name == "band025"
    assert sweep.target("075").name == "band075"
    with pytest.raises(ValueError):
        sweep.target("050")


def test_frozen_engine_replay_and_exit_not_rewritten():
    source = inspect.getsource(sweep.worker)
    assert "resume.run5(" in source
    assert "independent_qc(code, side, expected, catalog)" in source
    assert "write_once(OUT / \"summary.json\", summary)" in source
    assert "PILOT_PASS" in inspect.getsource(sweep.launch)
    assert sweep.RESERVE == 5 * 2**30
    from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as original_engine

    assert "16 * 2**30" in inspect.getsource(original_engine.run)


def test_new_book_admission_injects_only_new_width_boolean():
    source = inspect.getsource(sweep.prepare)
    assert 'injected = tuple(' in source
    assert '{**e, "PREV7D": True}' in source
    assert "eligible(e, self.side, WIDTHS[code], catalog[\"median\"])" in source
    assert "engine.old.Book.handle = handle_width" in source


def test_preflight_fails_closed_for_existing_output(tmp_path, monkeypatch):
    folder = tmp_path / "width"
    folder.mkdir()
    monkeypatch.setattr(sweep, "OUT", folder)
    with pytest.raises(FileExistsError, match="already created"):
        sweep.preflight(SimpleNamespace(), {"counts": {}}, "a" * 40)


def test_original_scenario_mode_cannot_become_live():
    assert "post_holdout_exploratory_not_oos" in inspect.getsource(sweep.worker)
    assert "fixed100USDT_notional_unbounded_virtual_wallet" in inspect.getsource(sweep.pilot)
    assert "real_exchange_fills" in inspect.getsource(sweep.independent_qc)
    assert sweep.QC_STATUS.startswith("INDEPENDENT_QC_PASS_P90L90_DEPTHZERO_PREV7D")


def test_study_outputs_are_not_source_modified(tmp_path, monkeypatch):
    dummy = tmp_path / "leaf.json"
    sweep.write_once(dummy, {"test": "PASS"})
    with pytest.raises(FileExistsError):
        sweep.write_once(dummy, {"test": "DUPLICATE"})
    assert json.loads(dummy.read_text()) == {"test": "PASS"}


def test_frozen_depthzero_prepare_tuple_contract(monkeypatch):
    """Original prepare returns (engine, proof), not (driver, engine)."""
    class DummyBook:
        def handle(self, *_args):
            return True

    fake_engine = SimpleNamespace(
        old=SimpleNamespace(
            START_WALLET=D("1000000000000"),
            FIXED_MARGIN=D("20"),
            LEVERAGE=D("5"),
            Book=DummyBook,
        ),
        PINNED=("original_sha_pins",),
    )
    monkeypatch.setattr(
        sweep.original,
        "prepare",
        lambda _expected: (fake_engine, {"status": "FROZEN_PASS"}),
    )
    monkeypatch.setattr(sweep, "verify_base", lambda: {})
    monkeypatch.setattr(
        sweep,
        "verify_source_catalog",
        lambda: {"median": medians(), "selected": {}, "counts": {}},
    )
    monkeypatch.setattr(sweep, "sha", lambda _p: "sample_sha")
    actual_engine, catalog, current = sweep.prepare("a" * 40)
    assert actual_engine is fake_engine
    assert sweep.SELF_REL in fake_engine.PINNED
    assert "median" in catalog
    assert current == {"width": None}
