"""#865 immutable MarketSource package and pinned-service unit preflight tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from operations.infrastructure import component_release_market_source_stage as source


ROOT = Path(__file__).resolve().parents[1]


def test_market_source_own_unit_pins_only_new_component_not_global_runtime() -> None:
    old = (
        ROOT / "operations/systemd/cripta-native-public-trade-source.service"
    ).read_bytes()
    assert hashlib.sha256(old).hexdigest() == source.OLD_UNIT_SHA
    root = Path("/srv/cripta/components/market_source/releases") / source.SOURCE_SHA
    result = source.pinned_market_source_unit(old, root=root).decode("utf-8")
    assert "WorkingDirectory=" + str(root) in result
    assert "Environment=PYTHONPATH=" + str(root) + "/src:" + str(root) in result
    assert (
        "ExecStart=/usr/bin/env CRIPTA_RELEASE_COMMIT=" + source.SOURCE_SHA
        + " " + str(root) + "/.venv/bin/python " + str(root)
        + "/operations/monitoring/native_public_trade_source.py"
    ) in result
    assert "ReadOnlyPaths=" + str(root) in result
    assert "/srv/cripta/runtime/current" not in result
    assert "EnvironmentFile=/etc/cripta/release.env" in result
    assert "Restart=on-failure" in result
    assert "ReadWritePaths=/var/lib/cripta/native_public_trade_source" in result
    assert "User=cripta" in result
    assert "Group=cripta" in result
    assert "NoNewPrivileges=true" in result
    assert result.count("ExecStart=") == 1
    assert "stop cripta-" not in result and "start cripta-" not in result


def test_market_source_unit_rejects_unreviewed_or_modified_template() -> None:
    old = (
        ROOT / "operations/systemd/cripta-native-public-trade-source.service"
    ).read_bytes()
    with pytest.raises(RuntimeError, match="UNIT_TEMPLATE_UNVERIFIED"):
        source.pinned_market_source_unit(old + b"# unreviewed\n")
    with pytest.raises(RuntimeError, match="UNIT_TEMPLATE_UNVERIFIED"):
        source.pinned_market_source_unit(old, root=Path("relative"))


def test_component_manifest_full_hash_covers_file_mode_bytes_and_symlink(
    tmp_path: Path,
) -> None:
    base = tmp_path / "root"
    (base / ".venv/bin").mkdir(parents=True)
    (base / ".venv/bin/python3").symlink_to("/usr/bin/python3")
    (base / "src").mkdir()
    file = base / "src/native.py"
    file.write_text("print(42)\n", encoding="utf-8")
    file.chmod(0o644)
    digest, count = source._walk_digest(base)
    assert count >= 4
    file.write_text("print(43)\n", encoding="utf-8")
    changed, _ = source._walk_digest(base)
    assert changed != digest
    file.write_text("print(42)\n", encoding="utf-8")
    file.chmod(0o755)
    executable, _ = source._walk_digest(base)
    assert executable != digest
    file.chmod(0o644)
    assert source._walk_digest(base)[0] == digest


def test_component_refuses_unrelated_moving_dashboard_symlink(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    (root / "operations/dashboard").mkdir(parents=True)
    (root / "operations/dashboard/index.html").symlink_to(
        "/srv/cripta/dashboard-ui/current/index.html"
    )
    with pytest.raises(RuntimeError, match="SOURCE_SYMLINK_OUTSIDE"):
        source._walk_digest(root)


def test_staging_never_grants_cutover_implicitly() -> None:
    # Static contract: the staging actor has no systemctl, PG or trade entry.
    code = (
        ROOT / "operations/infrastructure/component_release_market_source_stage.py"
    ).read_text(encoding="utf-8")
    assert "cutover_authorized\":False" in code
    assert "systemctl" not in code
    assert "psycopg" not in code
    assert "pybit" not in code
    assert "subprocess.run(" in code
    assert source.LEGACY_SHA != source.SOURCE_SHA
