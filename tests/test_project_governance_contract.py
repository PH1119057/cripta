from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_governance_is_canonical_and_authoritative() -> None:
    governance = (ROOT / "docs" / "PROJECT_GOVERNANCE_RU.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    authority = (ROOT / "docs" / "DOCUMENT_AUTHORITY_RU.md").read_text(encoding="utf-8")

    assert "Статус:** канонический нормативный контракт" in governance
    for token in ("REMOTE_HEAD", "SOURCE_HEAD", "INSTALLED_COMMIT", "LOADED_COMMIT"):
        assert token in governance
    assert "PROJECT_GOVERNANCE_RU.md" in agents
    assert "LEVEL 2 — BOOTSTRAP / GOVERNANCE" in authority
    assert "docs/PROJECT_GOVERNANCE_RU.md" in authority
    assert "ARCHITECTURE_CONFLICT=YES" in governance
    assert "HARD_STOP=YES" in governance


def test_governance_and_architecture_freeze_layer_ownership() -> None:
    governance = (ROOT / "docs" / "PROJECT_GOVERNANCE_RU.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "PROJECT_ARCHITECTURE_RU.md").read_text(encoding="utf-8")

    assert "Entry принимает решение об открытии" in governance
    assert "Exit сопровождает и закрывает по той же strategy binding" in governance
    assert "Технический контур не получает права изменить торговую policy" in governance
    assert "Dispatcher публикует snapshot торговой ёмкости" in governance
    assert "Ownership нельзя восстанавливать по `symbol + время`" in governance

    assert "После fill Entry ownership конкретной попытки заканчивается." in architecture
    assert "не блокирует mutation напрямую" in architecture
    assert "`Risk` — не отдельный верхний архитектурный слой." in architecture
