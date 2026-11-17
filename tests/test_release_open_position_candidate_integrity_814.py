from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.implementation import wait_p90_private_runtime_ready as barrier

ROOT = Path(__file__).resolve().parents[1]


def _routes_and_bundles():
    main = SimpleNamespace(
        strategy_id="p90l90_legacy_main_ltc_inj",
        trading_account_id="ta-" + "1" * 32,
        account_connector_id="ac-main",
    )
    sub = SimpleNamespace(
        strategy_id="p90l90_legacy_sub_dot_arb_apt",
        trading_account_id="ta-" + "2" * 32,
        account_connector_id="ac-sub",
    )
    routes = SimpleNamespace(main=main, sub=sub)
    bundles = (
        SimpleNamespace(card=SimpleNamespace(
            strategy_id=main.strategy_id,
            strategy_version=barrier.VERSION,
        )),
        SimpleNamespace(card=SimpleNamespace(
            strategy_id=sub.strategy_id,
            strategy_version=barrier.VERSION,
        )),
    )
    return routes, bundles


def test_release_generation_accepts_fresh_proven_open_inventory_shape() -> None:
    now = datetime(2026, 11, 17, 7, 30, tzinfo=UTC)
    row = ("gen-open", "COMPLETE", now, 1, 0, "PROVEN")

    class DB:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(fetchone=lambda: row)

    assert barrier._generation_ready(
        DB(),
        account_id="ta-" + "1" * 32,
        connector_id="ac-open",
        not_before=now,
        now=now,
    )


def test_release_barrier_uses_exact_open_restart_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 17, 7, 30, tzinfo=UTC)
    routes, bundles = _routes_and_bundles()
    seen: list[object] = []
    monkeypatch.setattr(barrier, "load_active_strategy_bundles", lambda _db: bundles)
    monkeypatch.setattr(barrier, "read_p90_real_account_routes", lambda *_a, **_k: routes)
    monkeypatch.setattr(barrier, "_generation_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(
        barrier,
        "inspect_p90_real_open_restart_epoch",
        lambda *_a, **_k: "p90-real-epoch-" + "a" * 40,
    )
    monkeypatch.setattr(
        barrier,
        "prove_p90_real_open_restart_inventory",
        lambda *_a, **kwargs: seen.append(kwargs)
        or SimpleNamespace(positions=(SimpleNamespace(),)),
    )
    monkeypatch.setattr(
        barrier,
        "read_p90_real_signed_flat_accounts",
        lambda *_a, **_k: pytest.fail("OPEN restart must not use FLAT proof"),
    )
    monkeypatch.setenv("CRIPTA_RELEASE_COMMIT", "b" * 40)

    result = barrier.wait_until_ready(
        object(),
        not_before=now,
        timeout_seconds=1.0,
        sleeper=lambda _x: None,
        clock=lambda: now,
    )
    assert result["status"] == "PASS_OPEN"
    assert result["positions"] == 1
    assert seen and seen[0]["allow_missing_trail035"] is True
    assert seen[0]["source_commit"] == "b" * 40


def test_release_barrier_keeps_flat_path_when_no_open_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 17, 7, 30, tzinfo=UTC)
    routes, bundles = _routes_and_bundles()
    monkeypatch.setattr(barrier, "load_active_strategy_bundles", lambda _db: bundles)
    monkeypatch.setattr(barrier, "read_p90_real_account_routes", lambda *_a, **_k: routes)
    monkeypatch.setattr(barrier, "_generation_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(
        barrier, "inspect_p90_real_open_restart_epoch", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        barrier,
        "read_p90_real_signed_flat_accounts",
        lambda *_a, **_k: (SimpleNamespace(), SimpleNamespace()),
    )
    monkeypatch.setattr(
        barrier,
        "prove_p90_real_open_restart_inventory",
        lambda *_a, **_k: pytest.fail("FLAT release must not enter OPEN proof"),
    )
    monkeypatch.setenv("CRIPTA_RELEASE_COMMIT", "b" * 40)

    result = barrier.wait_until_ready(
        object(),
        not_before=now,
        timeout_seconds=1.0,
        sleeper=lambda _x: None,
        clock=lambda: now,
    )
    assert result["status"] == "PASS_FLAT"
    assert result["positions"] == 0


def test_installer_rebuilds_noncurrent_target_and_refuses_current_target() -> None:
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "RELEASE_TARGET_CANDIDATE_IS_CURRENT" in installer
    assert "RELEASE_TARGET_CANDIDATE_REBUILT=PASS" in installer
    assert 'rm -rf --one-file-system -- "$candidate"' in installer
    assert 'git -C "$SOURCE" archive --format=tar "$RELEASE_COMMIT"' in installer


def test_installer_verifies_runtime_candidate_blobs_before_installed_marker() -> None:
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "verify_release_candidate_git.py" in installer
    assert "RELEASE_RUNTIME_GIT_BLOB_VERIFY=PASS" in installer
    verify = installer.index("RELEASE_RUNTIME_GIT_BLOB_VERIFY=PASS")
    marker = installer.index(
        'printf \'%s\\n\' "$RELEASE_COMMIT" > "$runtime_release/INSTALLED_COMMIT"'
    )
    assert verify < marker


def test_candidate_verifier_is_read_only_and_checks_blob_mode_and_path_set() -> None:
    source = (
        ROOT / "operations/infrastructure/verify_release_candidate_git.py"
    ).read_text(encoding="utf-8")
    assert "hash-object" in source
    assert "CANDIDATE_PATH_SET_MISMATCH" in source
    assert "CANDIDATE_GIT_MODE_MISMATCH" in source
    assert "subprocess.run" in source
    assert "unlink(" not in source
    assert "rmtree(" not in source
