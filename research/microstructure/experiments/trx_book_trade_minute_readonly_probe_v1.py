#!/usr/bin/env python3
"""Bybit OB200 one-day cold-minute read-only QC for TRX, research only.

No output file, no SQL, no service action and no RAW removal. Compare separate
native ZIP parsers, then combine OBSERVED minute summaries with separately
persisted and independently QC'd trade-minute facts for a size experiment.
Never call the result a lossless native trade/orderbook replay replacement.
"""
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import pathlib
import time
from decimal import Decimal

ROOT=pathlib.Path("/data/cripta/datasets/raw/20260518_20260816/TRXUSDT")
MOD=pathlib.Path("/srv/cripta/source_checkout/research/microstructure/experiments")
RUN=pathlib.Path("/data/cripta/research/runs/historical_trade_minute_mart_v1_TRXUSDT_20260518_7d_20261105")
DAY=dt.date(2026,5,18)
SYMBOL="TRXUSDT"
BOOK=ROOT/"orderbook/2026-05-18_TRXUSDT_ob200.data.zip"
TRADE=ROOT/"public_trades/TRXUSDT2026-05-18.csv.gz"
TRADE_MART=RUN/"results/TRXUSDT_2026-05-18_trade_minutes.jsonl.gz"
SHA_BOOK="1bec3d82ec201563d48e1b801e4aae166638d6a1ca649ef753642045bfac52d0"
SHA_TRADE="231a61877d0ddccfe62d191d0f1d98ec6c9387c2df6d24fc545d3a78bf2f03ff"
SHA_MART="9553d71be4b6fe334212e4c58da0fde97ea64fb9fb9b8a9eb380ff4b2edd5d1e"
FILES={
 "book":"r158_firstmonth_book9_11_29_utcclip_v2.py",
 "book_qc":"r158_firstmonth_book9_11_29_utcclip_independent_qc_v2.py",
 "decrease":"r158_firstmonth_book12_visible_decrease_v1.py",
 "decrease_qc":"r158_firstmonth_book12_visible_decrease_independent_qc_v1.py",
}
BOOK_FIELDS=("factor_9_queue_imbalance_near5bp","factor_11_bid_depth_usdt_near5bp","factor_11_ask_depth_usdt_near5bp","factor_29_spread_bps","best_bid_quote","best_ask_quote","quality","last_book_event_timestamp_ms","book_event_age_to_derived_end_ms")
DEC_FIELDS=(("book_messages","messages"),("snapshots","snapshots"),("deltas","deltas"),("bid_decrease_events","bid_count"),("ask_decrease_events","ask_count"),("bid_visible_decrease_usdt","bid_quote"),("ask_visible_decrease_usdt","ask_quote"))
def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for x in iter(lambda:f.read(1024*1024),b""):h.update(x)
 return h.hexdigest()
def load(name):
 path=MOD/name
 spec=importlib.util.spec_from_file_location("probe_"+name.replace(".","_"),path)
 m=importlib.util.module_from_spec(spec)
 spec.loader.exec_module(m)
 return m
