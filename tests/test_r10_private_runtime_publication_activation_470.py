"""#470 R10 physical activation: private runtimes use shared publication explicitly."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_r10_publication_is_explicitly_enabled_for_main_and_sub_runtime() -> None:
    main = (
        ROOT / "operations" / "systemd"
        / "cripta-private-runtime.service.d" / "10-pythonpath.conf"
    ).read_text(encoding="utf-8")
    sub = (
        ROOT / "operations" / "systemd"
        / "cripta-private-runtime@.service"
    ).read_text(encoding="utf-8")
    setting = "Environment=CRIPTA_R10_EXECUTION_PUBLICATION=1"
    assert main.count(setting) == 1
    assert sub.count(setting) == 1


def test_r10_code_default_stays_off_outside_reviewed_service_activation() -> None:
    source = (
        ROOT / "operations" / "connectivity" / "private_runtime.py"
    ).read_text(encoding="utf-8")
    assert 'os.environ.get("CRIPTA_R10_EXECUTION_PUBLICATION", "0")' in source
    assert "R10_EXECUTION_PUBLICATION_FLAG_INVALID" in source
