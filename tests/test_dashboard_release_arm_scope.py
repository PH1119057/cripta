"""Run the actual independent Dashboard UI-only scope verifier in isolation."""
import os
import subprocess
import sys
from pathlib import Path


def test_dashboard_read_model_scope_accepts_only_approved_alert_changes(tmp_path):
    base = os.getenv("TEST_DASHBOARD_BASE_COMMIT")
    if not base:
        import pytest
        pytest.skip("base Git commit not supplied")
    old_html = tmp_path / "old.html"
    old_app = tmp_path / "old_app.py"
    new_html = tmp_path / "new.html"
    new_app = tmp_path / "new_app.py"
    for name, output in [
        ("operations/dashboard/index.html", old_html),
        ("operations/dashboard/app.py", old_app),
    ]:
        output.write_bytes(
            subprocess.check_output(["git", "show", f"{base}:{name}"])
        )
    new_html.write_bytes(Path("operations/dashboard/index.html").read_bytes())
    new_app.write_bytes(Path("operations/dashboard/app.py").read_bytes())
    deployer = Path(
        "operations/infrastructure/deploy_dashboard_ui.sh"
    ).read_text(encoding="utf-8")
    prefix = (
        'python3 - "$tmp/old.html" "$tmp/new.html" "$tmp/old_app.py" '
        '"$tmp/new_app.py" <<\'PY\'\\n'
    )
    script = deployer.split(prefix, 1)[1].split("\nPY\n", 1)[0]
    p = subprocess.run(
        [sys.executable, "-c", script, str(old_html), str(new_html),
         str(old_app), str(new_app)],
        check=False, capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stdout + "\n" + p.stderr
    assert "DASHBOARD_PRESENTATION_READ_MODEL_SCOPE=PASS" in p.stdout
