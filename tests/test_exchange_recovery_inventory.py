from pathlib import Path
import sys
import pytest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "operations/connectivity"),
                str(Path(__file__).resolve().parents[1] / "research/server/connectivity")]
import private_runtime as runtime

def test_sixty_positions_require_two_pages(monkeypatch):
    def get(endpoint, params, *_):
        start = 50 if params.get("cursor") else 0
        count = 10 if start else 50
        return {"retCode": 0, "result": {
            "list": [{"symbol": str(i), "size": "1"} for i in range(start, start+count)],
            "nextPageCursor": "" if start else "NEXT"}}, 0
    monkeypatch.setattr(runtime, "api_get", get)
    rows = runtime._complete_exchange_inventory("/v5/position/list", {"limit": "50"}, "k", "s")
    assert len(rows) == 60
    assert len({r["symbol"] for r in rows}) == 60

def test_repeat_cursor_is_not_flat_account(monkeypatch):
    monkeypatch.setattr(runtime, "api_get", lambda *args: (
        {"retCode": 0, "result": {"list": [], "nextPageCursor": "DUP"}}, 0))
    with pytest.raises(runtime.ExchangeReadUnavailable):
        runtime._complete_exchange_inventory("/v5/position/list", {}, "k", "s")

def test_rejected_page_is_not_flat_account(monkeypatch):
    monkeypatch.setattr(runtime, "api_get", lambda *args: (
        {"retCode": 10001, "retMsg": "bad"}, 0))
    with pytest.raises(runtime.ExchangeReadUnavailable):
        runtime._complete_exchange_inventory("/v5/position/list", {}, "k", "s")
