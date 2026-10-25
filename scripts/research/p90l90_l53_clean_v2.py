"""Reproducible P90L90 L5-3 CLEAN v2; adapted from audited 2026-10-03 v1 source.
Original SHA256 7bd1a838804d1d15fb214f438696ee08cbf127d11d1906d682885ba3d55b8b5e.
Full May08..Aug15 raw source and explicit 5m gap fail-closed; no trading orders.
"""
import argparse,csv,gzip,hashlib,json,os,sqlite3,sys,time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec=40
DEFAULT_SYMBOLS=["LTCUSDT","DOTUSDT","ARBUSDT","APTUSDT","INJUSDT"]
STATE_FIELDS=["symbol","state_effective_ts","source_bar_start_ts","source_bar_end_ts","range_low","range_high","atr200","half_width","lower_inner","upper_inner","working_width_abs","working_width_pct_lower","working_width_pct_upper","working_width_pct_mid","range_span_pct","atr_pct_mid","range_atr_ratio","lower_stable_bars","upper_stable_bars","range_low_latest_age_bars","range_high_latest_age_bars","range_low_expiry_bars","range_high_expiry_bars","source_open","source_high","source_low","source_close"]
TOUCH_FIELDS=["event_id","symbol","side","touch_ts","state_effective_ts","boundary_price","touch_price","touch_delay_sec","range_low","range_high","atr200","half_width","lower_inner","upper_inner","working_width_abs","working_width_pct_lower","working_width_pct_upper","working_width_pct_mid","range_span_pct","atr_pct_mid","range_atr_ratio","lower_stable_bars","upper_stable_bars","side_stable_bars","opposite_stable_bars","range_low_latest_age_bars","range_high_latest_age_bars","range_low_expiry_bars","range_high_expiry_bars"]

