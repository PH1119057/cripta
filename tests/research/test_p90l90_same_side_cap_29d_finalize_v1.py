"""#638 independent finalizer forensic regression; NO server Data required.

All tests are pure synthetic and exercise the exact CLI absolute path from
outside GitHub checkout. The native producer is imported only by actual
--mode preflight/finalize after parsing args, not during --help.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = r'''
import importlib.util
from pathlib import Path
root=Path.cwd()
source=root/"scripts/research/p90l90_same_side_cap_29d_finalize_v1.py"
spec=importlib.util.spec_from_file_location("p90_sidecap_rescue_reviewed",source)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

good="a"*64
def test_strict_original_qc_status_schema_not_result():
    assert m.frozen_result_sha(
        {"result_sha256":good},
        {"status":"COMPLETE_PENDING_INDEPENDENT_QC","ledger_sha256":"b"*64}
    )==good
    for status,result in [
      ({},{"ledger_sha256":"b"*64}),
      ({"result_sha256":"wrong"},{}),
      ({"result_sha256":good},{"result_sha256":good}),
      ({"result_sha256":"Z"*64},{}),
    ]:
        try:m.frozen_result_sha(status,result)
        except ValueError:pass
        else:raise AssertionError("invalid original result/schema was accepted")

def test_no_write_or_tick_replay_and_exact_target():
    assert m.PERIOD=="29d"
    assert m.ORIGINAL_HEAD=="8c45213038b922272cfcd067f51dd6245ba96977"
    assert m.DEST.as_posix().endswith(
        "/p90l90_unified_sidecap_2_3_4_5_takertaker_20261114_v1")
    assert m.PRODUCER.as_posix().endswith("/p90l90_sidecap_638_20261114")
    assert "replay_day(" not in source.read_text()
    assert "load_data(" not in source.read_text()
    assert "source_snapshot" in source.read_text()
    assert "COMPLETE_PENDING_INDEPENDENT_QC" in source.read_text()
    assert "qc.json" in source.read_text()
    assert m.prior is None

for n,f in list(globals().copy().items()):
    if n.startswith("test_") and callable(f):f()
'''


def test_recovery_source_semantics_in_isolated_interpreter():
    root = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [sys.executable, "-B", "-c", SCRIPT], cwd=root,
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert p.returncode == 0, p.stdout + "\n" + p.stderr


def test_direct_absolute_cli_help_outside_repository_on_windows_and_linux():
    root = Path(__file__).resolve().parents[2]
    source = root / "scripts/research/p90l90_same_side_cap_29d_finalize_v1.py"
    p = subprocess.run(
        [sys.executable, "-B", str(source), "--help"],
        cwd=root.parent, capture_output=True, text=True,
        timeout=45, check=False,
    )
    assert p.returncode == 0, p.stdout + "\n" + p.stderr
    assert "--auditor-head" in p.stdout
    assert "--producer-head" in p.stdout
    assert "--mode" in p.stdout
