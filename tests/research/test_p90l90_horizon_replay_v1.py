"""Offline chronology checks; full server run must additionally prove exact uncapped ledger parity."""
import importlib.util
import sys
import types
import unittest
from types import SimpleNamespace
from pathlib import Path

for module in ('scripts', 'scripts.research'):
    sys.modules[module] = types.ModuleType(module)
base = types.ModuleType('scripts.research.p90l90_exit_90d_v2')
sys.modules[base.__name__] = base
entry = types.ModuleType('scripts.research.p90l90_entry_90d_v2')
entry.START_TS = 1779062400
entry.END_TS = 1786838400
entry.RAW_DEFAULT = Path('/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/inputs')
entry.FROZEN_DEFAULT = Path('/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/l53_clean_v2/parts')
entry.OUTPUT_ROOT = Path('/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results')
entry.SYMBOLS = ('LTCUSDT', 'DOTUSDT', 'ARBUSDT', 'APTUSDT', 'INJUSDT')
entry.load_frozen = lambda _: None
entry.sha256 = lambda _: 'mock'
sys.modules[entry.__name__] = entry
source = Path(__file__).parents[2] / 'scripts' / 'research' / 'p90l90_horizon_replay_v1.py'
spec = importlib.util.spec_from_file_location('horizon', source)
horizon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(horizon)

class TimeGateTests(unittest.TestCase):
    def setUp(self):
        self.pos = SimpleNamespace(entry_seq=100,entry_ts=1710000000.)
    def test_first_tick_after_cutoff(self):
        self.assertTrue(horizon.is_timeout(3,self.pos,101,self.pos.entry_ts+10800))
        self.assertTrue(horizon.is_timeout(48,self.pos,102,self.pos.entry_ts+172801))
    def test_tick_before_cutoff(self):
        self.assertFalse(horizon.is_timeout(3,self.pos,101,self.pos.entry_ts+10799.9))
        self.assertFalse(horizon.is_timeout(48,self.pos,102,self.pos.entry_ts+86400))
    def test_no_cap(self):
        self.assertFalse(horizon.is_timeout(None,self.pos,101,self.pos.entry_ts+3600*1000))
    def test_same_record_cannot_timeout(self):
        self.assertFalse(horizon.is_timeout(3,self.pos,100,self.pos.entry_ts+3600*4))
    def test_exact_horizons(self):
        self.assertEqual(horizon.HORIZONS,(None,3,6,12,18,24,36,48))
        self.assertEqual(horizon.KEYS,('NO_CAP','H03','H06','H12','H18','H24','H36','H48'))

if __name__=='__main__':
    unittest.main()
