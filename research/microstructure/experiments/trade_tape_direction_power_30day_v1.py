#!/usr/bin/env python3
"""CRIPTA: read-only historical long/short direction and move power diagnostics.
No decision-time receipt clock in archive, no trading action, no PnL.
"""
import argparse,csv,datetime as dt,gzip,hashlib,json,math,os,pwd,statistics,time
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
IDS=(1,2,3,5,6,7)
HS=(5,15,60)
FOLDS={"train":(0,20),"validation":(20,23),"late_holdout":(23,30)}
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
CUBEROOT=Path("/data/cripta/research/runs/trade_minute_cube_20261021_30d_v1")
GH=Path("/srv/cripta/source_checkout/research/microstructure")
MAN=GH/"TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
CUBEQC=GH/"TRADE_MINUTE_5COINS_30DAY_6FACTOR_CUBE_QC_2026-10-21.json"
MANBLOB="30b87fe11fb3a1bfc6fb59c8866091e92e823203"
CUBEBLOB="deeafdc1b460195f53831fd7c718d46ec8d80d52"

def blob(p):
 b=p.read_bytes()
 return hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
def filehash(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for chunk in iter(lambda:f.read(1048576),b""):h.update(chunk)
 return h.hexdigest()
def pct(n,d):
 return round(100*n/d,3) if d else None
def pctl(seq,q):
 if not seq:return None
 a=sorted(seq)
 return round(a[int(q*(len(a)-1))],4)
def metrics(items):
 n=len(items)
 if not n:return dict(n=0,distinct_days=0)
 returns=[x[0] for x in items]
 ups=[x[1] for x in items]
 downs=[x[2] for x in items]
 days=sorted(set(x[3] for x in items))
 return dict(n=n,distinct_days=len(days),
  up_pct=pct(sum(x>0 for x in returns),n),down_pct=pct(sum(x<0 for x in returns),n),
  flat_pct=pct(sum(x==0 for x in returns),n),
  endpoint_move_up_ge20_pct=pct(sum(x>=20 for x in returns),n),
  endpoint_move_down_ge20_pct=pct(sum(x<=-20 for x in returns),n),
  endpoint_move_up_ge50_pct=pct(sum(x>=50 for x in returns),n),
  endpoint_move_down_ge50_pct=pct(sum(x<=-50 for x in returns),n),
  endpoint_move_up_ge100_pct=pct(sum(x>=100 for x in returns),n),
  endpoint_move_down_ge100_pct=pct(sum(x<=-100 for x in returns),n),
  touched_up_ge20_pct=pct(sum(x>=20 for x in ups),n),
  touched_down_ge20_pct=pct(sum(x>=20 for x in downs),n),
  touched_up_ge50_pct=pct(sum(x>=50 for x in ups),n),
  touched_down_ge50_pct=pct(sum(x>=50 for x in downs),n),
  touched_up_ge100_pct=pct(sum(x>=100 for x in ups),n),
  touched_down_ge100_pct=pct(sum(x>=100 for x in downs),n),
  median_abs_endpoint_bps=pctl([abs(x) for x in returns],.5),
  p90_abs_endpoint_bps=pctl([abs(x) for x in returns],.9),
  median_max_upside_bps=pctl(ups,.5),p90_max_upside_bps=pctl(ups,.9),
  median_max_downside_bps=pctl(downs,.5),p90_max_downside_bps=pctl(downs,.9),
  median_endpoint_bps=pctl(returns,.5),
  mean_endpoint_bps=round(sum(returns)/n,4),
  daily_up_frac_gt50pct=sum(sum(x[0]>0 for x in items if x[3]==day)>sum(x[3]==day for x in items)/2 for day in days),
  daily_up_frac_lt50pct=sum(sum(x[0]>0 for x in items if x[3]==day)<sum(x[3]==day for x in items)/2 for day in days))
def compute(sym):
 if sym not in SYMS or os.geteuid()!=pwd.getpwnam("cripta").pw_uid:
  raise PermissionError("ACTOR_OR_SCOPE")
 if blob(MAN)!=MANBLOB or blob(CUBEQC)!=CUBEBLOB:
  raise ValueError("SOURCE_MANIFEST_VERSION_DRIFT")
 qc=json.loads(CUBEQC.read_text())
 rec=next(x for x in qc["by_symbol"] if x["symbol"]==sym)
 cubefile=CUBEROOT/(sym+"_6factor.csv.gz")
 if filehash(cubefile)!=rec["sha"]:raise ValueError("CUBE_SHA_MISMATCH")
 prior=GH/("FORWARD_RETURN_30DAY_SCREEN_"+sym+"_2026-10-21.json")
 previous=json.loads(prior.read_text())
 if previous["instrument_id"]!=sym or previous["source_days"]!=30:
  raise ValueError("FROZEN_THRESHOLDS_SCOPE")
 thresholds={}
 for g in previous["grid"]:
  iid=g["indicator_id"]
  pair=(g["train_threshold_low_q20"],g["train_threshold_high_q80"])
  if iid in thresholds and thresholds[iid]!=pair:
   raise ValueError("HORIZON_DEPENDENT_THRESHOLD")
  thresholds[iid]=pair
 n=30*1440
 factors={k:[None]*n for k in IDS}
 fills=[0]*n;count_cube=0
 with gzip.open(cubefile,"rt",newline="") as f:
  for line in csv.DictReader(f):
   idx=count_cube//6;fac=IDS[count_cube%6]
   if idx>=n or int(line["indicator_id"])!=fac or line["instrument_id"]!=sym:
    raise ValueError("CUBE_ROW_ORDER")
   if line["observed_at_utc"] or line["verified_available_after_utc"]:
    raise ValueError("FABRICATED_RECEIPT_CLOCK")
   if count_cube%6==0:fills[idx]=int(line["input_fill_count"])
   if line["value"]:
    v=float(line["value"])
    if not math.isfinite(v):raise ValueError("NONFINITE_FACTOR")
    factors[fac][idx]=v
   count_cube+=1
 if count_cube!=n*6:raise ValueError("CUBE_COUNT")
 source=json.loads(MAN.read_text())
 manifest={(x["symbol"],x["date_utc"]):x["compressed_sha256"] for x in source["source_files"]}
 close=[None]*n; hi=[None]*n;lo=[None]*n; observed_fills=[0]*n
 start_day=dt.date(2026,5,18)
 total=0;t0=time.monotonic()
 for k in range(30):
  day=(start_day+dt.timedelta(days=k)).isoformat()
  src=ROOT/sym/"public_trades"/(sym+day+".csv.gz")
  if filehash(src)!=manifest[(sym,day)]:raise ValueError("ARCHIVE_HASH_MISMATCH "+day)
  epoch=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
  prev=None
  with gzip.open(src,"rt",newline="") as f:
   for row in csv.DictReader(f):
    ts=Decimal(row["timestamp"])
    if prev is not None and ts<prev:raise ValueError("TS_NONMONOTONE")
    prev=ts
    m=int((ts-epoch)//60)
    if m<0 or m>=1440 or row["symbol"]!=sym:raise ValueError("RAW_DAY_OR_SYMBOL")
    ix=k*1440+m
    p=float(row["price"])
    if p<=0 or not math.isfinite(p):raise ValueError("INVALID_PRICE")
    close[ix]=p
    hi[ix]=p if hi[ix] is None else max(hi[ix],p)
    lo[ix]=p if lo[ix] is None else min(lo[ix],p)
    observed_fills[ix]+=1;total+=1
  for i in range(k*1440,(k+1)*1440):
   if fills[i]!=observed_fills[i]:raise ValueError("RAW_CUBE_MINUTE_FILL_MISMATCH "+str(i))
 if total!=previous["source_fills"]:raise ValueError("PRIOR_SOURCE_FILL_MISMATCH")
 missing=[0]*(n+1)
 for i,x in enumerate(observed_fills):
  missing[i+1]=missing[i]+int(x==0)
 label={}
 for h in HS:
  a=[None]*n
  for i in range(n-h):
   if close[i] is None or close[i+h] is None or missing[i+h+1]-missing[i+1]>0:continue
   upp=max(hi[i+1:i+h+1])
   low=min(lo[i+1:i+h+1])
   p=close[i]
   ret=10000*math.log(close[i+h]/p)
   up=max(0.,10000*math.log(upp/p))
   down=max(0.,10000*math.log(p/low))
   a[i]=(ret,up,down,i//1440)
  label[h]=a
 rows=[]
 for fac in IDS:
  lowthr,highthr=thresholds[fac]
  for h in HS:
   groups={}
   for fold,(daya,dayb) in FOLDS.items():
    start=daya*1440;end=dayb*1440-h
    buckets={"baseline":[],"high":[],"low":[]}
    for i in range(start,end):
     x=factors[fac][i];t=label[h][i]
     if x is None or t is None:continue
     buckets["baseline"].append(t)
     if x>=highthr:buckets["high"].append(t)
     if x<=lowthr:buckets["low"].append(t)
    groups[fold]={k:metrics(v) for k,v in buckets.items()}
   agree="NO_THRESHOLD_COLLISION" if lowthr<highthr else "TRAIN_BUCKET_OVERLAP"
   t=groups["train"];base=t["baseline"]
   upuplift=(t["high"].get("endpoint_move_up_ge20_pct") or 0)-(base.get("endpoint_move_up_ge20_pct") or 0)
   downuplift=(t["high"].get("endpoint_move_down_ge20_pct") or 0)-(base.get("endpoint_move_down_ge20_pct") or 0)
   lup=(t["low"].get("endpoint_move_up_ge20_pct") or 0)-(base.get("endpoint_move_up_ge20_pct") or 0)
   ldown=(t["low"].get("endpoint_move_down_ge20_pct") or 0)-(base.get("endpoint_move_down_ge20_pct") or 0)
   for bucket,incup,incdown in (("high",upuplift,downuplift),("low",lup,ldown)):
    direction="LONG" if incup>0 and incup>=incdown else ("SHORT" if incdown>0 else "NONE")
    info=[]
    for fold in ("train","validation","late_holdout"):
     m=groups[fold][bucket];b=groups[fold]["baseline"]
     key="endpoint_move_up_ge20_pct" if direction=="LONG" else "endpoint_move_down_ge20_pct"
     uplift=round(m[key]-b[key],4) if direction!="NONE" and m.get(key) is not None and b.get(key) is not None else None
     info.append({ "fold":fold,"uplift_pp":uplift,"n":m["n"],"days":m["distinct_days"]})
    valid=bool(direction!="NONE" and all(v["uplift_pp"] is not None and v["uplift_pp"]>0 for v in info) and info[-1]["n"]>=100 and info[-1]["days"]>=5 and agree=="NO_THRESHOLD_COLLISION")
    rows.append(dict(indicator_id=fac,horizon_minutes=h,bucket=bucket,
     train_threshold_low_q20=lowthr,train_threshold_high_q80=highthr,
     threshold_status=agree,direction_from_train_plusminus20_bps=direction,
     direction_fold_uplift=info,exploratory_consistency_filter_pass=valid,
     fold={fold:{key:groups[fold][key] for key in ("baseline",bucket)}
           for fold in FOLDS}))
 return dict(status="EX_POST_DIRECTIONAL_POTENTIAL_ONLY_NOT_CAUSAL_OR_TRADEABLE",
  symbol=sym,period="2026-05-18..2026-06-16 UTC",source_rows=total,
  source_sha_checked=30,cube_sha256=rec["sha"],cube_rows=count_cube,
  horizons_minutes=list(HS),factor_ids=list(IDS),rule_count=len(rows),
  direction_rules=rows,source_clock="NO_RECEIVER_TIMESTAMP",elapsed_seconds=round(time.monotonic()-t0,2),
  explanation="conditional outcome frequencies and intrahorizon print excursions; NOT executable trades, fees or PnL",
  limitations=["Train 20d / validation 3d / late holdout 7d, same source history","No observed_at/available_after proof","Returns overlap; no independence/statistical multiple testing correction","Intrahorizon print extrema not order fill simulation","Directional rules chosen only on train by relative >=20bps event uplift; not Strategy","No policy defaults or cross-coin pooling"])

if __name__=="__main__":
 ap=argparse.ArgumentParser()
 ap.add_argument("--symbol",required=True,choices=SYMS)
 args=ap.parse_args()
 print(json.dumps(compute(args.symbol),ensure_ascii=False,separators=(",",":")))
