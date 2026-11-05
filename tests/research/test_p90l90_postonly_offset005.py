"""Isolated regression checks of the research source; no raw dataset needed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECK = """
from decimal import Decimal as D
from scripts.research import p90l90_postonly_offset005_v1 as research
from scripts.research import p90l90_exit_90d_v2 as base

def event(side='LONG'):
    return {'PREV7D': True, 'side': side, 'price': '100',
            'ts': '1000', 'event_id': 'test', 'trade_id': 'a'}

state=(D('98'), D('102'))
book = research.PassiveBook(1800)
book.tick('LTCUSDT',1000.,1,D('100'),'a',(event(),),state)
assert book.orders['LTCUSDT']['limit']==D('99.95')
assert book.extra['proxy_filled']==0
book.tick('LTCUSDT',1001.,2,D('99.96'),'b',(),state)
assert book.extra['proxy_filled']==0
book.tick('LTCUSDT',1002.,3,D('99.94'),'c',(),state)
assert book.extra['proxy_filled']==1
assert book.pos['LTCUSDT'].entry==D('99.95')
assert book.pos['LTCUSDT'].entry_trade_id=='c'

short = research.PassiveBook(None)
short.tick('LTCUSDT',1000.,1,D('100'),'a',(event('SHORT'),),state)
assert short.orders['LTCUSDT']['limit']==D('100.05')
short.tick('LTCUSDT',1001.,2,D('100.06'),'b',(),state)
assert short.extra['proxy_filled']==1

expiry = research.PassiveBook(None)
expiry.tick('LTCUSDT',1000.,1,D('100'),'a',(event(),),state)
expiry.tick('LTCUSDT',1200.,2,D('99.93'),'b',(),state)
assert expiry.extra['expired']==1
assert expiry.extra['proxy_filled']==0

a = base.initial_position(event(),1,900,'PREV7D','TRAIL035')
b = base.initial_position(event(),1,900,'PREV7D','TRAIL035')
a.maker_entry_proxy=True
b.maker_entry_proxy=False
maker=research._money(a,D('100.5'),False)
taker=research._money(b,D('100.5'),False)
assert maker[1]==D('0.002000000000')
assert taker[1]==D('0.005500000000')
assert maker[3]-taker[3]==D('0.003500000000')
print('POSTONLY_SMALL_E2E_PASS')
"""


def test_passive_entry_and_fee_in_separate_interpreter():
    # Other legacy test modules install import-time sys.modules stubs for the
    # historical research namespace; avoid shared interpreter contamination.
    result = subprocess.run(
        [sys.executable, "-c", CHECK], cwd=ROOT,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "POSTONLY_SMALL_E2E_PASS" in result.stdout
