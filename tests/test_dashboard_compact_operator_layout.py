from pathlib import Path

HTML = Path("operations/dashboard/index.html").read_text(encoding="utf-8")


def test_health_controls_are_singletons_in_new_locations() -> None:
    for item in (
        "observerFaultBanner",
        "observerFaultSummary",
        "observerFaultItems",
        "tradeCommandStatus",
        "tradeOperationStatus",
        "tradeGateNotice",
        "tradeGateReasons",
        "tradeGateButton",
        "releaseArmCriticalBanner",
        "stamp",
    ):
        assert HTML.count(f'id="{item}"') == 1
    left = HTML.split('id="marketWorkspaceRail"', 1)[1].split("</aside>", 1)[0]
    assert 'id="observerFaultBanner"' in left
    assert 'id="tradeCommandStatus"' in left
    assert 'id="tradeOperationStatus"' in left
    center = HTML.split('id="liveDesk"', 1)[1].split('id="closedWorkspaceRail"', 1)[0]
    assert 'id="tradeGateButton"' in center
    assert 'id="tradeCommandStatus"' not in center


def test_floating_release_alert_remains_non_dismissible() -> None:
    assert 'id="releaseArmCriticalBanner" role="alert"' in HTML
    assert 'id="releaseArmCriticalDetails"' in HTML
    assert 'renderReleaseArmCritical(' in HTML


def test_compact_fault_list_preserves_original_resolution_semantics() -> None:
    assert '<summary><b>RED</b>' in HTML
    assert 'onclick="resolveObserverFault(this.dataset.faultId)"' in HTML
    assert "/api/observer-faults/resolve" in HTML
    assert "if(!confirm(" in HTML


def test_desktop_scroll_bounded_and_footer_static() -> None:
    assert "body:has(#liveDesk.active){overflow:hidden}" in HTML
    assert "overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain" in HTML
    assert "main:has(#liveDesk.active)>#liveDesk{height:calc(100vh - 118px)" in HTML
    assert 'id="releaseStampFooter"' in HTML
    assert "position:fixed;bottom:0" in HTML
    assert "SERVER RESEARCH CONTROL PLANE" not in HTML
