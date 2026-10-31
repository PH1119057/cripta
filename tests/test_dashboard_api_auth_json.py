from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
INDEX = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")


def test_api_auth_failure_returns_json_instead_of_login_redirect() -> None:
    start = APP.index("    def require_login(self) -> bool:")
    end = APP.index("    def login_page(", start)
    body = APP[start:end]
    assert 'request_path.startswith("/api/")' in body
    assert '"error": "AUTH_REQUIRED"' in body
    assert '"message": "Сессия Dashboard истекла или недействительна"' in body
    assert '"application/json; charset=utf-8"' in body
    assert "self.send_response(303)" in body


def test_live_post_reports_auth_expiry_without_parsing_html_as_json() -> None:
    start = INDEX.index("async function livePostTimed(")
    end = INDEX.index("function rearmReasonRu", start)
    body = INDEX[start:end]
    assert "content-type" in body
    assert "application/json" in body
    assert "HTTP ${r.status} вместо JSON" in body
    assert "r.status===401" in body
    assert "AUTH_REQUIRED" in body
    assert "Сессия Dashboard истекла" in body
