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
    assert "PYTHONPATH=/srv/cripta/runtime/current/src:/srv/cripta/runtime/current" in mayak


def test_r11_manifest_is_backed_up_and_installed_only_from_r11_onward() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    manifest = "/etc/cripta/market-source/required_public_feeds.json"
    exact = "operations/monitoring/r11_seven_public_feeds.json"
    assert manifest in source
    assert exact in source
    assert (
        'case "$ROADMAP470_STAGE" in\n  ""|R11|R11_REPAIR|R11_REPAIR2'
        '|R11_REPAIR3|R11_REPAIR4|R11_REPAIR5|R11_REPAIR6|R11_REPAIR7|R11_REPAIR8|R12|R13)'
    ) in source
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
        '("R11_REPAIR", "29cbe0229c137112e3f4e9d3b5b778ebdbc05738",' in source
        and "103ac4dc7ce0d7d06b413847938a22e356cc26b9" in source
    )
    assert (
        '("R11_REPAIR2", "103ac4dc7ce0d7d06b413847938a22e356cc26b9",' in source
        and "f16601924f6623260cbe45d96547c6e9fc71067a" in source
    )
    assert (
        '("R11_REPAIR3", "f16601924f6623260cbe45d96547c6e9fc71067a",' in source
        and "35923cfb7a8c1149284d2ba327a8d5ae306f5490" in source
    )
    assert (
        '("R11_REPAIR4", "35923cfb7a8c1149284d2ba327a8d5ae306f5490",' in source
        and "5fc00daf43676c30c494f871efb1a69e0eac33b4" in source
    )
    assert (
        '("R11_REPAIR5", "5fc00daf43676c30c494f871efb1a69e0eac33b4",' in source
        and "b9caae2843db344067412a5fb4b42ae15c244022" in source
    )
    assert (
        '("R11_REPAIR6", "b9caae2843db344067412a5fb4b42ae15c244022",' in source
        and "84f7fd5583daed5559a251284ffd90b8847fa496" in source
    )
    assert (
        '("R11_REPAIR7", "84f7fd5583daed5559a251284ffd90b8847fa496",' in source
        and "3f368fe88d72d6e10f0af0902d6205fc141d8447" in source
    )
    assert (
        '("R11_REPAIR8", "3f368fe88d72d6e10f0af0902d6205fc141d8447",' in source
        and "edc6363293b6f6dc64972f01684619a313936ef5" in source
    )
    assert (
        '("R12", "edc6363293b6f6dc64972f01684619a313936ef5",' in source
        and "dbcacb2203950fa0417d78ab93566aa1146dce79" in source
    )
    assert (
        '("R13", "dbcacb2203950fa0417d78ab93566aa1146dce79",' in source
        and "edf734b874ee11e1b988a6b1d86d0c6a452dd2da" in source
    )
