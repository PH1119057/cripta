#!/usr/bin/env python3
"""Bounded research-only queue for 90d holding-time counterfactual.

ACTOR: cripta UID107; no sudo/root, DB or systemd.
READ: /srv/cripta/source_checkout (published source) and original P90L90 data.
WRITE/CREATE: only new leaf results/p90l90_horizon_controller_20261025
including source_snapshot, plus new results/horizon_90d_{DOT,ARB,APT,INJ}_v1.
RENAME: own atomic run_manifest only. No deletes outside temporary files.
No services, exchange, LIVE, runtime or original research modifications.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024')
REPO = Path('/srv/cripta/source_checkout')
SYMS = ('LTC', 'DOT', 'ARB', 'APT', 'INJ')
SUBDIR = 'p90l90_horizon_controller_20261025'
HORIZONS = ('NO_CAP', 'H03', 'H06', 'H12', 'H18', 'H24', 'H36', 'H48')
lock = threading.Lock()


def iso():
    return datetime.now(timezone.utc).isoformat()


def preflight(expected_head):
    if os.geteuid() != 107:
        raise PermissionError('only cripta UID107')
    source_commit = subprocess.check_output(
        ['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True
    ).strip()
    if source_commit != expected_head:
        raise ValueError('server source != expected published main')
    r = ROOT / 'results'
    if r.stat().st_uid != 107 or not os.access(r, os.W_OK | os.X_OK):
        raise PermissionError('research parent ownership/writability')
    if (r / SUBDIR).exists():
        raise FileExistsError('controller output already exists')
    for symbol in SYMS[1:]:
        if (r / f'horizon_90d_{symbol}_v1').exists():
            raise FileExistsError('prior output exists ' + symbol)
        for typ in ('entry', 'exit'):
            if not (r / f'{typ}_{symbol}_FULL90').is_dir():
                raise FileNotFoundError('original v2 missing ' + symbol + ' ' + typ)
    ltc = r / 'horizon_90d_LTC_v1' / 'result.json'
    if not ltc.is_file():
        raise FileNotFoundError('LTC full-data control replay missing')
    lc = json.loads(ltc.read_text())
    if not lc.get('baseline_trade_by_trade_parity') or set(lc['scenarios']) != set(HORIZONS):
        raise ValueError('LTC control parity or horizon coverage failed')
    for name in (
        'p90l90_horizon_queue_v1.py',
        'p90l90_horizon_replay_v1.py',
        'p90l90_exit_90d_v2.py',
        'p90l90_entry_90d_v2.py',
    ):
        if not (REPO / 'scripts' / 'research' / name).is_file():
            raise FileNotFoundError(name)
    free = os.statvfs(str(r))
    if free.f_bavail * free.f_frsize < 2_000_000_000:
        raise OSError('less than 2GB research disk headroom')
    return source_commit


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--expected-head', required=True)
    args = p.parse_args()
    head = preflight(args.expected_head)
    out = ROOT / 'results'
    dest = out / SUBDIR
    dest.mkdir(mode=0o770)
    snapshot = dest / 'source_snapshot' / 'scripts' / 'research'
    snapshot.mkdir(parents=True, mode=0o770)
    source_hashes = {}
    for fname in (
        'p90l90_horizon_queue_v1.py',
        'p90l90_horizon_replay_v1.py',
        'p90l90_exit_90d_v2.py',
        'p90l90_entry_90d_v2.py',
    ):
        src = REPO / 'scripts' / 'research' / fname
        target = snapshot / fname
        shutil.copyfile(src, target)
        source_hashes[fname] = hashlib.sha256(target.read_bytes()).hexdigest()
    ltc_data = json.loads((out / 'horizon_90d_LTC_v1' / 'result.json').read_text())
    state = {
        'study': 'P90L90_FORCE_HORIZON_90D_V1',
        'status': 'RUNNING',
        'actor_uid': 107,
        'source_commit': head,
        'source_snapshot_sha256': source_hashes,
        'created_utc': iso(),
        'max_concurrent_heavy_jobs': 2,
        'baseline_parity_required': True,
        'tasks': {s: {'state': 'QUEUED'} for s in SYMS},
    }
    state['tasks']['LTC'] = {
        'state': 'SUCCEEDED_EXTERNAL_SMOKE',
        'output': str(out / 'horizon_90d_LTC_v1'),
        'baseline_parity': True,
        'net_usdt': {
            k: v['money']['net_usdt'] for k, v in ltc_data['scenarios'].items()
        },
    }

    def persist():
        fd, tmp = tempfile.mkstemp(dir=dest, prefix='.partial_', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest / 'run_manifest.json')
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    persist()

    def worker(sym):
        with lock:
            state['tasks'][sym]['state'] = 'RUNNING'
            state['tasks'][sym]['started_utc'] = iso()
            persist()
        output = out / f'horizon_90d_{sym}_v1'
        cmd = [
            sys.executable, '-B', '-m',
            'scripts.research.p90l90_horizon_replay_v1',
            '--symbol', sym + 'USDT',
            '--entry-output', str(out / f'entry_{sym}_FULL90'),
            '--original-exit', str(out / f'exit_{sym}_FULL90'),
            '--output', str(output),
        ]
        try:
            logfile = dest / f'{sym}_worker.log'
            with logfile.open('x', encoding='utf8') as f:
                run = subprocess.run(
                    cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT,
                    timeout=1600, check=False,
                )
            if run.returncode:
                raise RuntimeError('worker exit ' + str(run.returncode))
            m = json.loads((output / 'result.json').read_text())
            if not m.get('baseline_trade_by_trade_parity'):
                raise ValueError('NO_CAP baseline parity failed')
            if set(m['scenarios']) != set(HORIZONS):
                raise ValueError('incomplete time-horizon cohort')
            from scripts.research.p90l90_entry_90d_v2 import sha256
            if sha256(output / 'trade_ledger.jsonl') != m['trade_ledger_sha256']:
                raise ValueError('new ledger checksum invalid')
            for key, rec in m['scenarios'].items():
                c = rec['counts']
                if c['accepted'] != c['closed'] + c['censored_open']:
                    raise ValueError('accepted accounting mismatch ' + key)
            with lock:
                state['tasks'][sym] = {
                    'state': 'SUCCEEDED',
                    'finished_utc': iso(),
                    'output': str(output),
                    'log': str(logfile),
                    'baseline_parity': True,
                    'net_usdt': {
                        k: v['money']['net_usdt'] for k, v in m['scenarios'].items()
                    },
                }
                persist()
            return True
        except BaseException as err:
            with lock:
                state['tasks'][sym] = {
                    'state': 'FAILED',
                    'finished_utc': iso(),
                    'error_type': type(err).__name__,
                    'error': str(err)[:500],
                }
                state['status'] = 'PARTIAL_FAILED'
                persist()
            return False

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='p90h') as pool:
        futures = [pool.submit(worker, sym) for sym in SYMS[1:]]
        statuses = [x.result() for x in as_completed(futures)]
    with lock:
        state['status'] = 'COMPLETE' if all(statuses) else 'PARTIAL_FAILED'
        state['finished_utc'] = iso()
        persist()
    print(json.dumps({
        'status': state['status'],
        'manifest': str(dest / 'run_manifest.json'),
        'tasks': {s: t['state'] for s, t in state['tasks'].items()},
    }), flush=True)
    if state['status'] != 'COMPLETE':
        raise SystemExit(3)


if __name__ == '__main__':
    main()
