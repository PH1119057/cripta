"""R14 regression for exact R06-origin P90 source compatibility.

The final #470 release must recognize only the already accepted R07 provenance
and R10 PG-publication consumer bytes when the active real source epoch still
names the physically accepted R06 origin. No broader consumer delta is allowed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat


def test_r14_r06_origin_accepts_only_exact_reviewed_two_file_delta() -> None:
    expected = compat._REVIEWED_R07_R10_PUBLIC_SOURCE_SHA256
    changed = set(expected)

    assert compat._reviewed_delta_for_origin(
        changed,
        origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
    ) == expected

    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat._reviewed_delta_for_origin(
            changed | {"universal_exit/unreviewed.py"},
            origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
        )

    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat._reviewed_delta_for_origin(
            changed,
            origin_commit="0" * 40,
        )


def test_r14_reviewed_r07_r10_hashes_bind_current_exact_bytes() -> None:
    package = Path(compat.__file__).resolve().parents[1]
    reviewed = compat._REVIEWED_R07_R10_PUBLIC_SOURCE_SHA256
    for relative, expected in reviewed.items():
        actual = hashlib.sha256((package / relative).read_bytes()).hexdigest()
        assert actual == expected
