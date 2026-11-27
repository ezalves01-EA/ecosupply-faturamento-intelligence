import json
import os
import urllib.request
import urllib.parse
import time
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from flask import request, render_template_string, redirect, abort, jsonify
from sqlalchemy import text

def register(app, engine, root, html, money):
    def norm(v):
        return ''.join(c for c in unicodedata.normalize('NFKD',str(v or '').upper()) if c.isalnum())
    def seed():
        result={}
        for line in (root/'data'/'rotas_custos.txt').read_text(encoding='utf-8').splitlines():
            p,t,k,groups=line.split('|')
            for group in groups.split(';'):
                w,c=group.split(':',1)
                rid=p+'_'+w
                result[rid]={'plate':p,'type':t,'rate':k,'week':w,'cities':c,'destination':'','Sinop':'','Cuiabá':''}
        return result
    def load():
        with engine.begin() as db:
            db.execute(text("CREATE TABLE IF NOT EXISTS efi_route_config (id VARCHAR(100) PRIMARY KEY, payload TEXT NOT NULL)"))
            db.execute(text("CREATE TABLE IF NOT EXISTS efi_route_trips (id VARCHAR(250) PRIMARY KEY, trips INTEGER NOT NULL)"))
            configs=seed()
            for rid,payload in db.execute(text("SELECT id,payload FROM efi_route_config")):
                configs[rid]=json.loads(payload)
            trips=dict(db.execute(text("SELECT id,trips FROM efi_route_trips")).all())
        return configs,trips
    def page(body,**kw):
        head=html.split('<header>',1)[0]
        for anchor in ('overview','conciliation','ranking','map','audit'):
            head=head.replace('href="#'+anchor+'"','href="/#'+anchor+'"')
        return render_template_string(head+body+'</main></body></html>',args=request.args,money=money,**kw)
    def distance(base, city):
        key=norm(base)+'|'+norm(city)
        with engine.begin() as db:
            db.execute(text("CREATE TABLE IF NOT EXISTS efi_road_distance (id VARCHAR(250) PRIMARY KEY, km NUMERIC(12,3) NOT NULL)"))
            cached=db.execute(text("SELECT km FROM efi_road_distance WHERE id=:id"),{'id':key}).scalar()
        if cached is not None:return Decimal(str(cached))
        def get(url):
            req=urllib.request.Request(url,headers={'User-Agent':'Ecosupply-EFI/1.0 (ecosupply-faturamento-intelligence.onrender.com)'})
            with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
        def coords(name):
            uf='RO' if norm(name) in ('VILHENA','CEREJEIRAS') else 'MT'
            query=urllib.parse.urlencode({'city':name,'state':'Rondônia' if uf=='RO' else 'Mato Grosso','country':'Brasil','format':'jsonv2','limit':1,'countrycodes':'br'})
            time.sleep(1.1)
            data=get('https://nominatim.openstreetmap.org/search?'+query)
            if not data:raise ValueError('Município não localizado: '+name)
            return data[0]['lon']+','+data[0]['lat']
        if norm(base)==norm(city):km=Decimal(0)
        else:
            a,b=coords(base),coords(city)
            url=os.getenv('OSRM_URL','https://router.project-osrm.org').rstrip('/')
            data=get(url+'/route/v1/driving/'+a+';'+b+'?overview=false')
            if data.get('code')!='Ok':raise ValueError('Sem rota rodoviária: '+city)
            km=(Decimal(str(data['routes'][0]['distance']))/1000).quantize(Decimal('.001'))
        with engine.begin() as db:
            db.execute(text("INSERT INTO efi_road_distance(id,km) VALUES(:id,:km) ON CONFLICT(id) DO NOTHING"),{'id':key,'km':str(km)})
        return km
    @app.post('/cadastro-rotas/calcular')
    def route_calculate():
        configs,_=load()
        payload=request.get_json(silent=True) or {}
        rid=payload.get('id')
        base=payload.get('base')
        if rid not in configs or base not in ('Sinop','Cuiabá'):abort(400)
        c=configs[rid].copy()
        cities=[v.strip() for v in c['cities'].split(',') if norm(v) not in ('','ESPORADICO')]
        if not cities:return jsonify(status='skip',message='Sem cidades fixas')
        if c.get(base) and not payload.get('force'):return jsonify(status='skip',message='Distância já cadastrada')
        # One city per request; browser advances sequentially, avoiding long HTTP requests.
        city=payload.get('city')
        if city not in cities:abort(400)
        try:km=distance(base,city)
        except Exception as exc:return jsonify(status='error',message=str(exc)),502
        return jsonify(status='ok',city=city,km=float(km))
    @app.post('/cadastro-rotas/concluir')
    def route_finish():
        configs,_=load()
        p=request.get_json(silent=True) or {}
        rid,base=p.get('id'),p.get('base')
        if rid not in configs or base not in ('Sinop','Cuiabá'):abort(400)
        c=configs[rid].copy()
        cities=[v.strip() for v in c['cities'].split(',') if norm(v) not in ('','ESPORADICO')]
        if not cities:abort(400)
        with engine.begin() as db:
            values=[]
            for city in cities:
                km=db.execute(text("SELECT km FROM efi_road_distance WHERE id=:id"),{'id':norm(base)+'|'+norm(city)}).scalar()
                if km is None:return jsonify(status='error',message='Distâncias incompletas'),409
                values.append((Decimal(str(km)),city))
            km,city=max(values)
            c[base]=str(km)
            c['destination_'+base]=city
            c['destination']=city
            db.execute(text("INSERT INTO efi_route_config(id,payload) VALUES(:id,:p) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload"),{'id':rid,'p':json.dumps(c,ensure_ascii=False)})
        return jsonify(status='ok',destination=city,km=float(km))
    @app.get('/simulador-custos')
    def cost_simulator():
        configs,trips=load()
        year=request.args.get('year','2026')
        month=request.args.get('month','')
        if len(year)!=4 or not year.isdigit():abort(400)
        if month and month not in [f'{i:02d}' for i in range(1,13)]:abort(400)
        idx=defaultdict(set)
        for rid,c in configs.items():
            for city in c['cities'].split(','):
                if norm(city) not in ('','ESPORADICO'):idx[(norm(c['plate']),norm(city))].add(rid)
        data=[]
        basis=request.args.get('basis','billing')
        column='operation_date' if basis=='operation' else 'billing_date'
        plate_configs=defaultdict(list)
        for rid,c in configs.items():plate_configs[norm(c['plate'])].append(rid)
        with engine.connect() as db:
            rows=db.execute(text("""SELECT r.plate,r.city,r.state,r.transporter,substr(r.operation_date,1,7),r.origin,COALESCE(rg.region,'Não classificada'),SUM(r.amount),COALESCE(NULLIF(f.vehicle_type,''),NULLIF(fp.vehicle_type,''),'Não classificado')
            FROM revenue r LEFT JOIN regions rg ON UPPER(TRIM(r.city))=rg.city AND UPPER(TRIM(r.state))=rg.state
            LEFT JOIN fleet f ON r.vehicle_id=f.vehicle_id LEFT JOIN fleet_plates fp ON r.plate=fp.plate
            WHERE r."""+column+""" LIKE :period GROUP BY 1,2,3,4,5,6,7,9"""),{'period':year+('-'+month if month else '')+'%'}).all()
        general=sum((Decimal(str(row[7] or 0)) for row in rows),Decimal(0))
        for p,city,uf,t,period,origin,region,amount,vehicle_type in rows:
            base='Sinop' if str(t) in ('1','2','3','6') else 'Cuiabá' if str(t)=='5' else ''
            if not p or not city or not base or norm(origin) not in ('COLETA','SERVICO'):continue
            matches=idx[(norm(p),norm(city))]
            known=plate_configs[norm(p)]
            rid=next(iter(matches)) if len(matches)==1 else ''
            if not rid:
                rid='operacional:'+norm(p)+':'+norm(uf)+':'+norm(city)
                rates={configs[k]['rate'] for k in known}
                types={configs[k]['type'] for k in known}
                configs[rid]={'plate':p,'type':next(iter(types)) if len(types)==1 else vehicle_type,'rate':next(iter(rates)) if len(rates)==1 else '', 'destination':city,'Sinop':'','Cuiabá':''}
            c=configs[rid]
            data.append(dict(route=rid,plate=c['plate'],city=city,state=uf,base=base,period=period or 'Sem data operacional',origin=origin,region=region,type=c['type'],amount=float(amount or 0)))
        operational=sum((Decimal(str(r['amount'])) for r in data),Decimal(0))
        return page("""<header><h1>Simulador de Custo Logístico</h1><div>Seleção geográfica e análise de receita operacional</div></header><main><section><form class="filters"><label>Ano <input name="year" value="{{year}}" size="5"></label><label>Mês <select name="month"><option value="">Todos</option>{% for m in months %}<option {{'selected' if m==month else ''}}>{{m}}</option>{% endfor %}</select></label><label>Data <select name="basis"><option value="billing" {{'selected' if basis!='operation' else ''}}>Faturamento</option><option value="operation" {{'selected' if basis=='operation' else ''}}>Operação</option></select></label><button>Atualizar período</button><a href="/cadastro-rotas">Cadastro de custos</a></form></section>
        <style>.sim-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(240px,1fr);gap:16px}.sim-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.sim-filters label{display:grid;gap:4px}.sim-filters select{width:100%;padding:8px;border:1px solid #dce5ed;border-radius:5px}#sim-map{width:100%;height:430px;background:#f3f6f8}#sim-map path{cursor:pointer}#sim-map path:hover{stroke:#2466ad;stroke-width:2}.sim-row input{width:90px;padding:6px;border:1px solid #dce5ed}@media(max-width:950px){.sim-grid{grid-template-columns:1fr}}</style>
        <section><div class="sim-grid"><div><h2>Mapa de faturamento</h2><label>Selecionar por <select id="sim-mode"><option value="region">Região</option><option value="route">Rota</option><option value="city">Cidade</option></select></label><svg id="sim-map" viewBox="0 0 800 650"></svg><small id="sim-mapinfo">Clique no mapa para filtrar. Municípios fora de MT disponíveis nos filtros.</small></div><div><h2>Distribuição da receita</h2><div id="sim-ranking"></div></div></div></section>
        <section><h2>Seleção operacional</h2><p>Faturamento geral do período: <b>{{money(general)}}</b> · Receita operacional elegível: <b>{{money(operational)}}</b> · Diferença de escopo: <b>{{money(general-operational)}}</b>. Vendas e itens sem placa, cidade ou base não integram a simulação.</p><div id="sim-filters" class="sim-filters"></div><p><button id="sim-clear">Limpar filtros</button></p><div class="metrics"><article>Faturamento<div class="big" id="sim-revenue"></div></article><article>Coleta<div class="big" id="sim-collect"></div></article><article>Serviços<div class="big" id="sim-service"></div></article></div></section>
        <section><h2>Simular custo da seleção</h2><p>Uma linha por rota, base e mês. Confirme viagens e KM de ida; a distância cadastrada é uma estimativa rodoviária. Para seleção parcial, o custo representa a rota inteira.</p><div style="overflow:auto"><table><thead><tr><th>Rota / mês</th><th>Base</th><th>Destino</th><th>Receita selecionada</th><th>KM ida</th><th>R$/KM</th><th>Viagens</th></tr></thead><tbody id="sim-trips"></tbody></table></div><p><button id="sim-calculate">Calcular custo da seleção</button></p><div id="sim-result"></div></section>
        <script>const simData={{data|tojson}},simConfigs={{configs|tojson}},simTrips={{trips|tojson}};</script><script src="/static/cost-simulator.js?v=1"></script>""",data=data,configs=configs,trips=trips,general=general,operational=operational,basis=basis,year=year,month=month,months=[f'{i:02d}' for i in range(1,13)])

    @app.route('/cadastro-rotas',methods=['GET','POST'])
    def route_config():
        configs,_=load()
        if request.method=='POST':
            rid=request.form.get('id','')
            if rid not in configs:abort(400)
            c=configs[rid].copy()
            try:
                for k in ('rate','Sinop','Cuiabá'):
                    v=request.form.get(k,'').strip().replace(',','.')
                    if v and (not Decimal(v).is_finite() or Decimal(v)<0):raise ValueError()
                    if k=='rate' and not v:raise ValueError()
                    c[k]=v
            except (InvalidOperation,ValueError):abort(400)
            for k in ('type','cities','destination'):c[k]=request.form.get(k,'').strip()
            with engine.begin() as db:
                db.execute(text("INSERT INTO efi_route_config(id,payload) VALUES(:id,:p) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload"),{'id':rid,'p':json.dumps(c,ensure_ascii=False)})
            return redirect('/cadastro-rotas')
        return page("""<header><h1>Cadastro de rotas e custos</h1><div>Distâncias rodoviárias estimadas por base</div></header><main><section><div class="filters"><button id="all">Calcular todas as rotas pendentes</button><button id="stop">Pausar</button><input id="search" placeholder="Buscar placa ou cidade"><a href="/faturamento-rotas">Ver rentabilidade</a></div><p id="progress">Pronto. Distâncias existentes são preservadas.</p><small>Destino mais distante por base. Estimativa OSRM para automóveis, sem garantia de restrições de caminhões. Rotas locais precisam de KM urbano informado manualmente.</small></section><section style="overflow:auto"><table id="routes"><thead><tr><th>Rota / tipo</th><th>Cidades</th><th>R$/KM</th><th>Sinop: destino / ida</th><th>Cuiabá: destino / ida</th><th>Ações</th></tr></thead><tbody>{% for id,c in configs.items() %}<tr data-route="{{id}}"><td><b>{{id}}</b><br>{{c.type}}</td><td style="max-width:260px"><details><summary>{{c.cities.split(',')|length if c.cities else 0}} cidades</summary>{{c.cities}}</details></td><td>{{money(c.rate)}}</td>{% for base in ['Sinop','Cuiabá'] %}<td>{{c.get('destination_'+base,c.destination) or 'Pendente'}}<br><b>{{c[base] or '—'}} km</b></td>{% endfor %}<td><button class="calc" data-id="{{id}}">Calcular</button><details><summary>Editar</summary><form method="post"><input type="hidden" name="id" value="{{id}}"><label>Tipo <input name="type" value="{{c.type}}"></label><label>R$/KM <input name="rate" value="{{c.rate}}"></label><label>Cidades <textarea name="cities">{{c.cities}}</textarea></label><label>Destino <input name="destination" value="{{c.destination}}"></label><label>KM Sinop <input name="Sinop" value="{{c.Sinop}}"></label><label>KM Cuiabá <input name="Cuiabá" value="{{c['Cuiabá']}}"></label><button>Salvar</button></form></details></td></tr>{% endfor %}</tbody></table></section><script>
const configs={{configs|tojson}};let running=false,stop=false;
const status=document.getElementById('progress');
async function post(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok||j.status==='error')throw Error(j.message||r.status);return j;}
async function run(ids){if(running)return;running=true;stop=false;let ok=0,errors=0;for(const id of ids){for(const base of ['Sinop','Cuiabá']){if(stop)break;const c=configs[id];if(c[base])continue;const cities=c.cities.split(',').map(x=>x.trim()).filter(x=>x&&!/^espor[aá]dico$/i.test(x));if(!cities.length)continue;try{for(const city of cities){if(stop)break;status.textContent=id+' · '+base+' · '+city+' | concluídas '+ok+' | erros '+errors;await post('/cadastro-rotas/calcular',{id,base,city});}if(stop)break;const j=await post('/cadastro-rotas/concluir',{id,base});c[base]=String(j.km);const row=document.querySelector('[data-route="'+id+'"]');row.cells[base==='Sinop'?3:4].textContent=j.destination+' · '+j.km+' km';ok++;}catch(e){errors++;status.textContent=id+' '+e.message;}}if(stop)break;}running=false;status.textContent=(stop?'Pausado':'Finalizado')+' · '+ok+' bases calculadas · '+errors+' erros. Reexecute para retomar pendências.';}
document.getElementById('all').onclick=()=>run(Object.keys(configs));document.getElementById('stop').onclick=()=>stop=true;document.querySelectorAll('.calc').forEach(b=>b.onclick=()=>run([b.dataset.id]));document.getElementById('search').oninput=e=>document.querySelectorAll('#routes tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase()));
</script>""",configs=configs)
    @app.route('/faturamento-rotas',methods=['GET','POST'])
    def route_revenue():
        configs,trips=load()
        year=request.args.get('year','2026')
        month=request.args.get('month','')
        if len(year)!=4 or not year.isdigit():abort(400)
        if month and month not in [f'{i:02d}' for i in range(1,13)]:abort(400)
        if request.method=='POST':
            key=request.form.get('id','')
            try:n=int(request.form.get('trips',''))
            except ValueError:abort(400)
            if not key or len(key)>250 or n<1 or n>10000:abort(400)
            with engine.begin() as db:
                db.execute(text("INSERT INTO efi_route_trips(id,trips) VALUES(:id,:n) ON CONFLICT(id) DO UPDATE SET trips=excluded.trips"),{'id':key,'n':n})
            return redirect('/faturamento-rotas?year='+year+'&month='+month)
        index=defaultdict(list)
        for rid,c in configs.items():
            for city in c['cities'].split(','):
                if norm(city) not in ('','ESPORADICO'):index[(norm(c['plate']),norm(city))].append(rid)
        groups=defaultdict(lambda:Decimal(0))
        with engine.connect() as db:
            rows=db.execute(text("SELECT plate,city,transporter,operation_date,amount FROM revenue WHERE operation_date LIKE :period AND UPPER(TRIM(origin)) IN ('COLETA','SERVIÇO','SERVICO') AND COALESCE(schedule_id,'')<>''"),{'period':year+('-'+month if month else '')+'%'}).all()
        for p,city,t,day,amount in rows:
            base='Sinop' if str(t) in ('1','2','3','6') else 'Cuiabá' if str(t)=='5' else ''
            if not base:continue
            try:d=date.fromisoformat(day[:10])
            except (ValueError,TypeError):continue
            matches=index[(norm(p),norm(city))]
            if len(matches)!=1:continue
            rid=matches[0]
            groups[(rid,base,d.strftime('%Y-%m'))]+=Decimal(str(amount or 0))
        result=[]
        for (rid,base,period),rev in sorted(groups.items()):
            c=configs[rid]
            key=rid+'|'+base+'|'+period
            n=trips.get(key)
            km=Decimal(c[base])*2*n if c[base] and c.get('destination_'+base,c['destination']) and n and Decimal(c[base])>0 else None
            cost=km*Decimal(c['rate']) if km is not None else None
            result.append(dict(id=key,route=rid,base=base,period=period,type=c['type'],destination=c.get('destination_'+base,c['destination']),rate=c['rate'],trips=n,km=km,revenue=rev,cost=cost,net=rev-cost if cost is not None else None,margin=(rev-cost)*100/rev if cost is not None and rev else None))
        comparable=[r for r in result if r['cost'] is not None]
        return page("""<header><h1>Faturamento × Custo por rota</h1><div>Período da operação · custo rodoviário estimado</div></header><main><section><form class="filters"><input name="year" value="{{year}}" size="5"><select name="month"><option value="">Todos os meses</option>{% for m in months %}<option {{'selected' if m==month else ''}}>{{m}}</option>{% endfor %}</select><button>Filtrar</button><a href="/cadastro-rotas">Cadastro de rotas e custos</a></form><p>Somente vínculos inequívocos por placa e cidade. Confirme a quantidade de viagens por rota, base e mês; não se presume uma viagem por coleta. Custos pendentes não entram na margem.</p></section><section class="metrics"><article>Receita vinculada<div class="big">{{money(total)}}</div></article><article>Receita comparável<div class="big">{{money(comp)}}</div></article><article>Custo estimado comparável<div class="big">{{money(cost) if comparable else 'Pendente'}}</div></article><article>Resultado comparável<div class="big">{{money(comp-cost) if comparable else 'Pendente'}}</div></article></section><section><h2>Rotas por mês operacional</h2><div style="overflow:auto"><table><tr>{% for h in ['Mês','Rota','Base','Tipo','Destino','R$/KM','Viagens','KM total','Receita','Custo','Resultado','Margem'] %}<th>{{h}}</th>{% endfor %}</tr>{% for r in result %}<tr><td>{{r.period}}</td><td>{{r.route}}</td><td>{{r.base}}</td><td>{{r.type}}</td><td>{{r.destination or 'Pendente'}}</td><td>{{money(r.rate)}}</td><td><form method="post"><input type="hidden" name="id" value="{{r.id}}"><input type="number" name="trips" min="1" max="10000" value="{{r.trips or ''}}" required style="width:65px"><button>OK</button></form></td><td>{{r.km if r.km is not none else '—'}}</td><td>{{money(r.revenue)}}</td><td>{{money(r.cost) if r.cost is not none else 'Pendente'}}</td><td>{{money(r.net) if r.net is not none else '—'}}</td><td>{{'%.1f%%'|format(r.margin) if r.margin is not none else '—'}}</td></tr>{% endfor %}</table></div></section><section><h2>Receita × custo estimado</h2>{% for r in comparable %}<p>{{r.route}} · {{r.period}} · {{r.base}}</p>{% for v in [r.revenue,r.cost] %}<div class="barrow"><span>{{'Receita' if loop.first else 'Custo'}}</span><b>{{money(v)}}</b></div><div class="barbg"><div class="bar" style="width:{{100*(v|float|abs)/peak}}%;background:{{'#2466ad' if loop.first else '#e5a143'}}"></div></div>{% endfor %}{% endfor %}</section>""",year=year,month=month,months=[f'{i:02d}' for i in range(1,13)],result=result,comparable=comparable,total=sum(r['revenue'] for r in result),comp=sum(r['revenue'] for r in comparable),cost=sum(r['cost'] for r in comparable),peak=max([abs(float(v)) for r in comparable for v in (r['revenue'],r['cost'])]+[1]))
