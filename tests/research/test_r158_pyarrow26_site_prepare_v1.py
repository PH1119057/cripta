"""Issue #158 isolated pyarrow decoder-site preparation tests."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_pyarrow26_site_prepare_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_pyarrow_prepare", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

COMMIT = "d" * 40


def synthetic_wheel(*, unsafe=False):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        if unsafe:
            archive.writestr("../escape.txt", "bad")
        else:
            archive.writestr("pyarrow/__init__.py", '__version__ = "26.0.0"\n')
            archive.writestr("pyarrow/parquet.py", "MARKER = 1\n")
            archive.writestr(
                "pyarrow-26.0.0.dist-info/METADATA",
                "Name: pyarrow\nVersion: 26.0.0\n",
            )
    return raw.getvalue()


def test_prepares_only_new_isolated_site_and_atomically_finishes(tmp_path):
    payload = synthetic_wheel()
    parent = tmp_path / "cache"
    parent.mkdir()
    output = parent / "decoder"

    def validate(site):
        assert (site / "pyarrow/__init__.py").is_file()
        assert (site / "pyarrow/parquet.py").is_file()
        return {"pyarrow_version": "26.0.0", "synthetic": True}

    result = m.prepare(
        COMMIT,
        output_root=output,
        fetcher=lambda _: payload,
        import_validator=validate,
        wheel_url="https://example.invalid/pyarrow.whl",
        wheel_sha256=hashlib.sha256(payload).hexdigest(),
        wheel_bytes=len(payload),
        require_actor=False,
        enforce_platform=False,
    )
    assert result["status"] == "ISOLATED_DECODER_SITE_COMPLETE"
    assert result["system_runtime_modified"] is False
    assert result["delete_operations"] == "NONE"
    assert not output.with_name(output.name + ".partial").exists()
    assert (output / "site/pyarrow/__init__.py").is_file()
    saved = json.loads((output / "MANIFEST.json").read_text())
    assert saved == result


def test_bad_wheel_hash_fails_closed(tmp_path):
    payload = synthetic_wheel()
    parent = tmp_path / "cache"
    parent.mkdir()
    output = parent / "decoder"
    with pytest.raises(ValueError, match="WHEEL_SHA256_MISMATCH"):
        m.prepare(
            COMMIT,
            output_root=output,
            fetcher=lambda _: payload,
            import_validator=lambda _: {},
            wheel_url="https://example.invalid/pyarrow.whl",
            wheel_sha256="0" * 64,
            wheel_bytes=len(payload),
            require_actor=False,
            enforce_platform=False,
        )
    assert not output.exists()
    assert output.with_name(output.name + ".partial").exists()


def test_unsafe_wheel_member_is_rejected(tmp_path):
    payload = synthetic_wheel(unsafe=True)
    parent = tmp_path / "cache"
    parent.mkdir()
    output = parent / "decoder"
    with pytest.raises(ValueError, match="UNSAFE_WHEEL_MEMBER"):
        m.prepare(
            COMMIT,
            output_root=output,
            fetcher=lambda _: payload,
            import_validator=lambda _: {},
            wheel_url="https://example.invalid/pyarrow.whl",
            wheel_sha256=hashlib.sha256(payload).hexdigest(),
            wheel_bytes=len(payload),
            require_actor=False,
            enforce_platform=False,
        )


def test_existing_final_root_blocks_mutation(tmp_path):
    parent = tmp_path / "cache"
    parent.mkdir()
    output = parent / "decoder"
    output.mkdir()
    with pytest.raises(FileExistsError, match="IMMUTABLE_DECODER_SITE"):
        m.prepare(
            COMMIT,
            output_root=output,
            fetcher=lambda _: b"unused",
            import_validator=lambda _: {},
            require_actor=False,
            enforce_platform=False,
        )
