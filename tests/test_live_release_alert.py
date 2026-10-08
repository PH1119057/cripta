from bybit_workbench.live_release_alert import project_release_arm_health


def test_release_mismatch_red_banner_even_without_trade_signal():
    result = project_release_arm_health(
        loaded_commit="b" * 40,
        gate_open=True,
        sessions=[("r1_injusdt", "INJUSDT", "a" * 40)],
        recent_not_arm_ready=0,
        last_not_arm_ready_at=None,
    )
    assert result["critical"] is True
    assert result["mismatched_sessions"][0]["symbol"] == "INJUSDT"


def test_recovered_exact_release_does_not_alarm():
    result = project_release_arm_health(
        loaded_commit="a" * 40,
        gate_open=True,
        sessions=[("r1_injusdt", "INJUSDT", "a" * 40)],
        recent_not_arm_ready=0,
        last_not_arm_ready_at=None,
    )
    assert result["critical"] is False


def test_real_entry_not_ready_alone_alarms():
    result = project_release_arm_health(
        loaded_commit="a" * 40,
        gate_open=True,
        sessions=[("r1_injusdt", "INJUSDT", "a" * 40)],
        recent_not_arm_ready=1,
        last_not_arm_ready_at="2026-10-09T00:00:00Z",
    )
    assert result["critical"] is True


def test_disarmed_gate_still_shows_unresolved_release_mismatch():
    result = project_release_arm_health(
        loaded_commit="b" * 40,
        gate_open=False,
        sessions=[("r1_injusdt", "INJUSDT", "a" * 40)],
        recent_not_arm_ready=3,
        last_not_arm_ready_at=None,
    )
    assert result["critical"] is True


def test_unknown_loaded_commit_is_fail_closed_in_read_model():
    result = project_release_arm_health(
        loaded_commit="",
        gate_open=True,
        sessions=[],
        recent_not_arm_ready=0,
        last_not_arm_ready_at=None,
    )
    assert result["critical"] is True


def test_gate_open_without_any_active_sessions_is_critical():
    result = project_release_arm_health(
        loaded_commit="a" * 40,
        gate_open=True,
        sessions=[],
        recent_not_arm_ready=0,
        last_not_arm_ready_at=None,
    )
    assert result["critical"] is True
