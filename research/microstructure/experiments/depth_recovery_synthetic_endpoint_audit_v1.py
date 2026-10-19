#!/usr/bin/env python3
"""#13 deterministic synthetic book-message episode QC, no server/raw writes.
Checks source behavior versus precise endpoint right-censor semantics, reports differences.
"""
import datetime as dt,json
from decimal import Decimal
def events(records,end_ms,window_ms=60000):
 book={};pending={};result=dict(events=0,recovered=0,expired=0,censored=0)
 for ts,p,q in records:
  for key,(started,target) in list(pending.items()):
   if ts-started>window_ms:
    result["expired"]+=1;del pending[key]
  old=book.get(p,Decimal(0))
  if p in pending and q>=pending[p][1]:
   result["recovered"]+=1;del pending[p]
  elif old>0 and q<old and p not in pending:
   pending[p]=(ts,old);result["events"]+=1
  if q<=0:book.pop(p,None)
  else:book[p]=q
 # A finite one-hour measurement window is NOT an observation event.
 result["censored"]=len(pending)
 result["pending_older_than_window_at_end"]=sum(end_ms-ts>window_ms for ts,_ in pending.values())
 return result
def main():
 cases=[
  ("reduction_without_later_price_update",[(0,Decimal(100),Decimal(10)),(1000,Decimal(100),Decimal(8))],86400000),
  ("recovery_before_deadline",[(0,Decimal(100),Decimal(10)),(1000,Decimal(100),Decimal(8)),(2000,Decimal(100),Decimal(10))],86400000),
  ("other_price_event_expires_after_deadline",[(0,Decimal(100),Decimal(10)),(1000,Decimal(100),Decimal(8)),(62001,Decimal(101),Decimal(3))],86400000)
 ]
 for label,records,end in cases:
  print(json.dumps(dict(case=label,**events(records,end))))
if __name__=="__main__":main()
