from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from bybit_workbench.mayak.research.discovery import (
    analyze_discovery,
    freeze_discovery,
    write_discovery,
)
from bybit_workbench.mayak.research.event_truth import load_discovery_events
from bybit_workbench.mayak.research.feature_engine import (
    FeatureSpec,
    compute_event_features,
    load_discovery_series,
    write_feature_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAYAK ALL9 discovery and immutable freeze")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    audit = cast(
        dict[str, Any],
        json.loads((output / "MAYAK_DATA_COMPLETENESS.json").read_text(encoding="utf-8")),
    )
    if not audit.get("universe_complete"):
        raise RuntimeError("fail closed: frozen panel data audit is not complete")
    events = load_discovery_events(root)
    print(f"stage=features processed=0/{len(events)} percent=0")
    spec = FeatureSpec()
    rows = compute_event_features(events, load_discovery_series(root), spec)
    write_feature_outputs(output, rows, spec)
    print(f"stage=features processed={len(events)}/{len(events)} percent=100")
    eligible = [row for row in rows if row["primary_label"] != "UNRESOLVED_DATA_END"]
    discovery = analyze_discovery(eligible)
    write_discovery(output, discovery)
    feature_spec = cast(
        dict[str, Any],
        json.loads((output / "MAYAK_FEATURE_SPEC.json").read_text(encoding="utf-8")),
    )
    digest = freeze_discovery(
        output,
        discovery=discovery,
        feature_spec=feature_spec,
        dataset_fingerprint=str(audit["dataset_fingerprint"]),
        entry_fingerprint=events[0].entry_fingerprint,
    )
    print(
        json.dumps(
            {
                "eligible_events": len({str(row["event_id"]) for row in eligible}),
                "feature_rows": len(rows),
                "selected_candidates": len(discovery["selected_candidates"]),
                "frozen_manifest_sha256": digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