def main():
 start=time.monotonic()
 if sha(BOOK)!=SHA_BOOK or sha(TRADE)!=SHA_TRADE or sha(TRADE_MART)!=SHA_MART:
  raise ValueError("PREFLIGHT_IMMUTABLE_PROVENANCE_MISMATCH")
 m={k:load(v) for k,v in FILES.items()}
 books,meta=m["book"].day_rows(SYMBOL,DAY,BOOK)
 decreases,decrease_meta=m["decrease"].snapshots_and_deltas(DAY,BOOK,SYMBOL)
 states,nmsgs,nsnaps,aux=m["book_qc"].source_minute_states(SYMBOL,DAY,BOOK)
 independent,totals,suffix=m["decrease_qc"].independently_measure(DAY,BOOK,SYMBOL)
 if len(books)!=len(decreases)!=1440:
  raise ValueError("MISSING_PRIMARY_DAY")
 if not (len(books)==len(decreases)==len(states)==len(independent)==1440):
  raise ValueError("MINUTE_COVERAGE_MISMATCH")
 if meta["messages"]!=decrease_meta["messages"] or meta["messages"]!=nmsgs or totals["messages"]!=nmsgs:
  raise ValueError("MESSAGE_COUNT_DISAGREEMENT")
 if meta["snapshots"]!=decrease_meta["snapshots"] or nsnaps!=meta["snapshots"] or totals["snapshots"]!=nsnaps:
  raise ValueError("SNAPSHOT_COUNT_DISAGREEMENT")
 start_ms=int(dt.datetime.combine(DAY,dt.time(),dt.UTC).timestamp()*1000)
 counts={"book":0,"decrease":0}
 for i, (row,state) in enumerate(zip(books,states,strict=True)):
  exp=m["book_qc"].expected_from_state(state,start_ms,i)
  for k in BOOK_FIELDS:
   a=row[k];b=exp[k]
   if k in ("quality","last_book_event_timestamp_ms","book_event_age_to_derived_end_ms"):
    good=a==b
   else:
    good=(a is None and b is None) or (a is not None and b is not None and Decimal(str(a))==b)
   if not good:raise ValueError(f"BOOK_MISMATCH_{i}_{k}")
   counts["book"]+=1
 for i,(row,ref) in enumerate(zip(decreases,independent,strict=True)):
  for k,j in DEC_FIELDS:
   if Decimal(str(row[k]))!=Decimal(str(ref[j])):
    raise ValueError(f"DECREASE_MISMATCH_{i}_{k}")
   counts["decrease"]+=1
 if counts!={"book":12960,"decrease":10080}:raise ValueError("QC_COUNT_CHANGED")
 with gzip.open(TRADE_MART,"rt",encoding="utf-8") as f:
  trade_minutes=[json.loads(x) for x in f]
 if len(trade_minutes)!=1440:raise ValueError("TRADE_MART_NOT_1440")
 out=[]
 for i,(t,b,d) in enumerate(zip(trade_minutes,books,decreases,strict=True)):
  item={"observed_minute_utc":t["observed_minute_utc"],
   "source_identity_status":"EXTERNAL_BYBIT_LINEAR_UNBOUND",
   "available_after_utc":t["derived_available_after_utc"],
   "native_receiver_clock":None,
   "trade":t,
   "book":{k:(str(v) if isinstance(v,Decimal) else v) for k,v in b.items() if k in BOOK_FIELDS or k in ("book_levels_bid","book_levels_ask")},
   "decrease":{k:(str(v) if isinstance(v,Decimal) else v) for k,v in d.items() if k in (x[0] for x in DEC_FIELDS)}}
  out.append(item)
 encoded="\n".join(json.dumps(x,separators=(",",":"),sort_keys=True) for x in out).encode()
 packed=gzip.compress(encoded,compresslevel=6,mtime=0)
 print(json.dumps({
  "status":"TRX_1DAY_COMBINED_TRADE_BOOK_READ_ONLY_INDEPENDENT_QC_PASS",
  "scope":"TRXUSDT 2026-05-18 UTC",
  "native_trade_source_bytes":TRADE.stat().st_size,
  "native_ob200_source_bytes":BOOK.stat().st_size,
  "compressed_input_bytes":TRADE.stat().st_size+BOOK.stat().st_size,
  "combined_in_memory_mart_bytes":len(packed),
  "combined_ratio":round((TRADE.stat().st_size+BOOK.stat().st_size)/len(packed),2),
  "hypothetical_output_sha256":hashlib.sha256(packed).hexdigest(),
  "minutes":len(out),
  "book_native_messages":meta["messages"],
  "book_snapshots":meta["snapshots"],
  "numeric_independent_checks":counts,
  "mismatches":0,
  "source_script_hashes":{k:sha(MOD/v) for k,v in FILES.items()},
  "elapsed_seconds":round(time.monotonic()-start,2),
  "raw_purge_allowed":False,
  "filesystem_writes":0,
  "native_trade_order_retained":False,
  "individual_ob200_deltas_retained":False,
  "book_source_continuity":"UNKNOWN",
  "receiver_timestamp":"UNKNOWN",
  "full_R8_feature_coverage":"NOT_VERIFIED"
 },ensure_ascii=False))
if __name__=="__main__":
 main()
