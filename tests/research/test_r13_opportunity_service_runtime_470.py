"""R13 #470 regression: Opportunity Tracker service can import installed app."""

from pathlib import Path

UNIT = Path(
    "research/server/monitoring/cripta-opportunity-tracker.service"
).read_text(encoding="utf-8")


def test_r13_opportunity_service_loads_installed_application_package() -> None:
    assert "User=cripta" in UNIT
    assert "Group=cripta" in UNIT
    assert "Environment=PYTHONPATH=/srv/cripta/runtime/current/src" in UNIT
    assert (
        "ExecStart=/srv/cripta/runtime/current/.venv/bin/python "
        "/srv/cripta/runtime/current/research/server/monitoring/opportunity_tracker.py"
        in UNIT
    )
