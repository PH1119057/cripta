from __future__ import annotations

import argparse
import json
from pathlib import Path

from bybit_workbench.mayak.research.data_audit import audit_frozen_panel, write_audit
from bybit_workbench.mayak.research.event_truth import (
    load_discovery_events,
    write_normalized_events,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed MAYAK frozen data/event audit")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    report = audit_frozen_panel(root)
    write_audit(output / "MAYAK_DATA_COMPLETENESS.json", report)
    events = load_discovery_events(root)
    write_normalized_events(output / "MAYAK_ALL9_NORMALIZED_EVENTS.json", events)
    print(
        json.dumps(
            {
                "universe_complete": report["universe_complete"],
                "dataset_fingerprint": report["dataset_fingerprint"],
                "normalized_events": len(events),
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0 if bool(report["universe_complete"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
