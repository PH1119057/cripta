"""Portal health and readiness progress must not drive trading preparation."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.dashboard import app


def test_timeout_retains_the_exact_last_readiness_blocker(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(app.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(app.time, 'sleep', lambda seconds: setattr(clock, 'now', clock.now+seconds))
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    monkeypatch.setattr(app.psycopg, 'connect', lambda *_a, **_kw: DB())
    def blocked(*_args, **_kw):
        raise app.P90PrearmEvidenceBlocked('MAIN_EXACT_PROTECTION_BLOCKER')
    monkeypatch.setattr(app, 'prepare_p90l90_real_v3_prearm_evidence', blocked)
    with pytest.raises(app.P90PrearmEvidenceBlocked, match='MAIN_EXACT_PROTECTION_BLOCKER'):
        app._wait_p90_real_v3_technical_evidence(timeout_seconds=1)


def test_online_does_not_depend_on_wallet_or_arm_progress():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required for browser state regression')
    html = Path('operations/dashboard/index.html').read_text(encoding='utf-8')
    fn = next(line for line in html.splitlines()
              if line.startswith('function updateOperatorConnection()'))
    code = """
    let lastPortalHeartbeatAt=999000, lastWalletRefreshMs=1, lastLiveSuccessAt=1000000;
    let gateChanging=true, operatorConnection={},liveFreshness={};
    Date.now=()=>1000000;
    """ + fn + """
    updateOperatorConnection();
    const fresh={...operatorConnection,detail:liveFreshness.textContent};
    lastPortalHeartbeatAt=1;lastWalletRefreshMs=1000000;
    updateOperatorConnection();
    console.log(JSON.stringify({fresh,offline:operatorConnection.textContent}));
    """
    result = subprocess.run([node, '-e', code], check=True, capture_output=True, text=True)
    state = json.loads(result.stdout)
    assert state['fresh']['textContent'] == 'ONLINE'
    assert state['fresh']['detail'] == 'Данные аккаунта устарели'
    assert state['offline'] == 'OFFLINE'


def test_progress_get_reads_durable_operation_without_preparation(monkeypatch):
    calls = []
    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    def connect(dsn):
        assert 'default_transaction_read_only=on' in dsn
        return DB()
    monkeypatch.setattr(app.psycopg, 'connect', connect)
    monkeypatch.setattr(app, 'latest_real_readiness_operation',
                        lambda *_a, **_k: {'request_id': 'same-request', 'state': 'VALIDATING'})
    monkeypatch.setattr(app, '_u6_json', lambda _h, status, payload: calls.append(payload))
    handler = object.__new__(app.Handler)
    handler.path = '/api/p90l90/real-v3/readiness-status'
    handler.require_login = lambda: False
    app.Handler.do_GET(handler)
    app.Handler.do_GET(handler)
    assert [x['readiness_operation']['request_id'] for x in calls] == ['same-request']*2

