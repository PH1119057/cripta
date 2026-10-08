from pathlib import Path

HTML=(Path(__file__).resolve().parents[1]/"operations/dashboard/index.html").read_text(encoding="utf-8")


def test_sound_queue_expires_and_never_replays_delayed_events():
    assert "SOUND_MAX_LATENCY_MS=5000" in HTML
    assert "SOUND_PENDING_LIMIT=8" in HTML
    assert "pendingSounds.filter(item=>now-item.at<=SOUND_MAX_LATENCY_MS)" in HTML
    assert "pendingSounds.push({kind,at:Date.now()})" in HTML
    assert "queue.forEach(item=>playSound(item.kind))" in HTML
    assert "index*1200" not in HTML


def test_audio_unblock_has_explicit_check_and_feedback():
    assert 'id="testSound"' in HTML
    assert "getElementById('testSound').addEventListener('click'" in HTML
    assert "звук заблокирован браузером" in HTML
    assert "пропущено устаревших" in HTML
    assert "audioContext.resume().then(flushSounds)" in HTML
