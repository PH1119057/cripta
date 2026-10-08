from pathlib import Path


def test_sound_events_have_bounded_queue_and_suspended_audio_recovery() -> None:
    source = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "pendingSounds.push({kind,at:Date.now()})" in source
    assert "SOUND_MAX_LATENCY_MS=5000" in source
    assert "expirePendingSounds()" in source
    assert "flushSounds()" in source
    assert "cripta-last-close-ms" in source
    assert "cripta-open-position-keys" in source
    assert "visibilitychange" in source


def test_close_sound_uses_net_result() -> None:
    source = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "exitSound(Number(x.net_pnl)>0)" in source
    assert "profitSoundChoice.value:lossSoundChoice.value" in source
