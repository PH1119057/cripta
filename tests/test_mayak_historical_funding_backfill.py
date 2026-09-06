from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.mayak.research import historical_funding_backfill as mod


def test_page_rows_parses_exact_timestamp_and_rate() -> None:
    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {"fundingRateTimestamp": "1779148800000", "fundingRate": "0.0001"},
                {"fundingRateTimestamp": "1779120000000", "fundingRate": "-0.00002"},
            ]
        },
    }
    assert mod._page_rows(payload) == [
        (1779148800000, "0.0001"),
        (1779120000000, "-0.00002"),
    ]


def test_download_symbol_pages_and_writes_ascending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    start = datetime(2026, 5, 17, tzinfo=UTC)
    end = datetime(2026, 5, 19, tzinfo=UTC)
    t1 = int((start.timestamp() + 8 * 3600) * 1000)
    t2 = int((start.timestamp() + 16 * 3600) * 1000)
    pages = [
        {
            "retCode": 0,
            "result": {
                "list": [
                    {"fundingRateTimestamp": str(t2), "fundingRate": "0.0001"},
                    {"fundingRateTimestamp": str(t1), "fundingRate": "0.0002"},
                ]
            },
        }
    ]

    def fake(url: str, *, label: str):
        calls.append(url)
        return pages.pop(0)

    monkeypatch.setattr(mod, "read_json_with_retry", fake)
    result = mod.download_symbol(
        "UNIUSDT",
        endpoint="https://example.test",
        start=start,
        end=end,
        output_dir=tmp_path,
    )
    assert result["rows"] == 2
    assert len(calls) == 1
    text = (tmp_path / "funding/UNIUSDT.csv").read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == "timestamp;funding_rate"
    assert lines[1] < lines[2]
