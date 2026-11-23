"""#849 immutable Exit component: its P90 owner requires a non-Python manifest.

This regression covers the missing physical package dependency that caused
the real 2026-11-23 Exit-only canary to return to OLD.  It does not grant a
release or change trading policy.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p90_exit_native_feed_manifest_is_source_tracked() -> None:
    owner = (
        ROOT / "operations/connectivity/p90_r06_exit_owner.py"
    ).read_text(encoding="utf-8")
    required = ROOT / "operations/monitoring/required_public_feeds.json"
    assert 'monitoring/required_public_feeds.json' in owner
    assert required.is_file()
    contents = json.loads(required.read_text(encoding="utf-8"))
    exit_symbols = {
        row["symbol"] for row in contents
        if row["consumer_id"] == "EXIT_P90"
        and row["product"] == "LINEAR"
        and row["dataset"] == "PUBLIC_TRADE"
        and row["required"] is True
    }
    assert exit_symbols == {
        "LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"
    }
