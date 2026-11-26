"""Importação mensal em lotes retomáveis pelo navegador."""
from datetime import date
from calendar import monthrange
from decimal import Decimal
from sqlalchemy import text
from sisgr_sync import Client,build

def run_batch(engine,fields,month,offset,limit=5):
    year,m=map(int,month.split("-"))
    start=date(year,m,1).isoformat();end=date(year,m,monthrange(year,m)[1]).isoformat()
    client=Client()
    # A lista completa é validada antes de selecionar o lote.
    bills=sorted(client.listing("billing",{"registed_at$bw":start+","+end}),key=lambda b:int(b["id"]))
    if any(not start<=str(b["registed_at"])[:10]<=end for b in bills):raise ValueError("Filtro de período divergente")
    batch=bills[offset:offset+limit]
    results=[]
    statement=text("INSERT INTO revenue ("+",".join(fields)+") VALUES ("+",".join(":"+f for f in fields)+") ON CONFLICT(item_id) DO UPDATE SET "+",".join(f+"=excluded."+f for f in fields[1:]))
    for bill in batch:
        try:
            rows,audit=build(client,[bill],fields,lambda n,t:None)
            if not rows or Decimal(audit["bm_total"])!=Decimal(audit["item_total"]) or audit["issues"]:
                results.append({"id":bill["id"],"status":"pendente","audit":audit});continue
            with engine.begin() as db:
                for row in rows:
                    row["plate"]=db.execute(text("SELECT plate FROM fleet WHERE vehicle_id=:id"),{"id":row["vehicle_id"]}).scalar() or ""
                db.execute(text("DELETE FROM revenue WHERE billing_id=:id"),{"id":str(bill["id"])})
                db.execute(statement,rows)
            results.append({"id":bill["id"],"status":"gravado","audit":audit})
        except Exception as e:
            results.append({"id":bill["id"],"status":"erro","tipo":type(e).__name__,"http":getattr(e,"code",None)})
    return {"total":len(bills),"next":offset+len(batch),"results":results}
