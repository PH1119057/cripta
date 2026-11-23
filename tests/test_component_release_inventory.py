"""Static, entire service-unit inventory tests for release isolation (#849)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "infrastructure"))
from component_release_inventory import unit_inventory  # noqa: E402


def test_entire_reviewed_systemd_inventory_has_explicit_coupling_report() -> None:
    units = unit_inventory(ROOT)
    assert len(units) >= 30
    by_unit = {row.unit: row for row in units}
    for unit in (
        "cripta-universal-entry-consumer.service",
        "cripta-universal-entry-consumer@.service",
        "cripta-universal-exit-consumer.service",
        "cripta-dashboard.service",
    ):
        assert unit in by_unit
        assert by_unit[unit].scope == "LEGACY_SHARED_RUNTIME"
        assert by_unit[unit].executable_uses_shared_runtime
    assert all(row.source_path.startswith("operations/") for row in units)
    assert len(by_unit) == len(units)


def test_only_exact_unit_dependencies_are_reported(tmp_path: Path) -> None:
    unit_dir = tmp_path / "operations" / "systemd"
    unit_dir.mkdir(parents=True)
    (unit_dir / "demo.service").write_text(
        "[Unit]\nWants=postgresql.service network-online.target\n"
        "Requires=postgresql.service\nAfter=postgresql.service\n"
        "[Service]\n"
        "WorkingDirectory=/srv/cripta/components/demo/current\n"
        "ExecStart=/srv/cripta/components/demo/current/runner\n"
        "Environment=MODE=DEMO\n",
        encoding="utf-8",
    )
    rows = unit_inventory(tmp_path)
    assert len(rows) == 1
    assert rows[0].scope == "INDEPENDENCE_NOT_ATTESTED"
    assert rows[0].wants == ("network-online.target", "postgresql.service")
    assert rows[0].requires == ("postgresql.service",)
    assert rows[0].after == ("postgresql.service",)
    assert not rows[0].executable_uses_shared_runtime


def test_shared_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    unit_dir = tmp_path / "operations" / "systemd"
    unit_dir.mkdir(parents=True)
    target = tmp_path / "outside.service"
    target.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    link = unit_dir / "linked.service"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable on this filesystem")
    with pytest.raises(ValueError, match="INVENTORY_UNIT_NOT_REGULAR"):
        unit_inventory(tmp_path)


def test_unclosed_unit_continuation_is_not_accepted(tmp_path: Path) -> None:
    unit_dir = tmp_path / "operations" / "systemd"
    unit_dir.mkdir(parents=True)
    (unit_dir / "broken.service").write_text(
        "[Service]\nExecStart=/bin/echo \\\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="UNIT_CONTINUATION_INCOMPLETE"):
        unit_inventory(tmp_path)
