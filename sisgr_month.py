"""Importação mensal em lotes retomáveis pelo navegador."""
from datetime import date
from calendar import monthrange
from decimal import Decimal
from sqlalchemy import text
from threading import BoundedSemaphore, Lock
from time import monotonic
_page_lock=Lock()
_pages={}
_page_size=100
slots=BoundedSemaphore(3)
from sisgr_sync import Client,build

def run_batch(engine,fields,month,offset,limit=1,mode="full"):
    with slots:
        return _run_batch(engine,fields,month,offset,mode)

def _run_batch(engine,fields,month,offset,mode):
    year,m=map(int,month.split("-"))
    start=date(year,m,1).isoformat();end=date(year,m,monthrange(year,m)[1]).isoformat()
    client=Client(engine if mode=='missing' else None)
    page=offset//_page_size+1
    key=(month,page)
    with _page_lock:
        cached=_pages.get(key)
        if not cached or monotonic()-cached[0]>300:
            payload=client.get("billing",{"registed_at$bw":start+","+end,"page":page,"per_page":_page_size})
            if isinstance(payload,list) and len(payload)==1 and "data" in payload[0]:payload=payload[0]
            if int(payload["current_page"])!=page:raise ValueError("Página divergente")
            total=int(payload["total"])
            if len(payload["data"])!=max(0,min(_page_size,total-(page-1)*_page_size)):raise ValueError("Tamanho de página divergente")
            if len({b['id'] for b in payload['data']})!=len(payload['data']):raise ValueError("IDs duplicados na página")
            if len(_pages)>100:_pages.clear()
            _pages[key]=(monotonic(),payload)
        else:payload=cached[1]
    total=int(payload["total"])
    bills=payload["data"][offset%_page_size:offset%_page_size+1]
    if any(not start<=str(b["registed_at"])[:10]<=end for b in bills):raise ValueError("Filtro de período divergente")
    batch=bills
    results=[]
    statement=text("INSERT INTO revenue ("+",".join(fields)+") VALUES ("+",".join(":"+f for f in fields)+") ON CONFLICT(item_id) DO UPDATE SET "+",".join(f+"=excluded."+f for f in fields[1:]))
    for bill in batch:
        try:
            if mode=="missing":
                with engine.connect() as db:
                    exists=db.execute(text("SELECT 1 FROM revenue WHERE billing_id=:id LIMIT 1"),{"id":str(bill["id"])}).scalar()
                if exists:
                    results.append({"id":bill["id"],"status":"existente"});continue
            rows,audit=build(client,[bill],fields,lambda n,t:None)
            if not rows or abs(Decimal(audit["bm_total"])-Decimal(audit["item_total"]))>Decimal("0.01") or audit["issues"]:
                results.append({"id":bill["id"],"status":"pendente","audit":audit});continue
            with engine.begin() as db:
                if engine.dialect.name=="postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:id))"),{"id":str(bill["id"])})
                for row in rows:
                    row["plate"]=db.execute(text("SELECT plate FROM fleet WHERE vehicle_id=:id"),{"id":row["vehicle_id"]}).scalar() or ""
                db.execute(text("DELETE FROM revenue WHERE billing_id=:id"),{"id":str(bill["id"])})
                db.execute(statement,rows)
            results.append({"id":bill["id"],"status":"gravado","audit":audit})
        except Exception as e:
            results.append({"id":bill["id"],"status":"erro","tipo":type(e).__name__,"http":getattr(e,"code",None)})
    return {"total":total,"next":offset+len(batch),"results":results}
