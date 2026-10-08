from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"


def test_successful_schema_validation_preserves_owner_live_intent():
    source = SRC.read_text(encoding="utf-8")
    main = source[source.index("def main() -> None:"):]
    before, after = main.split("    except Exception as exc:", 1)
    assert "validate_runtime_schema_contract(bootstrap)" in before
    assert "disarm_new_entries(" not in before
    assert 'disarm_new_entries(' in after
    assert '"restart: schema validation failed; owner re-arm required"' in after
    assert after.index("disarm_new_entries(") < after.index('atomic_status(\n            "schema"')


def test_successful_bootstrap_still_reconciles_before_trading_threads():
    source = SRC.read_text(encoding="utf-8")
    main = source[source.index("def main() -> None:"):]
    assert main.index("validate_runtime_schema_contract(bootstrap)") < main.index("startup_live_safety(bootstrap")
    assert main.index("startup_live_safety(bootstrap") < main.index("threading.Thread(target=trade_loop")
