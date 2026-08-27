from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bybit_workbench.mayak.research.verdict import verify_frozen_manifest


def test_frozen_manifest_verification(tmp_path: Path) -> None:
    content = b'{"selected_candidates": []}\n'
    (tmp_path / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.json").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (tmp_path / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.sha256").write_text(
        f"{digest}  MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.json\n", encoding="ascii"
    )
    manifest, actual = verify_frozen_manifest(tmp_path)
    assert manifest == {"selected_candidates": []}
    assert actual == digest


def test_frozen_manifest_rejects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.json"
    path.write_text(json.dumps({"selected_candidates": []}), encoding="utf-8")
    (tmp_path / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.sha256").write_text(
        f"{'0' * 64}  {path.name}\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="mismatch"):
        verify_frozen_manifest(tmp_path)
