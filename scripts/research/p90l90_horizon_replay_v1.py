#!/usr/bin/env python3
"""Research-only P90L90 90d PREV7D/TRAIL035 forced-exit horizon replay.

EXECUTION / FILESYSTEM CONTRACT
EFFECTIVE_ACTOR: cripta (UID 107), never root.
READ_PATHS: /data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/{inputs,results/l53_clean_v2,results/entry_*_FULL90,results/exit_*_FULL90,source_snapshot}.
READ_CODE: exact GitHub-main-published p90l90_entry_90d_v2.py and p90l90_exit_90d_v2.py, unchanged; their imports from scripts/research.
CREATE_CHILD_PARENTS: only preexisting /data/.../results; output leaf dir specified with --output, created once by cripta; output files created inside.
WRITE_PATHS: only --output/{trade_ledger.jsonl,result.json} under research results, no other files; no rename/delete.
EXPECTED_OWNER/GROUP/MODE: inherited cripta:cripta and parent mode; preflight from exact actor verifies traversal/writable parent.
SYSTEMD_SANDBOX: none; interactive bounded research job.
DB_ROLE: none. No trading runtime or exchange access.

Experimental extension, not replacement for canonical uncapped P90L90 V2.
At first observed raw tick t>=entry_ts + H*3600 and after entry source order,
close at that actual tick using taker fee. Other stop/resting TP rules identical
until that tick. Same-record re-entry remains blocked. No interpolation.
For baseline H=None all financial ledger records must match frozen v2 exactly.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path
from statistics import median

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research.p90l90_entry_90d_v2 import (
    START_TS,END_TS,RAW_DEFAULT,FROZEN_DEFAULT,OUTPUT_ROOT,SYMBOLS,load_frozen,sha256,
)

COHORT='PREV7D'
KIND='TRAIL035'
HORIZONS=(None,3,6,12,18,24,36,48)
KEYS=tuple('NO_CAP' if x is None else f'H{x:02d}' for x in HORIZONS)
EXPERIMENT='P90L90_PREV7D_TRAIL035_FORCE_HORIZONS_V1'
ROOT=Path('/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024')


def require_output(out:Path)->None:
    if os.geteuid()!=107:raise PermissionError('research actor must be cripta uid=107')
    if not out.is_absolute() or out.exists():raise ValueError('absolute unique output required')
    parent=out.parent
    if parent.resolve()!=ROOT/'results':raise ValueError('no writes except approved results root')
    if not parent.is_dir() or parent.stat().st_uid!=107 or not os.access(parent,os.W_OK|os.X_OK):
        raise PermissionError('result parent inaccessible by exact research actor')
    if not (ROOT/'source_snapshot/scripts/research/p90l90_exit_90d_v2.py').is_file():
        raise FileNotFoundError('frozen imported source snapshot missing')


def is_timeout(horizon_hours:int|None, pos, source_seq:int, tick_ts:float)->bool:
    return (horizon_hours is not None and source_seq>pos.entry_seq and
            tick_ts>=pos.entry_ts+horizon_hours*3600)


def read_original(path:Path)->list:
    if not path.is_file():raise FileNotFoundError(path)
    ledger=[]
    with path.open(encoding='utf8') as fd:
        for line in fd:
            row=json.loads(line)
            if row['scenario']==f'{COHORT}_{KIND}':ledger.append(row)
    return ledger


def check_manifest(symbol:str,candidates_root:Path,frozen_root:Path)->tuple:
    entry=json.loads((candidates_root/'manifest.json').read_text())
    if entry['research']!='P90L90_ENTRY_90D_V2' or entry['symbol']!=symbol or not entry['complete_90d_input']:
        raise ValueError('invalid frozen entry lineage or period')
    if entry['frozen_l53']['states']!=25920:raise ValueError('90d state mismatch')
    path=candidates_root/f'candidates_{symbol}.jsonl'
    if sha256(path)!=entry['events_sha256']:raise ValueError('candidate hash mismatch')
    statepath=frozen_root/f'states_{symbol}.csv.gz'
    if sha256(statepath)!=entry['frozen_l53_sha256']:raise ValueError('state hash mismatch')
    states,_,median7,sm=load_frozen(statepath)
    if len(median7)!=90:raise ValueError('causal median coverage mismatch')
    events,counts=base.read_events(path,symbol)
    return entry,states,events,counts


def run(symbol:str,raw_root:Path,frozen_root:Path,candidates_root:Path,original_root:Path,output:Path)->dict:
    require_output(output)
    if symbol not in SYMBOLS:raise ValueError('symbol not part of P90L90 90d set')
    entry,states,events,event_counts=check_manifest(symbol,candidates_root,frozen_root)
    old=json.loads((original_root/'result.json').read_text())
    original_ledger_path=original_root/f'trade_ledger_{symbol}.jsonl'
    if sha256(original_ledger_path)!=old['trade_ledger_sha256']:
        raise ValueError('existing v2 reference ledger changed')
    oldrows=read_original(original_ledger_path)
    oldstat=old['scenarios'][f'{COHORT}_{KIND}']
    held={key:None for key in KEYS}
    metrics={key:Counter() for key in KEYS}
    money={key:defaultdict(lambda:D(0)) for key in KEYS}
    direction={key:{s:Counter() for s in ('LONG','SHORT')} for key in KEYS}
    trade_rows={key:[] for key in KEYS}
    prices_read=0
    last_t=None
    last_px=None
    progress_t=time.monotonic()
    for f in entry['inputs']:
        file_path=raw_root/symbol/'public_trades'/f['file']
        if sha256(file_path)!=f['sha256']:raise ValueError('raw source SHA mismatch '+str(file_path))
        count=0
        with gzip.open(file_path,'rt',newline='') as stream:
            for row in csv.DictReader(stream):
                prices_read+=1;count+=1
                t=float(row['timestamp']);px=D(row['price']);bucket=int(t//300)*300
                if last_t is not None and t<last_t:raise ValueError('input ordering violation')
                last_t=t;last_px=px
                if not START_TS<=t<END_TS:continue
                incoming=events.get(prices_read,())
                for e in incoming:
                    if float(e['ts'])!=t or D(e['price'])!=px or e['trade_id']!=row['trdMatchID']:
                        raise ValueError('entry event no longer matches raw tick')
                if not incoming and not any(held.values()):continue
                for horizon,key in zip(HORIZONS,KEYS):
                    position=held[key]
                    ended_this_record=False
                    if position is not None:
                        reason=None;exit_px=None;maker=False
                        if is_timeout(horizon,position,prices_read,t):
                            reason,exit_px,maker=('TIME_LIMIT',px,False)
                            held[key]=None
                        else:
                            st=states.get(bucket)
                            tp=None if st is None else (st[1] if position.side=='LONG' else st[0])
                            held[key],out=base.on_trade(position,px,prices_read,bucket,tp,KIND)
                            if out is not None:reason,exit_px,maker=out
                        if reason is not None:
                            gross,entry_fee,exit_fee,net=base.gross_and_net(position,exit_px,maker)
                            rec=base.trade_record(symbol,COHORT,KIND,position,prices_read,t,
                                     exit_px,reason,maker,gross,entry_fee,exit_fee,net)
                            rec['scenario']=f'{COHORT}_{KIND}_{key}'
                            trade_rows[key].append(rec)
                            m=metrics[key];dm=direction[key][position.side]
                            for met in (m,dm):
                                met['closed']+=1;met['close_'+reason]+=1
                                met['win']+=net>0;met['loss']+=net<0;met['flat']+=net==0
                            for k,v in (('gross_usdt',gross),('entry_fees_usdt',entry_fee),
                              ('exit_fees_usdt',exit_fee),('net_usdt',net),
                              ('positive_net_usdt',max(net,D(0))),('negative_net_usdt',min(net,D(0)))):
                                money[key][k]+=v
                            ended_this_record=True
                    for e in incoming:
                        if not e[COHORT]:continue
                        m=metrics[key];dm=direction[key][e['side']]
                        m['qualified_candidate']+=1;dm['qualified_candidate']+=1
                        if held[key] is not None or ended_this_record:
                            m['busy_or_same_tick']+=1;dm['busy_or_same_tick']+=1
                            continue
                        p=base.initial_position(e,prices_read,bucket,COHORT,KIND)
                        st=states.get(bucket)
                        if st is None:raise ValueError('missing frozen L5 at Entry')
                        tp=st[1] if p.side=='LONG' else st[0]
                        if base.take_profit_eligible(p.side,px,tp):
                            p.tp=tp;p.tp_live_after_seq=prices_read
                        held[key]=p;m['accepted']+=1;dm['accepted']+=1
        if count!=int(f['rows']):raise ValueError('input raw count changed')
        if time.monotonic()-progress_t>15:
            print(json.dumps({'symbol':symbol,'day_file':f['file'],'raw_rows':prices_read,
                              'scenario_completed':{k:metrics[k]['closed'] for k in KEYS}}),flush=True)
            progress_t=time.monotonic()
    if prices_read!=sum(int(f['rows']) for f in entry['inputs']):raise ValueError('raw row count total mismatch')
    if len(trade_rows['NO_CAP'])!=len(oldrows):raise ValueError('uncapped baseline count mismatch')
    for nr,oldr in zip(trade_rows['NO_CAP'],oldrows):
        nr2=dict(nr);nr2['scenario']=f'{COHORT}_{KIND}'
        if nr2!=oldr:raise ValueError('uncapped baseline trade-by-trade mismatch')
    if money['NO_CAP']['net_usdt']!=D(oldstat['money']['net_usdt']):
        raise ValueError('uncapped baseline money mismatch')
    data={'research':EXPERIMENT,'symbol':symbol,'status':'HISTORICAL_TICK_PROXY_NO_EXCHANGE_FILL',
          'period':['2026-05-18T00:00:00Z','2026-08-16T00:00:00Z'],
          'input_entry_events_sha256':entry['events_sha256'],
          'input_v2_exit_ledger_sha256':old['trade_ledger_sha256'],
          'baseline_trade_by_trade_parity':True,
          'execution':'FIRST_RAW_TICK_AT_OR_AFTER_ENTRY_AGE;TIMEOUT_MARKET_TAKER_PRIORITY;SAME_TICK_REENTRY_BLOCKED',
          'unmodeled':['spread','slippage','latency','funding'],'notional_usdt':'10',
          'horizons_hours':[None,3,6,12,18,24,36,48],
          'source_rows':prices_read,'scenarios':{}}
    for key in KEYS:
        cur=held[key];m=metrics[key];m['censored_open']=int(cur is not None)
        if cur is not None:direction[key][cur.side]['censored_open']+=1
        if m['accepted']!=m['closed']+m['censored_open']:raise ValueError('accepted not reconciled '+key)
        if m['qualified_candidate']!=m['accepted']+m['busy_or_same_tick']:
            raise ValueError('candidates not reconciled '+key)
        mon=money[key]
        if mon['net_usdt']!=mon['gross_usdt']-mon['entry_fees_usdt']-mon['exit_fees_usdt']:
            raise ValueError('money mismatch '+key)
        if mon['net_usdt']!=mon['positive_net_usdt']+mon['negative_net_usdt']:
            raise ValueError('money split mismatch '+key)
        holds=[D(t['holding_seconds']) for t in trade_rows[key]]
        data['scenarios'][key]={
          'counts':dict(m),
          'by_side_counts':{k:dict(v) for k,v in direction[key].items()},
          'money':{k:str(v) for k,v in mon.items()},
          'holding_hours_median':str(median(holds)/D(3600)) if holds else None,
          'holding_hours_mean':str(sum(holds,D(0))/D(len(holds))/D(3600)) if holds else None,
          'holding_hours_max':str(max(holds)/D(3600)) if holds else None,
          'slot_hours_closed':str(sum(holds,D(0))/D(3600)),
          'censored_position':None if cur is None else {
             'side':cur.side,'entry_ts':repr(cur.entry_ts),'entry_event_id':cur.entry_event_id},
        }
    output.mkdir(mode=0o770)
    ledger=output/'trade_ledger.jsonl'
    with ledger.open('x',encoding='utf8') as out:
        for key in KEYS:
            for rec in trade_rows[key]:out.write(json.dumps(rec,sort_keys=True,separators=(',',':'))+'\n')
    data['trade_ledger_sha256']=sha256(ledger)
    data['trade_ledger_rows']=sum(len(v) for v in trade_rows.values())
    with (output/'result.json').open('x',encoding='utf8') as out:
        json.dump(data,out,indent=2,sort_keys=True);out.write('\n')
    return data


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument('--symbol',choices=SYMBOLS,required=True)
    p.add_argument('--raw-root',type=Path,default=RAW_DEFAULT)
    p.add_argument('--frozen-root',type=Path,default=FROZEN_DEFAULT)
    p.add_argument('--entry-output',type=Path,required=True)
    p.add_argument('--original-exit',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    data=run(args.symbol,args.raw_root,args.frozen_root,args.entry_output,args.original_exit,args.output)
    print(json.dumps({'status':data['status'],'symbol':data['symbol'],'baseline_parity':data['baseline_trade_by_trade_parity'],
                      'scenarios':{k:{'counts':v['counts'],'money':v['money']} for k,v in data['scenarios'].items()}},sort_keys=True),flush=True)

if __name__=='__main__':main()