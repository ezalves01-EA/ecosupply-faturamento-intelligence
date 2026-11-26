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
