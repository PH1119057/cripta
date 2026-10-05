from pathlib import Path


SOURCE = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")


def test_initial_protection_not_modified_requires_exchange_verification() -> None:
    block = SOURCE.split(
        'elif kind in {"break_even", "current_stop", "initial_protection"}:', 1
    )[1].split('elif kind == "trailing_stop":', 1)[0]
    assert 'kind != "initial_protection" or "not modified" not in str(exc).lower()' in block
    assert '"/v5/position/list"' in block
    assert "verified_position" in block
    assert "verified_stop == stop" in block
    assert "target is None or verified_target == target" in block
    assert '"verifiedFromExchange": True' in block


def test_initial_protection_not_modified_stays_fail_closed_on_mismatch() -> None:
    block = SOURCE.split(
        'elif kind in {"break_even", "current_stop", "initial_protection"}:', 1
    )[1].split('elif kind == "trailing_stop":', 1)[0]
    assert "initial protection not-modified could not be verified: position missing" in block
    assert "initial protection not-modified verification mismatch" in block
    assert "if not stop_matches or not target_matches:" in block


def test_other_protection_commands_do_not_inherit_34040_exception() -> None:
    block = SOURCE.split(
        'elif kind in {"break_even", "current_stop", "initial_protection"}:', 1
    )[1].split('elif kind == "trailing_stop":', 1)[0]
    assert 'if kind != "initial_protection"' in block
