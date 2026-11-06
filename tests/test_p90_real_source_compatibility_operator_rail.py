"""Static security contract for explicit P90 source compatibility registration."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "operations/systemd/cripta-p90-source-compatibility-register.service"
SCRIPT = ROOT / "operations/implementation/register_p90_real_source_compatibility.py"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def test_registration_unit_is_inert_cripta_oneshot_without_filesystem_write():
    unit = UNIT.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "User=cripta" in unit and "Group=cripta" in unit
    assert "ProtectSystem=strict" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "ReadWritePaths=" not in unit
    assert "[Install]" not in unit
    assert "--current-release --active-epoch --register" in unit
    assert "control_p90l90_real_v3_prearm.py" not in unit


def test_registration_cli_derives_only_exact_current_release_and_one_active_epoch():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'release.add_argument("--current-release", action="store_true")' in source
    assert 'epoch.add_argument("--active-epoch", action="store_true")' in source
    assert "WHERE status='ACTIVE' ORDER BY opened_at,epoch_id" in source
    assert "len(rows) != 1" in source
    assert "P90_REAL_COMPATIBILITY_ACTIVE_EPOCH_UNVERIFIED" in source


def test_installer_only_installs_operator_unit_and_does_not_manage_start_it():
    source = INSTALLER.read_text(encoding="utf-8")
    spec = (
        '"operations/systemd/cripta-p90-source-compatibility-register.service|'
        'cripta-p90-source-compatibility-register.service|runtime"'
    )
    assert source.count(spec) == 1
    managed = source[source.index("managed_services=("):]
    assert "cripta-p90-source-compatibility-register.service" not in managed

def test_pruned_origin_fallback_is_local_read_only_git_only():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'SOURCE_CHECKOUT = Path("/srv/cripta/source_checkout")' in source
    assert 'if (retained_origin / "INSTALLED_COMMIT").is_file()' in source
    assert '"origin_git_checkout": SOURCE_CHECKOUT' in source
    for forbidden in ("git fetch", "git clone", "git worktree", "git checkout"):
        assert forbidden not in source

