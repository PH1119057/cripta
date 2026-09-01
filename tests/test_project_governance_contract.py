from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_governance_is_canonical_and_referenced() -> None:
    governance = (ROOT / "docs" / "PROJECT_GOVERNANCE_RU.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    authority = (ROOT / "docs" / "DOCUMENT_AUTHORITY_RU.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "PROJECT_ARCHITECTURE_RU.md").read_text(encoding="utf-8")

    assert "Статус:** канонический нормативный контракт" in governance
    assert "REMOTE_HEAD = SOURCE_HEAD = INSTALLED_COMMIT = LOADED_COMMIT" in governance
    assert "UNRESOLVED_EXACT_LINK" in governance
    assert "OWNER_MANUAL_INTERVENTION" in governance
    assert "PROJECT_GOVERNANCE_RU.md" in agents
    assert "PROJECT_GOVERNANCE_RU.md" in authority
    assert "PROJECT_GOVERNANCE_RU.md" in architecture


def test_governance_freezes_layer_ownership() -> None:
    text = (ROOT / "docs" / "PROJECT_GOVERNANCE_RU.md").read_text(encoding="utf-8")

    assert "После подтверждённого fill владение\nEntry заканчивается" in text
    assert "Маяк только наблюдает" in text
    assert "Диспетчер только даёт рекомендацию" in text
    assert "symbol + ближайшее время" in text
