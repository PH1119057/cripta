"""Research batch: fail closed on mismatched existing outputs and allow verified resume."""
import importlib.util
import json
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_firstmonth_known_physical_batch_v1.py"
)
SPEC = importlib.util.spec_from_file_location("r158_firstmonth_batch", SCRIPT)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)


def test_existing_price_requires_verified_hash_and_qc(tmp_path, monkeypatch):
    monkeypatch.setitem(SUT.GROUPS["price"], "root", tmp_path)
    status, _ = SUT.verify_existing("price", "APTUSDT", 2)
    assert status == "MISSING"
    root = tmp_path / "physical_2d/APTUSDT"
    root.mkdir(parents=True)
    try:
        SUT.verify_existing("price", "APTUSDT", 2)
    except ValueError as err:
        assert "PARTIAL_RESULT" in str(err)
    else:
        raise AssertionError("Incomplete folder must not be silently resumed")
    program = root / "source_snapshot/materializer.py"
    program.parent.mkdir()
    program.write_text("sample program")
    output = root / "features.jsonl.gz"
    output.write_bytes(b"fake immutable data")
    (root / "result.json").write_text(json.dumps({
        "status": "FIRST_MONTH_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC",
        "days": 2,
        "symbols": ["APTUSDT"],
        "file_sha256": SUT.sha(output),
        "program_sha256": SUT.sha(program),
    }))
    status, _ = SUT.verify_existing("price", "APTUSDT", 2)
    assert status == "PHYSICAL_ONLY"
    (root / "independent_qc_report.json").write_text(json.dumps({
        "status": "ALL_MINUTES_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "symbol": "APTUSDT", "days": 2, "rows": 2880,
        "original_sha256": SUT.sha(output),
    }))
    status, _ = SUT.verify_existing("price", "APTUSDT", 2)
    assert status == "QC_PASS"
    output.write_bytes(b"corrupted")
    try:
        SUT.verify_existing("price", "APTUSDT", 2)
    except ValueError as err:
        assert "HASH_DRIFT" in str(err)
    else:
        raise AssertionError("Corrupted result must fail closed")


def test_existing_book_requires_full_decimals_receipt(tmp_path, monkeypatch):
    monkeypatch.setitem(SUT.GROUPS["book"], "root", tmp_path)
    root = tmp_path / "physical_30d/DOTUSDT"
    root.mkdir(parents=True)
    source = root / "source_snapshot/book_materializer.py"
    source.parent.mkdir()
    source.write_text("immutable")
    output = root / "book_features.jsonl.gz"
    output.write_bytes(b"book bytes")
    (root / "result.json").write_text(json.dumps({
        "days": 30, "symbol": "DOTUSDT",
        "data_sha256": SUT.sha(output), "code_sha256": SUT.sha(source),
    }))
    (root / "independent_qc_report.json").write_text(json.dumps({
        "status": "ALL_BOOK_OBSERVED_MINUTES_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "symbol": "DOTUSDT", "scope_days": 30,
        "totals": {"rows": 43200}, "book_feature_sha256": SUT.sha(output),
    }))
    assert SUT.verify_existing("book", "DOTUSDT", 30)[0] == "QC_PASS"
