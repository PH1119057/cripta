"""Pure local regression gates for 2025 capital-free P90L90 experiment.

No server archive required. Tests must not create any research artifacts.
"""
from __future__ import annotations

from decimal import Decimal as D
from operator import eq

import pytest

from scripts.research import p90l90_bull2025_capfree100_v1 as cap


def test_capfree_research_version_configure_is_scoped(monkeypatch: pytest.MonkeyPatch):
    old = cap.old
    engine = cap.engine
    values = {
        "START_WALLET": old.START_WALLET,
        "FIXED_MARGIN": old.FIXED_MARGIN,
        "SCENARIOS": old.SCENARIOS,
        "CASH_MODES": old.CASH_MODES,
    }
    modules = {
        "PARTS": engine.PARTS,
        "FINAL": engine.FINAL,
        "STATUS": engine.STATUS,
        "PINNED": engine.PINNED,
    }
    for name, value in values.items():
        monkeypatch.setattr(old, name, value)
    for name, value in modules.items():
        monkeypatch.setattr(engine, name, value)
    cap.configure()
    assert eq(old.START_WALLET, D("1000000000000"))
    assert eq(old.FIXED_MARGIN, D("20"))
    assert eq(cap.POSITION_NOTIONAL, D("100"))
    assert eq(old.SCENARIOS, ("NO_PAUSE", "SECOND48"))
    assert eq(old.CASH_MODES, ("fixed",))
    assert eq(engine.PARTS, cap.OUT / "checkpoint")
    assert eq(engine.FINAL, cap.OUT / "full")
    assert cap.SELF_REL in engine.PINNED
    assert eq(set(engine.new_books("LONG_ONLY", {})), {
        "NO_PAUSE__fixed", "SECOND48__fixed"
    })
    for name, value in values.items():
        monkeypatch.setattr(old, name, value)
    for name, value in modules.items():
        monkeypatch.setattr(engine, name, value)


def test_capfree_notional_fee_is_scaled_at_original_12_decimal_quantum():
    assert eq(cap.POSITION_NOTIONAL, D("100"))
    assert eq(cap.old.money(cap.POSITION_NOTIONAL * cap.old.TAKER), D("0.055000000000"))
    assert eq(cap.old.money(cap.POSITION_NOTIONAL * cap.old.MAKER), D("0.020000000000"))


def test_wrong_actor_fails_before_any_write(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cap.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cap.os, "getegid", lambda: 1000)
    with pytest.raises(PermissionError, match="cripta"):
        cap.preflight("f"*40)
