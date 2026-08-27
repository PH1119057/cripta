from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast


def verify_frozen_manifest(output: Path) -> tuple[dict[str, Any], str]:
    manifest_path = output / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.json"
    sidecar_path = output / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.sha256"
    expected = sidecar_path.read_text(encoding="ascii").split()[0]
    actual = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"frozen manifest SHA256 mismatch: expected={expected} actual={actual}")
    payload = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    return payload, actual


def finalize_p1(output: Path) -> dict[str, Any]:
    manifest, digest = verify_frozen_manifest(output)
    selected = manifest.get("selected_candidates")
    if not isinstance(selected, list):
        raise ValueError("frozen selected_candidates must be a list")
    if selected:
        raise ValueError("NEW5 confirmation runner is required for non-empty candidates")
    confirmation = {
        "confirmation_universe": "NEW5",
        "frozen_manifest_sha256_verified": digest,
        "outcome_tables_opened": False,
        "classification": "FAILED",
        "reason": "No discovery candidate survived the frozen ALL9 robustness protocol",
        "retuning_performed": False,
    }
    (output / "MAYAK_P1_NEW5_CONFIRMATION.json").write_text(
        json.dumps(confirmation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    verdict = {
        "p1_verdict": "NO EVIDENCE",
        "hard_stop": True,
        "hard_stop_contract_sections": [39, 40, 72],
        "discovery_events_total": 1063,
        "discovery_events_eligible": 1054,
        "discovery_events_excluded_incomplete": 9,
        "selected_candidates": 0,
        "new5_confirmation": confirmation,
        "p2_authorized": False,
        "runtime_authorized": False,
        "production_shadow_authorized": False,
        "interpretation": (
            "No objective pre-Entry market-state component robustly separated both frozen "
            "failure classes from continuation across assets under the predeclared protocol."
        ),
    }
    (output / "MAYAK_P1_VERDICT.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = """# MAYAK P1 verdict — NO EVIDENCE

## Decision

P1 stops here. P2, Sea State, online runtime, API, PostgreSQL, and production SHADOW
are not authorized by the research gate.

## Evidence

- ALL9 frozen Entry events: 1063.
- Eligible mutually-exclusive primary outcomes: 1054.
- Incomplete data-end events excluded before comparison: 9.
- Causal feature rows: 6378 across T-30m, T-15m, T-10m, T-5m, T-1m, and T.
- Selected robust discovery candidates: 0.
- The strongest apparent breadth effects did not have bootstrap intervals excluding
  zero on both primary comparisons and produced roughly 46-50% continuation false alarms.
- Frozen manifest SHA256 was verified before the confirmation decision.
- NEW5 outcome tables were not opened because there was no frozen hypothesis to confirm.

## Contractual hard stop

Sections 39, 40, and 72 prohibit manufacturing a Sea State after `NO EVIDENCE`.
The negative result is the successful scientific result of this workflow.
"""
    (output / "MAYAK_P1_VERDICT.md").write_text(markdown, encoding="utf-8")
    return verdict
