from pathlib import Path


def test_sound_events_survive_reload_and_suspended_audio_context() -> None:
    source = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "pendingSounds.push(kind)" in source
    assert "flushSounds()" in source
    assert "cripta-last-close-ms" in source
    assert "cripta-open-position-keys" in source
    assert "visibilitychange" in source


def test_close_sound_uses_net_result() -> None:
    source = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "exitSound(Number(x.net_pnl)>0)" in source
    assert "profitSoundChoice.value:lossSoundChoice.value" in source
