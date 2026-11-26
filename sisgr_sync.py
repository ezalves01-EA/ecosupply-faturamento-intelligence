"""Importação piloto por faturamento, com publicação transacional."""
import os,json,time,calendar,urllib.request,urllib.parse,urllib.error
from threading import Lock
_rate_lock=Lock()
_next_request=0.0
_cooldown=0.0
from decimal import Decimal
from datetime import date
from sisgr_probe import BASE,NoRedirect

class Client:
    def __init__(self):
        self.token=os.environ["SISGR_TOKEN"]
        self.opener=urllib.request.build_opener(NoRedirect)
        self.cache={}
    def get(self,path,params=None):
        req=urllib.request.Request(BASE+"/"+path+("?"+urllib.parse.urlencode(params) if params else ""),headers={"Authorization":"Bearer "+self.token,"Accept":"application/json"})
        global _next_request,_cooldown
        for attempt in range(5):
            while True:
                with _rate_lock:
                    delay=max(_next_request,_cooldown)-time.monotonic()
                    if delay<=0:
                        _next_request=time.monotonic()+0.35
                        break
                time.sleep(delay)
            try:
                with self.opener.open(req,timeout=30) as r:return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code not in (429,502,503,504) or attempt==4:raise
                delay=min(60,2**(attempt+2))
                if e.code==429:
                    with _rate_lock:
                        _cooldown=max(_cooldown,time.monotonic()+delay)
                time.sleep(delay)
    def listing(self,path,params):
        seen=set()
        for page in range(1,501):
            p=self.get(path,{**params,"page":page,"per_page":100})
            if isinstance(p,list) and len(p)==1 and "data" in p[0]:p=p[0]
            rows=p["data"]
            if int(p["current_page"])!=page:raise ValueError("Paginação divergente")
            for r in rows:
                if r["id"] in seen:raise ValueError("ID duplicado")
                seen.add(r["id"]);yield r
            if page==int(p["last_page"]):return
        raise ValueError("Limite de paginação")
    def detail(self,path,id):
        if not id:return {}
        key=(path,str(id))
        if key not in self.cache:
            p=self.get(path+"/"+str(id))
            if isinstance(p,dict) and "data" in p:p=p["data"]
            if isinstance(p,list):p=next(r for r in p if str(r.get("id"))==str(id))
            if str(p.get("id"))!=str(id):raise ValueError("ID divergente")
            self.cache[key]=p
        return self.cache[key]

def name(v):
    if isinstance(v,dict):return str(v.get("name") or v.get("nome") or v.get("fantasy_name") or v.get("id") or "")
    return str(v or "")
def canceled(r):
    return bool(r.get("canceled_at")) or str(r.get("canceled","")).lower() in ("1","true","sim")
def dec(v):return Decimal(str(v if v is not None and v!="" else 0))
def build(client,bills,fields,progress):
    rows=[];issues=[];warnings=[];seen_collect=set();bm_total=Decimal(0)
    for n,b in enumerate(bills,1):
        progress(n,len(bills))
        for bm in client.listing("romain",{"billing_id":b["id"]}):
            if str(bm["billing_id"])!=str(b["id"]):raise ValueError("BM fora do faturamento")
            if canceled(bm):continue
            subtotal=Decimal(0)
            bm_rows=[]
            for i in client.listing("romain-items",{"romanian_id":bm["id"]}):
                if str(i["romanian_id"])!=str(bm["id"]):raise ValueError("Item fora do BM")
                if canceled(i):continue
                amount=dec(i["total"]);subtotal+=amount
                co=client.detail("collects",i.get("collect_id"))
                sid=i.get("schedule_id") or co.get("agendamento_id") or co.get("schedule_id")
                if i.get("schedule_id") and (co.get("agendamento_id") or co.get("schedule_id")) and str(i["schedule_id"])!=str(co.get("agendamento_id") or co.get("schedule_id")):
                    issues.append("Vínculo divergente item "+str(i["id"]));sid=None
                s=client.detail("schedules",sid)
                if canceled(s):issues.append("Agendamento cancelado item "+str(i["id"]));s={}
                g=client.detail("generators",s.get("generator_id") or i.get("generator_id") or bm.get("generator_id"))
                driver=client.detail("professionals",s.get("motoristaIniciouAtendimento_id") or s.get("driver_id"))
                address=g.get("address") or {};city=address.get("city") or {};state=city.get("state") or {} if isinstance(city,dict) else {}
                uf=state.get("uf") or state.get("sigla") or "" if isinstance(state,dict) else state
                end=str(s.get("end_attendance") or s.get("end_at") or "")
                residue=co.get("residue") or {}
                unit=residue.get("unity") or ""
                collected=Decimal(0)
                dest=co.get("destination") or {}
                key=dest.get("id") or co.get("id")
                if key and key not in seen_collect and unit and dest.get("measure") is not None:
                    collected=dec(dest["measure"]);seen_collect.add(key)
                row=dict.fromkeys(fields,"")
                row.update(item_id=str(i["id"]),billing_id=str(b["id"]),bm_id=str(bm["id"]),billing_date=str(b["registed_at"])[:10],due_date=str(b.get("due_date") or "")[:10],branch=name(b.get("company")),client=name(b.get("client")),generator=name(g),city=name(city),state=str(uf)[:5],origin=name(i.get("origin")),product=name(residue.get("name") or residue.get("nome") or (str(i.get("description") or "").split(" - ",1)[1] if " - " in str(i.get("description") or "") and "FRANQUIA" not in str(i.get("description") or "").upper() else "")),service=name(i.get("description")),unit=str(unit),collected=collected,quantity=dec(i.get("quantity")),amount=amount,vehicle_id=str(s.get("vehicle_id") or ""),driver=name(driver),transporter=str(s.get("transporter_id") or ""),schedule_id=str(sid or ""),collect_id=str(i.get("collect_id") or ""),scheduled_date=str(s.get("schedule_date") or "")[:10],attendance_start=str(s.get("start_attendance") or s.get("start_at") or ""),attendance_end=end,operation_date=end[:10])
                bm_rows.append(row)
                rows.append(row)
            linked=[r for r in bm_rows if r["schedule_id"] and r["vehicle_id"]]
            dimensions=("vehicle_id","city","state","driver","transporter","generator","operation_date","schedule_id","scheduled_date","attendance_start","attendance_end")
            for row in bm_rows:
                if "FRANQUIA" not in row["service"].upper() or row["vehicle_id"]:continue
                candidates=[r for r in bm_rows if r is not row and "FRANQUIA" not in r["service"].upper()]
                if linked and len(candidates)==len(linked) and len({r["vehicle_id"] for r in linked})==1:
                    for field in dimensions:
                        vals={r[field] for r in linked}
                        if len(vals)==1:row[field]=next(iter(vals))
                    row["product"]="Franquia — múltiplos resíduos"
                else:
                    warnings.append("Franquia sem atribuição segura: "+row["item_id"])
                    row["product"]="Franquia — sem atribuição operacional"
            expected=dec(bm.get("total_value"));bm_total+=expected
            if subtotal!=expected:
                (warnings if abs(subtotal-expected)<=Decimal("0.01") else issues).append("BM "+str(bm["id"])+" diferença "+str(expected-subtotal))
    return rows,{"bills":len(bills),"items":len(rows),"bm_total":str(bm_total),"item_total":str(sum((r["amount"] for r in rows),Decimal(0))),"issues":issues,"warnings":warnings}
