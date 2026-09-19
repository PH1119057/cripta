from pathlib import Path

from operations.dashboard import app

ROOT = Path(__file__).resolve().parents[1]
SHA512_CRYPT_VECTOR = (
    "$6$criptaTestSalt$"
    ".0YmJDmhAKYbrnWunockhmC8fptK.C0YAL6SStjCKjjnZZxb5P0WJukPH2fz/"
    "MAW5BKr3JG9bviGDiXUlKN8w."
)


def test_system_crypt_verifier_preserves_sha512_crypt_compatibility() -> None:
    assert app._verify_system_password_hash("test-password", SHA512_CRYPT_VECTOR)
    assert not app._verify_system_password_hash("wrong-password", SHA512_CRYPT_VECTOR)


def test_system_crypt_verifier_rejects_nul_inputs() -> None:
    assert not app._verify_system_password_hash("test-password\x00suffix", SHA512_CRYPT_VECTOR)
    assert not app._verify_system_password_hash(
        "test-password", SHA512_CRYPT_VECTOR + "\x00suffix"
    )


def test_system_crypt_verifier_fails_closed_without_libcrypt(monkeypatch) -> None:
    monkeypatch.setattr(app, "_system_crypt", None)
    assert not app._verify_system_password_hash("test-password", SHA512_CRYPT_VECTOR)


def test_dashboard_source_does_not_import_deprecated_python_crypt_module() -> None:
    source = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "import crypt" not in source
    assert "_verify_system_password_hash(password, stored_hash)" in source
