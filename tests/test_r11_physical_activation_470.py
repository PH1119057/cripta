"""#470 R11 physical activation: one source, PG-only MAYAK, rollback-safe manifest."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_UNIT = ROOT / "operations/systemd/cripta-native-public-trade-source.service"
MAYAK_UNIT = ROOT / "operations/systemd/cripta-mayak-v2.service"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"
RAIL = ROOT / "operations/infrastructure/cripta-roadmap470-staged-release"


def test_r11_units_explicitly_activate_single_source_and_pg_mayak() -> None:
    native = NATIVE_UNIT.read_text(encoding="utf-8")
    mayak = MAYAK_UNIT.read_text(encoding="utf-8")
    assert "CRIPTA_R11_MAYAK_ANCHORS=1" in native
    assert "CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=1" in native
    assert "CRIPTA_R09_SOURCE_POSTFILL_BROKER=ENABLED" in native
    assert "CRIPTA_MAYAK_SOURCE_MODE=PG_PUBLICATION" in mayak


def test_r11_manifest_is_backed_up_and_installed_only_from_r11_onward() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    manifest = "/etc/cripta/market-source/required_public_feeds.json"
    exact = "operations/monitoring/r11_seven_public_feeds.json"
    assert manifest in source
    assert exact in source
    assert 'case "$ROADMAP470_STAGE" in\n  ""|R11|R12|R13)' in source
    assert "install -d -o root -g cripta -m 0750 /etc/cripta/market-source" in source
    assert "install -o root -g cripta -m 0640" in source
    assert "R11_SEVEN_FEED_MANIFEST=INSTALLED_EXACT" in source
    backup_line = next(
        line for line in source.splitlines()
        if line.startswith("for path in /usr/local/sbin/cripta-apply-incoming")
    )
    assert manifest in backup_line


def test_r11_physical_stage_targets_are_exact_activated_chain() -> None:
    source = RAIL.read_text(encoding="utf-8")
    assert (
        '("R11", "0cdf4ce0ca80ee4dcc5c7cc5c2595223fd4208d4",' in source
        and "29cbe0229c137112e3f4e9d3b5b778ebdbc05738" in source
    )
    assert (
        '("R12", "29cbe0229c137112e3f4e9d3b5b778ebdbc05738",' in source
        and "fedf6bea772a9d7b4269e2b2c856a8d7bd65efd9" in source
    )
    assert (
        '("R13", "fedf6bea772a9d7b4269e2b2c856a8d7bd65efd9",' in source
        and "8c5164d33972bdfc357c64b29927e3caacc964b7" in source
    )
