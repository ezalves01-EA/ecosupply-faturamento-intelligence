import os,json,urllib.request,urllib.error,urllib.parse

BASE="https://ecosupply.sisgr.com/api/v1"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def probe():
    token=os.getenv("SISGR_TOKEN")
    if not token:
        return {"status":"Token não configurado"}
    opener=urllib.request.build_opener(NoRedirect)
    results={}
    for path in ("billing","romain","romain-items"):
        try:
            req=urllib.request.Request(BASE+"/"+path+"?page=1&per_page=1",headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
            with opener.open(req,timeout=15) as response:
                data=json.loads(response.read(4000000))
            if isinstance(data,list) and len(data)==1 and isinstance(data[0],dict) and "data" in data[0]:
                data=data[0]
            rows=data.get("data",[]) if isinstance(data,dict) else data
            results[path]={"status":"OK","campos":sorted(rows[0].keys()) if isinstance(rows,list) and rows and isinstance(rows[0],dict) else [],"paginacao":{k:data[k] for k in ("current_page","last_page","total","per_page") if isinstance(data,dict) and k in data}}
        except urllib.error.HTTPError as e:
            results[path]={"status":"HTTP "+str(e.code)}
        except Exception as e:
            results[path]={"status":type(e).__name__}
    return results


def test_filters():
    token=os.getenv("SISGR_TOKEN")
    if not token:return {"status":"Token não configurado"}
    opener=urllib.request.build_opener(NoRedirect)
    def get(path,params):
        req=urllib.request.Request(BASE+"/"+path+"?"+urllib.parse.urlencode(params),headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
        with opener.open(req,timeout=15) as response:
            p=json.loads(response.read(8000000))
        if isinstance(p,list) and len(p)==1 and isinstance(p[0],dict) and "data" in p[0]:p=p[0]
        return (p.get("data",[]) if isinstance(p,dict) else p,p.get("total") if isinstance(p,dict) else None)
    tests=[
        ("billing_period","billing",{"registed_at$bw":"2026-11-01,2026-11-30"},"registed_at",lambda v:str(v)[:10]>="2026-11-01" and str(v)[:10]<="2026-11-30"),
        ("bm_billing","romain",{"billing_id":105595},"billing_id",lambda v:str(v)=="105595"),
        ("items_bm","romain-items",{"romanian_id":106452},"romanian_id",lambda v:str(v)=="106452"),
        ("items_bm_eq","romain-items",{"romanian_id$eq":106452},"romanian_id",lambda v:str(v)=="106452"),
        ("bm_billing_eq","romain",{"billing_id$eq":105595},"billing_id",lambda v:str(v)=="105595"),
    ]
    result={}
    for name,path,params,field,check in tests:
        try:
            rows,total=get(path,{**params,"page":1,"per_page":10})
            result[name]={"total":total,"sample_count":len(rows),"matches":sum(check(row.get(field)) for row in rows),"ids":[row.get("id") for row in rows],"status":"OK"}
        except urllib.error.HTTPError as e:result[name]={"status":"HTTP "+str(e.code)}
        except Exception as e:result[name]={"status":type(e).__name__}
    return result
