"""Carga anual retomável, com checkpoint PostgreSQL."""
import json,time,threading
from sqlalchemy import text
from sisgr_month import run_batch
from concurrent.futures import ThreadPoolExecutor
def setup(engine):
    with engine.begin() as db:
        db.execute(text("CREATE TABLE IF NOT EXISTS efi_annual (month VARCHAR(7) PRIMARY KEY, pos INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, saved INTEGER NOT NULL DEFAULT 0, existing INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0, status VARCHAR(20) NOT NULL DEFAULT 'fila')"))
        db.execute(text("CREATE TABLE IF NOT EXISTS efi_annual_errors (month VARCHAR(7), pos INTEGER, detail TEXT, PRIMARY KEY(month,pos))"))
        db.execute(text("CREATE TABLE IF NOT EXISTS efi_annual_control (id INTEGER PRIMARY KEY, active INTEGER NOT NULL)"))
        db.execute(text("INSERT INTO efi_annual_control VALUES(1,0) ON CONFLICT(id) DO NOTHING"))
def start(engine):
    setup(engine)
    with engine.begin() as db:
        for m in range(1,13):
            db.execute(text("INSERT INTO efi_annual(month) VALUES(:m) ON CONFLICT(month) DO NOTHING"),{"m":f"2026-{m:02d}"})
        db.execute(text("UPDATE efi_annual_control SET active=1 WHERE id=1"))
def pause(engine):
    with engine.begin() as db:db.execute(text("UPDATE efi_annual_control SET active=0 WHERE id=1"))
def worker(engine,fields):
    while True:
        try:
            setup(engine)
            with engine.connect() as lock:
                if engine.dialect.name!="postgresql":return
                if not lock.execute(text("SELECT pg_try_advisory_lock(20261126)")).scalar():
                    time.sleep(10);continue
                lock.commit()
                try:
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        while True:
                            with engine.connect() as db:
                                active=db.execute(text("SELECT active FROM efi_annual_control WHERE id=1")).scalar()
                                row=db.execute(text("SELECT month,pos,total FROM efi_annual WHERE status<>'concluido' ORDER BY month LIMIT 1")).first()
                            if not active or not row:break
                            month,pos,total=row
                            offsets=list(range(pos,min(pos+3,total))) if total else [pos]
                            if not offsets:offsets=[pos]
                            futures=[pool.submit(run_batch,engine,fields,month,p,1,"missing") for p in offsets]
                            for p,f in zip(offsets,futures):
                                result=f.result()
                                items=result["results"]
                                if result["next"]==p and p<result["total"]:raise RuntimeError("Sem progresso")
                                counts={k:sum(x["status"]==k for x in items) for k in ("gravado","existente","pendente","erro")}
                                with engine.begin() as db:
                                    if counts["pendente"] or counts["erro"]:
                                        db.execute(text("INSERT INTO efi_annual_errors VALUES(:m,:p,:d) ON CONFLICT(month,pos) DO UPDATE SET detail=excluded.detail"),{"m":month,"p":p,"d":json.dumps(items,default=str)})
                                    db.execute(text("UPDATE efi_annual SET pos=:p,total=:t,saved=saved+:s,existing=existing+:e,pending=pending+:n,status=:status WHERE month=:m"),{"p":result["next"],"t":result["total"],"s":counts["gravado"],"e":counts["existente"],"n":counts["pendente"]+counts["erro"],"status":"concluido" if result["next"]>=result["total"] else "rodando","m":month})
                finally:
                    lock.execute(text("SELECT pg_advisory_unlock(20261126)"));lock.commit()
        except Exception:
            import logging
            logging.exception("EFI carga anual; retomada em 30 segundos")
            time.sleep(30)
        time.sleep(5)
def launch(engine,fields):
    threading.Thread(target=worker,args=(engine,fields),daemon=True,name="efi-annual").start()
