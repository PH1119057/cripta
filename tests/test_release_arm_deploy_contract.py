from pathlib import Path


def test_red_banner_is_above_all_portal_sections_and_not_audio_dependent():
    html = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    banner = html.index('id="releaseArmCriticalBanner"')
    assert banner < html.index("<header>")
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html
    assert 'position:sticky;top:0;z-index:1000' in html
    assert "renderReleaseArmCritical(d.live_trading)" in html
    assert "state.critical" in html
    assert "lt.release_arm_incidents" in html
    assert "soundEnabled" not in html[
        html.index("function renderReleaseArmCritical"):html.index(
            "function renderLiveState"
        )
    ]


def test_read_model_exposes_durable_incident_and_loaded_release():
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "release_arm_incidents" in source
    assert "release_arm_health" in source
    assert "project_release_arm_health(" in source
    assert "LOADED_RELEASE_COMMIT" in source


def test_release_runner_inhibits_entry_before_installer_without_flat_gate():
    runner = Path("operations/infrastructure/cripta-apply-incoming").read_text(
        encoding="utf-8"
    )
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert runner.index("NEW_REAL_ENTRY_RELEASE_INHIBIT=PASS") < runner.index(
        "=== PACKAGE INSTALLER START ==="
    )
    assert "20261009_release_arm_incidents.sql" in installer
    assert "TRADING_STATE_INFORMATIONAL=" in installer
    assert "Release installation is independent of all Exchange" in installer
