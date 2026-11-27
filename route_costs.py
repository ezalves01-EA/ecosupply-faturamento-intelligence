import json
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from flask import request, render_template_string, redirect, abort
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
        return page("""<header><h1>Cadastro de rotas e custos</h1></header><main><section><p>Informe o destino final confirmado e a distância rodoviária de ida, em km, para cada base. Não use distância em linha reta. Custo por KM pode ser ajustado por rota.</p><a href="/faturamento-rotas">Ver rentabilidade</a></section>{% for id,c in configs.items() %}<section><h2>{{id}}</h2><form method="post" class="filters"><input type="hidden" name="id" value="{{id}}"><label>Tipo <input name="type" value="{{c.type}}"></label><label>R$/KM <input name="rate" value="{{c.rate}}" required></label><label>Cidades <textarea name="cities" rows="3">{{c.cities}}</textarea></label><label>Destino final <input name="destination" value="{{c.destination}}"></label><label>KM ida Sinop <input name="Sinop" value="{{c.Sinop}}"></label><label>KM ida Cuiabá <input name="Cuiabá" value="{{c['Cuiabá']}}"></label><button>Salvar</button></form></section>{% endfor %}""",configs=configs)
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
            km=Decimal(c[base])*2*n if c[base] and c['destination'] and n else None
            cost=km*Decimal(c['rate']) if km is not None else None
            result.append(dict(id=key,route=rid,base=base,period=period,type=c['type'],destination=c['destination'],rate=c['rate'],trips=n,km=km,revenue=rev,cost=cost,net=rev-cost if cost is not None else None,margin=(rev-cost)*100/rev if cost is not None and rev else None))
        comparable=[r for r in result if r['cost'] is not None]
        return page("""<header><h1>Faturamento × Custo por rota</h1><div>Período da operação · custo rodoviário estimado</div></header><main><section><form class="filters"><input name="year" value="{{year}}" size="5"><select name="month"><option value="">Todos os meses</option>{% for m in months %}<option {{'selected' if m==month else ''}}>{{m}}</option>{% endfor %}</select><button>Filtrar</button><a href="/cadastro-rotas">Cadastro de rotas e custos</a></form><p>Somente vínculos inequívocos por placa e cidade. Confirme a quantidade de viagens por rota, base e mês; não se presume uma viagem por coleta. Custos pendentes não entram na margem.</p></section><section class="metrics"><article>Receita vinculada<div class="big">{{money(total)}}</div></article><article>Receita comparável<div class="big">{{money(comp)}}</div></article><article>Custo estimado comparável<div class="big">{{money(cost) if comparable else 'Pendente'}}</div></article><article>Resultado comparável<div class="big">{{money(comp-cost) if comparable else 'Pendente'}}</div></article></section><section><h2>Rotas por mês operacional</h2><div style="overflow:auto"><table><tr>{% for h in ['Mês','Rota','Base','Tipo','Destino','R$/KM','Viagens','KM total','Receita','Custo','Resultado','Margem'] %}<th>{{h}}</th>{% endfor %}</tr>{% for r in result %}<tr><td>{{r.period}}</td><td>{{r.route}}</td><td>{{r.base}}</td><td>{{r.type}}</td><td>{{r.destination or 'Pendente'}}</td><td>{{money(r.rate)}}</td><td><form method="post"><input type="hidden" name="id" value="{{r.id}}"><input type="number" name="trips" min="1" max="10000" value="{{r.trips or ''}}" required style="width:65px"><button>OK</button></form></td><td>{{r.km if r.km is not none else '—'}}</td><td>{{money(r.revenue)}}</td><td>{{money(r.cost) if r.cost is not none else 'Pendente'}}</td><td>{{money(r.net) if r.net is not none else '—'}}</td><td>{{'%.1f%%'|format(r.margin) if r.margin is not none else '—'}}</td></tr>{% endfor %}</table></div></section><section><h2>Receita × custo estimado</h2>{% for r in comparable %}<p>{{r.route}} · {{r.period}} · {{r.base}}</p>{% for v in [r.revenue,r.cost] %}<div class="barrow"><span>{{'Receita' if loop.first else 'Custo'}}</span><b>{{money(v)}}</b></div><div class="barbg"><div class="bar" style="width:{{100*(v|float|abs)/peak}}%;background:{{'#2466ad' if loop.first else '#e5a143'}}"></div></div>{% endfor %}{% endfor %}</section>""",year=year,month=month,months=[f'{i:02d}' for i in range(1,13)],result=result,comparable=comparable,total=sum(r['revenue'] for r in result),comp=sum(r['revenue'] for r in comparable),cost=sum(r['cost'] for r in comparable),peak=max([abs(float(v)) for r in comparable for v in (r['revenue'],r['cost'])]+[1]))
