"""GET-only, synthetic Exchange payload tests. Not a real Bybit/PG integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from bybit_workbench.exchange.bybit.account_position_inventory import complete_linear_positions


def row(symbol="UNIUSDT", index=0, size="2"):
    return {"symbol": symbol, "positionIdx": index, "size": size, "side": "Buy"}


def page(rows, cursor="", code=0):
    return {"retCode": code, "result": {"list": rows, "nextPageCursor": cursor}}


class Reader:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, endpoint: str, params: Mapping[str, object], *, authenticated: bool):
        self.calls.append((endpoint, dict(params), authenticated))
        return self.responses.pop(0)


def run(responses, max_pages=100):
    r = Reader(responses)
    return asyncio.run(complete_linear_positions(r, max_pages=max_pages)), r


def test_claimless_symbol_is_observed_without_arm_or_strategy_scope():
    rows, r = run([page([row("LTCUSDT"), row("TRXUSDT")])])
    assert {x["symbol"] for x in rows} == {"LTCUSDT", "TRXUSDT"}
    assert r.calls == [
        ("/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": "200"}, True)
    ]


def test_all_pages_exhausted_and_cursor_forwarded():
    rows, r = run([page([row("UNIUSDT")], "p2"), page([row("BTCUSDT")])])
    assert len(rows) == 2
    assert r.calls[-1][1]["cursor"] == "p2"


def test_empty_account_is_valid_only_after_authenticated_success():
    rows, r = run([page([])])
    assert rows == [] and len(r.calls) == 1


@pytest.mark.parametrize(
    "responses",
    [
        [page([], "p2"), page([], "p2")],
        [page([], "p2"), page([], "p3"), page([], "p2")],
        [page([row("UNIUSDT")], "p2"), page([row("UNIUSDT")])],
        [page([row(size="NaN")])],
        [page([row(size="Infinity")])],
        [page([row(size="-1")])],
        [page([row(size="oops")])],
        [page([row(index="0")])],
        [page([row(index=True)])],
        [page([row(symbol="")])],
        [page(["x"])],
        [{"retCode": 0, "result": {}}],
        [{"retCode": 10001, "result": {"list": []}}],
        [page([], cursor=3)],
    ],
)
def test_malformed_or_incomplete_input_fails_closed(responses):
    with pytest.raises(RuntimeError):
        run(responses)


def test_bounded_page_limit_rejects_truncation():
    with pytest.raises(RuntimeError, match="incomplete"):
        run([page([row()], "more")], max_pages=1)


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_unapproved_limits_block(limit):
    with pytest.raises(ValueError):
        run([page([])], max_pages=limit)


def test_no_mutating_endpoint_or_unauthenticated_read():
    _, r = run([page([])])
    assert all(x[0] == "/v5/position/list" and x[2] is True for x in r.calls)