def decstr(x):
    return format(x,'f')

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def process_symbol(sym, raw_root, parts_dir, first_date=None, last_date=None):
    t0=time.time()
    files=sorted((Path(raw_root)/sym/"public_trades").glob(f"{sym}*.csv.gz"))
    if first_date:
        files=[p for p in files if p.name[len(sym):len(sym)+10]>=first_date]
    if last_date:
        files=[p for p in files if p.name[len(sym):len(sym)+10]<=last_date]
    if not files: raise RuntimeError(f"{sym}: no input files")
    sp=Path(parts_dir)/f"states_{sym}.csv.gz"
    tp=Path(parts_dir)/f"touches_{sym}.csv.gz"
    recent=deque(maxlen=36)
    trs=[]
    atr=None
    prev_close=None
    bar=None
    bar_index=-1
    last_ts=None
    current_state=None
    current_touch_bucket=None
    touched=set()
    lower_prev=upper_prev=None
    lower_stable=upper_stable=0
    state_n=touch_n=trade_n=0
    first_ts=last_seen=None

    sf=gzip.open(sp,'wt',newline='')
    tf=gzip.open(tp,'wt',newline='')
    sw=csv.DictWriter(sf,fieldnames=STATE_FIELDS); sw.writeheader()
    tw=csv.DictWriter(tf,fieldnames=TOUCH_FIELDS); tw.writeheader()

    def finalize(b):
        nonlocal atr,prev_close,bar_index,current_state,lower_prev,upper_prev,lower_stable,upper_stable,state_n
        bs,o,h,l,c=b
        bar_index+=1
        tr=(h-l) if prev_close is None else max(h-l,abs(h-prev_close),abs(l-prev_close))
        if bar_index<200:
            trs.append(tr)
            if bar_index==199: atr=sum(trs,Decimal(0))/Decimal(200)
        else:
            atr=(atr*Decimal(199)+tr)/Decimal(200)
        prev_close=c
        recent.append((bs,o,h,l,c))
        current_state=None
        if atr is None or len(recent)<36: return
        lo=min(x[3] for x in recent); hi=max(x[2] for x in recent)
        half=atr*Decimal("0.5"); lower=lo+half; upper=hi-half
        mid=(lower+upper)/Decimal(2)
        width_abs=upper-lower
        width_lower=width_abs/lower*Decimal(100) if lower else Decimal(0)
        width_upper=width_abs/upper*Decimal(100) if upper else Decimal(0)
        width_mid=width_abs/mid*Decimal(100) if mid else Decimal(0)
        rmid=(lo+hi)/Decimal(2)
        span=(hi-lo)/rmid*Decimal(100) if rmid else Decimal(0)
        atrpct=atr/mid*Decimal(100) if mid else Decimal(0)
        ratio=(hi-lo)/atr if atr else Decimal(0)
        lower_stable=lower_stable+1 if lower_prev==lo else 1
        upper_stable=upper_stable+1 if upper_prev==hi else 1
        lower_prev,upper_prev=lo,hi
        low_age=next(i for i,x in enumerate(reversed(recent)) if x[3]==lo)
        high_age=next(i for i,x in enumerate(reversed(recent)) if x[2]==hi)
        eff=bs+300
        st={"symbol":sym,"state_effective_ts":eff,"source_bar_start_ts":bs,"source_bar_end_ts":eff,
            "range_low":decstr(lo),"range_high":decstr(hi),"atr200":decstr(atr),"half_width":decstr(half),
            "lower_inner":decstr(lower),"upper_inner":decstr(upper),"working_width_abs":decstr(width_abs),
            "working_width_pct_lower":decstr(width_lower),"working_width_pct_upper":decstr(width_upper),"working_width_pct_mid":decstr(width_mid),
            "range_span_pct":decstr(span),"atr_pct_mid":decstr(atrpct),"range_atr_ratio":decstr(ratio),
            "lower_stable_bars":lower_stable,"upper_stable_bars":upper_stable,
            "range_low_latest_age_bars":low_age,"range_high_latest_age_bars":high_age,
            "range_low_expiry_bars":36-low_age,"range_high_expiry_bars":36-high_age,
            "source_open":decstr(o),"source_high":decstr(h),"source_low":decstr(l),"source_close":decstr(c)}
        sw.writerow(st); state_n+=1; current_state=st

    try:
        for fp in files:
            with gzip.open(fp,'rt',newline='') as f:
                rd=csv.DictReader(f)
                if "timestamp" not in rd.fieldnames or "price" not in rd.fieldnames:
                    raise RuntimeError(f"{sym}: bad schema {fp}")
                for z in rd:
                    ts=float(z["timestamp"]); px=Decimal(z["price"]); trade_n+=1
                    if last_ts is not None and ts<last_ts: raise RuntimeError(f"{sym}: timestamps not monotonic at {fp}")
                    last_ts=ts; first_ts=ts if first_ts is None else first_ts; last_seen=ts
                    bucket=int(ts//300)*300
                    if bar is None:
                        bar=[bucket,px,px,px,px]
                        current_state=None
                    elif bucket!=bar[0]:
                        if bucket!=bar[0]+300: raise RuntimeError(f"{sym}: missing 5m candle {bar[0]} -> {bucket}")
                        prev_bucket=bar[0]
                        finalize(bar)
                        current_state=current_state if current_state and current_state["state_effective_ts"]==bucket else None
                        bar=[bucket,px,px,px,px]
                        current_touch_bucket=bucket; touched=set()
                    else:
                        if px>bar[2]: bar[2]=px
                        if px<bar[3]: bar[3]=px
                        bar[4]=px
                    if current_touch_bucket!=bucket:
                        current_touch_bucket=bucket; touched=set()
                    st=current_state
                    if st and st["state_effective_ts"]==bucket:
                        lower=Decimal(st["lower_inner"]); upper=Decimal(st["upper_inner"])
                        for side,boundary,hit in (("LONG",lower,px<=lower),("SHORT",upper,px>=upper)):
                            if hit and side not in touched:
                                touched.add(side)
                                side_st=st["lower_stable_bars"] if side=="LONG" else st["upper_stable_bars"]
                                opp_st=st["upper_stable_bars"] if side=="LONG" else st["lower_stable_bars"]
                                row={k:st[k] for k in TOUCH_FIELDS if k in st}
                                row.update({"event_id":f"{sym}:{bucket}:{side}","symbol":sym,"side":side,"touch_ts":repr(ts),
                                    "state_effective_ts":bucket,"boundary_price":decstr(boundary),"touch_price":decstr(px),
                                    "touch_delay_sec":repr(ts-bucket),"side_stable_bars":side_st,"opposite_stable_bars":opp_st})
                                tw.writerow(row); touch_n+=1
        if bar is not None: finalize(bar)
    finally:
        sf.close(); tf.close()
    return {"symbol":sym,"files":len(files),"trades":trade_n,"states":state_n,"touches":touch_n,
            "first_trade_ts":first_ts,"last_trade_ts":last_seen,"seconds":round(time.time()-t0,3),
            "states_file":sp.name,"touches_file":tp.name,"states_sha256":sha256(sp),"touches_sha256":sha256(tp)}

def load_sqlite(db, parts, metas):
    if db.exists(): db.unlink()
    con=sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=OFF"); con.execute("PRAGMA synchronous=OFF"); con.execute("PRAGMA temp_store=MEMORY")
    state_cols=[f'"{x}" TEXT' for x in STATE_FIELDS]
    touch_cols=[f'"{x}" TEXT' for x in TOUCH_FIELDS]
    con.execute("CREATE TABLE states ("+",".join(state_cols)+")")
    con.execute("CREATE TABLE touches ("+",".join(touch_cols)+")")
    def ins(table,fields,path):
        q="INSERT INTO "+table+" VALUES ("+",".join("?" for _ in fields)+")"
        with gzip.open(path,'rt',newline='') as f:
            rd=csv.DictReader(f); batch=[]
            for row in rd:
                batch.append([row[x] for x in fields])
                if len(batch)>=5000: con.executemany(q,batch); batch=[]
            if batch: con.executemany(q,batch)
    for m in metas:
        ins("states",STATE_FIELDS,parts/m["states_file"]); ins("touches",TOUCH_FIELDS,parts/m["touches_file"])
    con.execute("CREATE INDEX idx_states_symbol_time ON states(symbol,state_effective_ts)")
    con.execute("CREATE INDEX idx_touches_symbol_side_time ON touches(symbol,side,touch_ts)")
    con.execute("CREATE INDEX idx_touches_fast_filters ON touches(symbol,side,working_width_pct_lower,side_stable_bars)")
    con.commit(); con.execute("ANALYZE"); con.commit()
    counts={"states":con.execute("SELECT COUNT(*) FROM states").fetchone()[0],"touches":con.execute("SELECT COUNT(*) FROM touches").fetchone()[0]}
    con.close(); return counts

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--raw-root",required=True); ap.add_argument("--out",required=True)
    ap.add_argument("--symbols",nargs="+",default=DEFAULT_SYMBOLS); ap.add_argument("--workers",type=int,default=3)
    ap.add_argument("--first-date"); ap.add_argument("--last-date")
    a=ap.parse_args()
    out=Path(a.out).absolute()
    allowed=Path("/data/cripta/research/runs")
    if allowed not in out.parents or out.exists(): raise RuntimeError("v2 output must be new under Data research runs")
    if os.geteuid()==0 or not os.access(out.parent,os.W_OK|os.X_OK): raise PermissionError("requires exact cripta writer")
    if (a.first_date,a.last_date)!=("2026-05-08","2026-08-15") or sorted(a.symbols)!=sorted(DEFAULT_SYMBOLS):
        raise ValueError("P90L90 v2 requires all five symbols and May08..Aug15 exact input")
    for sym in a.symbols:
        p=Path(a.raw_root)/f"manifest_{sym}.json"
        v=json.loads(p.read_text())
        if v["status"]!="INPUT_QC_PASS_NOT_YET_GEOMETRY_READY" or len(v["files"])!=100:
            raise ValueError("v2 prewarm input manifest not validated")
    parts=out/"parts"; parts.mkdir(parents=True,exist_ok=False)
    started=time.time(); metas=[]; pending={}
    print(json.dumps({"stage":"START","symbols":a.symbols,"workers":a.workers,"first_date":a.first_date,"last_date":a.last_date}),flush=True)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for s in a.symbols: pending[ex.submit(process_symbol,s,a.raw_root,str(parts),a.first_date,a.last_date)]=s
        while pending:
            done,_=wait(list(pending),timeout=20,return_when=FIRST_COMPLETED)
            if not done:
                print(json.dumps({"stage":"HEARTBEAT","completed":len(metas),"total":len(a.symbols),"elapsed_s":round(time.time()-started,1)}),flush=True); continue
            for fut in done:
                s=pending.pop(fut); m=fut.result(); metas.append(m)
                print(json.dumps({"stage":"SYMBOL_DONE",**m,"completed":len(metas),"total":len(a.symbols)}),flush=True)
    metas.sort(key=lambda x:a.symbols.index(x["symbol"]))
    with open(out/"by_symbol.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(metas[0])); w.writeheader(); w.writerows(metas)
    counts=load_sqlite(out/"l53_clean.sqlite",parts,metas)
    schema={"dataset":"L5-3 CLEAN P90L90","version":"v2","geometry":{"timeframe":"5m","lookback_closed_bars":36,"atr":"Wilder ATR200","half_width":"ATR200*0.5","gap":0},
      "semantics":{"states":"causal L5-3 geometry available at state_effective_ts; no strategy filters","touches":"first raw public-trade touch per state+side of corresponding inner boundary; no width/STAND6/Entry/Exit/outcome filters","width_metrics":"absolute width plus percent vs lower_inner, upper_inner, and midpoint; filters must name the metric explicitly",
      "LONG":"lower_inner touch","SHORT":"upper_inner touch"},"state_fields":STATE_FIELDS,"touch_fields":TOUCH_FIELDS}
    (out/"schema.json").write_text(json.dumps(schema,ensure_ascii=False,indent=2)+"\n")
    files=[out/"l53_clean.sqlite",out/"by_symbol.csv",out/"schema.json"]+sorted(parts.glob("*.csv.gz"))
    manifest={"status":"COMPLETE","dataset":"L5-3 CLEAN P90L90","version":"v2","symbols":a.symbols,"symbol_count":len(a.symbols),
      "raw_root":a.raw_root,"first_date_filter":a.first_date,"last_date_filter":a.last_date,"workers":a.workers,
      "counts":counts,"per_symbol":metas,"elapsed_s":round(time.time()-started,3),
      "files":{p.relative_to(out).as_posix():{"bytes":p.stat().st_size,"sha256":sha256(p)} for p in files}}
    (out/"result_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"stage":"COMPLETE","counts":counts,"elapsed_s":manifest["elapsed_s"]}),flush=True)
if __name__=="__main__": main()
