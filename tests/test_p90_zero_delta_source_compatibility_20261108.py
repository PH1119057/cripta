"""An identical original consumer is safe to attest across Git releases.

No real trading authorization, no provenance rewrite, no private Exchange
interaction: only immutable Git consumer inventories are compared.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat


_SHARED = (
    "strategy_position.py",
    "exchange_position_identity.py",
    "domain/models.py",
    "market_data/shared_closed_candles.py",
)
_MODULES = (
    "universal_entry/entry.py",
    "universal_exit/exit.py",
    *_SHARED,
)


def _consumer_tree(root: Path) -> None:
    for relative in _MODULES:
        path = root / "src/bybit_workbench" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# identical reviewed consumer bytes\n")


def test_exact_zero_delta_consumer_registration_has_no_old_bugfix_dependency(
    tmp_path: Path,
) -> None:
    origin = tmp_path / ("a" * 40)
    loaded = tmp_path / ("b" * 40)
    _consumer_tree(origin)
    _consumer_tree(loaded)

    # A new release SHA with no consumer changes is a stronger proof than
    # asking the old origin-specific stabilization changed-set to recur.
    compat.verify_reviewed_bugfix_delta(
        current_root=loaded, origin_root=origin,
    )
    # A nonzero unreviewed change is still rejected, even one byte.
    path = loaded / "src/bybit_workbench/universal_exit/exit.py"
    path.write_bytes(path.read_bytes() + b"# one unreviewed byte\n")
    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat.verify_reviewed_bugfix_delta(
            current_root=loaded, origin_root=origin,
        )


def test_exact_git_origin_zero_delta_requires_same_all_consumer_bytes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "source_checkout"
    _consumer_tree(repo)
    subprocess.run(("git", "-C", str(repo), "init"), check=True, capture_output=True)
    subprocess.run(("git", "-C", str(repo), "add", "."), check=True, capture_output=True)
    subprocess.run(
        (
            "git", "-C", str(repo),
            "-c", "user.name=Cripta Test",
            "-c", "user.email=cripta-test@example.invalid",
            "commit", "-m", "immutable exact origin",
        ), check=True, capture_output=True,
    )
    origin_sha = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    loaded = tmp_path / ("b" * 40)
    shutil.copytree(repo / "src/bybit_workbench", loaded / "src/bybit_workbench")
    compat.verify_reviewed_bugfix_delta_from_git(
        current_root=loaded, source_checkout=repo, origin_commit=origin_sha,
    )

    changed = loaded / "src/bybit_workbench/universal_entry/entry.py"
    changed.write_bytes(changed.read_bytes() + b"# unauthorized\n")
    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat.verify_reviewed_bugfix_delta_from_git(
            current_root=loaded, source_checkout=repo, origin_commit=origin_sha,
        )

