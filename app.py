import os
import csv
import io
import json
import hmac
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
db_url = os.getenv("DATABASE_URL", "sqlite:///efi.db").replace("postgres://", "postgresql://", 1)
engine = create_engine(db_url, pool_pre_ping=True)

FIELDS = ["item_id", "billing_id", "bm_id", "billing_date", "operation_date",
          "branch", "client", "generator", "city", "state", "origin",
          "product", "service", "unit", "collected", "quantity", "amount",
          "vehicle_id", "plate", "driver", "transporter", "schedule_id", "collect_id", "scheduled_date", "attendance_start", "attendance_end", "due_date"]
NUMERIC = {"collected", "quantity", "amount"}
FILTERS = ["branch", "state", "city", "plate", "driver", "transporter", "origin", "product", "service"]
DIMS = {
    "city": "COALESCE(NULLIF(r.city,''),'Não identificada')",
    "state": "COALESCE(NULLIF(r.state,''),'Não identificado')",
    "region": "COALESCE(NULLIF(rg.region,''),'Não classificada')",
    "branch": "CASE WHEN UPPER(TRIM(r.branch)) IN ('CANAÃ NORTE RESÍDUOS LTDA','CANAÃ NORTE RESÍDUOS LTDA.','CANAÃ NORTE RESIDUOS LTDA','CANAÃ NORTE RESIDUOS LTDA.','ECOSUPPLY RECICLADORA DO NORTE LTDA','ECOSUPPLY RECICLADORA DO NORTE LTDA.') THEN 'ECOSUPPLY RECICLADORA DO NORTE LTDA.' ELSE COALESCE(NULLIF(r.branch,''),'Não identificada') END",
    "client": "COALESCE(NULLIF(r.client,''),'Não identificado')",
    "generator": "COALESCE(NULLIF(r.generator,''),'Não identificada')",
    "origin": "COALESCE(NULLIF(r.origin,''),'Não identificada')",
    "product": "COALESCE(NULLIF(r.product,''),'Não identificado')",
    "service": "COALESCE(NULLIF(r.service,''),'Não identificado')",
    "plate": "COALESCE(NULLIF(r.plate,''),'Sem vínculo')",
    "vehicle_type": "COALESCE(NULLIF(f.vehicle_type,''),NULLIF(fp.vehicle_type,''),'Não classificado')",
    "driver": "COALESCE(NULLIF(r.driver,''),'Sem vínculo')",
    "transporter": "CASE (CASE WHEN r.transporter='1' THEN '6' ELSE r.transporter END) WHEN '5' THEN '5 · ECOSUPPLY RECICLADORA LTDA.' WHEN '6' THEN '6 · ECOSUPPLY RECICLADORA DO NORTE LTDA.' ELSE COALESCE(NULLIF(r.transporter,''),'Sem vínculo') END",
}
LABELS = {"city":"Cidade", "state":"UF", "region":"Região", "branch":"Filial",
          "client":"Cliente", "generator":"Geradora", "origin":"Origem",
          "product":"Produto", "service":"Serviço", "plate":"Placa",
          "vehicle_type":"Tipo de veículo", "driver":"Motorista", "transporter":"Transportadora"}

def norm_plate(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

def setup_database():
    with engine.begin() as db:
        db.execute(text("CREATE TABLE IF NOT EXISTS sisgr_detail_cache (cache_key VARCHAR(200) PRIMARY KEY, payload TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL)"))
        db.execute(text("CREATE TABLE IF NOT EXISTS fleet (vehicle_id VARCHAR(40) PRIMARY KEY, plate VARCHAR(20) NOT NULL, vehicle_type VARCHAR(100) NOT NULL)"))
        db.execute(text("CREATE TABLE IF NOT EXISTS fleet_plates (plate VARCHAR(20) PRIMARY KEY, vehicle_type VARCHAR(100) NOT NULL)"))
        db.execute(text("CREATE TABLE IF NOT EXISTS regions (city VARCHAR(200) NOT NULL, state VARCHAR(5) NOT NULL, region VARCHAR(150), PRIMARY KEY(city,state))"))
        db.execute(text("CREATE TABLE IF NOT EXISTS revenue (item_id VARCHAR(150) PRIMARY KEY, billing_id VARCHAR(80) NOT NULL, bm_id VARCHAR(80), billing_date VARCHAR(10) NOT NULL, operation_date VARCHAR(10), branch VARCHAR(150), client VARCHAR(250), generator VARCHAR(250), city VARCHAR(150), state VARCHAR(5), origin VARCHAR(80), product VARCHAR(250), service VARCHAR(250), unit VARCHAR(30), collected NUMERIC(20,4), quantity NUMERIC(20,4), amount NUMERIC(20,2), vehicle_id VARCHAR(40), plate VARCHAR(20), driver VARCHAR(250), transporter VARCHAR(250), schedule_id VARCHAR(80), collect_id VARCHAR(80))"))
        from sqlalchemy import inspect
        existing = {c["name"] for c in inspect(db).get_columns("revenue")}
        for column in ("scheduled_date", "attendance_start", "attendance_end", "due_date"):
            if column not in existing:
                db.execute(text("ALTER TABLE revenue ADD COLUMN " + column + " VARCHAR(40)"))
        source = ROOT / "data" / "veiculos_exportados.json"
        if source.exists():
            vehicles = json.loads(source.read_text(encoding="utf-8"))["vehicles"]
            by_plate = {}
            for vehicle in vehicles:
                item = {"id":str(vehicle["id"]), "plate":norm_plate(vehicle["place_number"]), "type":str(vehicle["vehicle_type"]).strip()}
                db.execute(text("INSERT INTO fleet(vehicle_id,plate,vehicle_type) VALUES(:id,:plate,:type) ON CONFLICT(vehicle_id) DO UPDATE SET plate=excluded.plate,vehicle_type=excluded.vehicle_type"), item)
                if item["plate"] in by_plate and by_plate[item["plate"]] != item["type"]:
                    raise ValueError("Classificação divergente para placa " + item["plate"])
                by_plate[item["plate"]] = item["type"]
            for plate, vehicle_type in by_plate.items():
                db.execute(text("INSERT INTO fleet_plates(plate,vehicle_type) VALUES(:plate,:type) ON CONFLICT(plate) DO UPDATE SET vehicle_type=excluded.vehicle_type"), {"plate":plate,"type":vehicle_type})
        source = ROOT / "data" / "regioes.csv"
        if source.exists():
            with source.open(encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file, delimiter=";"):
                    if not row.get("Cidade") or not row.get("UF"):
                        continue
                    db.execute(text("INSERT INTO regions(city,state,region) VALUES(:city,:state,:region) ON CONFLICT(city,state) DO UPDATE SET region=excluded.region"), {
                        "city":row["Cidade"].strip().upper(), "state":row["UF"].strip().upper(), "region":(row.get("Região") or "").strip()})

setup_database()

@app.before_request
def require_password():
    if request.path == "/health":
        return
    expected = os.getenv("APP_PASSWORD")
    if not expected:
        return "Configure APP_PASSWORD no serviço antes de utilizar o EFI.", 503
    supplied = (request.authorization.password if request.authorization else "") or ""
    if not hmac.compare_digest(supplied, expected):
        return Response("Autenticação necessária", 401, {"WWW-Authenticate": 'Basic realm="Ecosupply EFI"'})

def conditions():
    year = request.args.get("year", "2026").strip()
    if len(year) != 4 or not year.isdigit():
        year = "2026"
    sql = ["r.billing_date LIKE :year"]
    params = {"year": year + "%"}
    month = request.args.get("month", "")
    if month in {f"{n:02d}" for n in range(1,13)}:
        sql.append("substr(r.billing_date,6,2)=:month")
        params["month"] = month
    for field in FILTERS:
        value = request.args.get(field, "")
        if value:
            sql.append("r.transporter IN ('1','6')" if field=="transporter" and value in ("1","6") else (DIMS["branch"] if field=="branch" else "r."+field) + "=:" + field)
            if not (field=="transporter" and value in ("1","6")):params[field] = value
    return " AND ".join(sql), params, year

def base_query(where):
    return (" FROM revenue r LEFT JOIN fleet f ON r.vehicle_id=f.vehicle_id"
            " LEFT JOIN fleet_plates fp ON r.plate=fp.plate"
            " LEFT JOIN regions rg ON UPPER(TRIM(r.city))=rg.city AND UPPER(TRIM(r.state))=rg.state"
            " WHERE " + where)

def money(value):
    return "R$ " + f"{float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def number(value):
    return f"{float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def map_data(db,base,params):
    rows=db.execute(text("SELECT r.city,r.state,SUM(r.amount),SUM(CASE WHEN UPPER(r.unit)='KG' THEN r.collected ELSE 0 END),SUM(CASE WHEN UPPER(r.unit)='L' THEN r.collected ELSE 0 END)"+base+" GROUP BY 1,2"),params)
    return [{"city":r[0],"state":r[1],"amount":float(r[2] or 0),"kg":float(r[3] or 0),"liters":float(r[4] or 0)} for r in rows]

HTML = """
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EFI • Inteligência de Faturamento</title>
<style>
:root{font-family:system-ui,Arial;color:#183a31;background:#f3f6f5}
*{box-sizing:border-box}body{margin:0}header{background:#075d48;color:white;padding:22px 5%}
main{max-width:1280px;margin:auto;padding:24px}section{background:#fff;border:1px solid #dbe8e3;border-radius:14px;margin:18px 0;padding:22px}
h1{font-size:24px;margin:0}h2{font-size:19px;margin:0 0 14px}small,.muted{color:#60776e}
.filters{display:flex;flex-wrap:wrap;gap:8px}.filters select,.filters input,button{padding:9px;border-radius:7px;border:1px solid #c7d9d1}
button{background:#08795e;color:#fff;cursor:pointer}.metrics{display:flex;flex-wrap:wrap;gap:16px}
.metrics article{flex:1;min-width:180px;padding:15px;background:#f4faf7;border-radius:8px}
.big{font-size:26px;font-weight:700}.bars{display:grid;gap:7px}.barbg{height:11px;background:#e4efea;border-radius:8px;overflow:hidden}
.bar{height:100%;background:#108b6e}.barrow{display:flex;justify-content:space-between;gap:12px;font-size:13px}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #e1e8e5;font-size:14px}
a{color:#08795e}.upload{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
aside{position:fixed;inset:0 auto 0 0;width:245px;background:#103d35;color:white;padding:24px 18px}aside a{display:block;color:white;text-decoration:none;padding:12px;border-radius:8px}aside a:hover{background:#286052}.brandmark{display:inline-block;background:#fff;color:#08795e;border-radius:12px;padding:5px 12px;font-size:32px;font-weight:800}.brand{font-size:23px;font-weight:700}body>header,main{margin-left:245px}main{max-width:none}section{scroll-margin-top:15px}@media(max-width:800px){aside{position:static;width:auto}body>header,main{margin-left:0}}
aside{background:#041321;overflow-y:auto}aside .brand img{width:100%;max-width:180px;display:block;margin:auto;mix-blend-mode:normal}aside p{text-align:center;color:#a9c3d9;font-size:12px;letter-spacing:1.5px}aside a{color:#dce8f2}aside a:hover,aside a:focus{background:#18354e}body>header{background:#fff;color:#183044;border-bottom:1px solid #dce5ed}:root{background:#f3f6f8;color:#183044}
aside .brand img{mask-image:radial-gradient(ellipse 75% 75% at center,#000 54%,transparent 100%);max-width:195px}aside{background:#061522}button{background:#2466ad}a{color:#2466ad}.monthly-chart{display:grid;grid-template-columns:repeat(12,minmax(64px,1fr));gap:10px;min-width:780px}.month-col{text-align:center;font-size:12px}.month-track{height:220px;display:flex;align-items:flex-end;justify-content:center;background:repeating-linear-gradient(to top,#e5edf5 0px,#e5edf5 1px,transparent 1px,transparent 55px);border-bottom:1px solid #b8cce0}.month-fill{width:65%;background:#2466ad;border-radius:5px 5px 0 0}.month-value{font-size:11px;min-height:32px;overflow-wrap:anywhere}.category-tabs{display:flex;gap:12px;margin:12px 0}.category-tabs a{padding:10px;border:1px solid #ccdbea;border-radius:7px}.category-tabs a.active{background:#2466ad;color:white}.category-bar{height:8px;background:#2466ad;border-radius:4px}
</style></head><body><aside><div class="brand"><img alt="Ecosupply" src="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABsSFBcUERsXFhceHBsgKEIrKCUlKFE6PTBCYFVlZF9VXVtqeJmBanGQc1tdhbWGkJ6jq62rZ4C8ybqmx5moq6T/2wBDARweHigjKE4rK06kbl1upKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKT/wAARCACIAJYDASIAAhEBAxEB/8QAGgABAAMBAQEAAAAAAAAAAAAAAAEEBQMCBv/EADgQAAEDAgQDBgUCBQUBAAAAAAEAAgMEEQUSITETQVEiUmFxgZEUI6Gx0TLBBiQzYvA0QnJzouH/xAAXAQEBAQEAAAAAAAAAAAAAAAAAAQID/8QAIxEBAQEBAAIBAgcAAAAAAAAAAAERAiExEgMTMkFRYXGh8f/aAAwDAQACEQMRAD8A+bREWkEREBERARWqahlnAcbMjJsHO5+Q3K0IsNpmwySvIc2L9Re63/kflWSprFRfQOpGRUskz6VrQwNNjFuD0NylTRUzHmMxse9rM8gY0jIPEgn7K/FNfPotSowoN/pvyGwIa8ggg7aj97LPlifC8skaWuHIqZi65opRQQilEEIpRBCKUQQiIiilERBaWG0BkHGfGX6FzWWvcDmfD7qpRQCecB18jRmfboFuCrqKWsZT07MzjYytY25/4jwA0WpEtRSVULzJFURktlGRr2tJcD08PII2UQUpp6wl7TcFjdSemuwtZdqp8Layf4a7S8/MI69B+6qOq2NuGnMRyCus7jzJW5qUxuinfEbAF8nIbDZe4sRhNSZjxYHPaGyFtnhw9QqzpJpLnNYHkNlwdEQuk5/VNrcppoPiZa2oySMdZjC3VrG25g6j2VN8Ta6N3yS2O5yZRcsG/p5eyz4JX08oe3yIOxHQrZmq5KXD2TUb7NLxna83LegHhos2ZcWXXzlRA+nlLH26gjYjqFzW7iMPxdI2obEYw8F8Y6W3HlzHqsJYsalERFFEREBERBClQpUBERBqYXaKnfMbau5i+jdfuQtV2Kh8clQxkZkjiLc9u1e4APr08FSwio+Fgil4Rl/UMo3Ny1dMQlEtNUvbA6EF8fZcwNPNdJGWfBM5twdc2+q7RxNJuNAq0e6vN/paeq3148sR0j4Vw06eJXSals24C4RC7xdakMJkjyhc53fzanlgzR5VoYVUWcG5Q8PaWFrtQTyXHEIjDKWO3Cp0znNrIS0kfMb9106nyieq1JsQlneIXxRRNi1EbTc3Av8Aa49VgVUXBqZY+TXEBfQCoZFxII6FgzFw4ua5ykkX6rDxH/WvJ52+wXO+mp7VkRSstIRSiCEUoghERQERFRsYRM9lM4xG0jHEDS/6h+QPdXql8leyoZGJuGIwTxRbtjp5i+iwqCYRT2cbMeMpPTofQ2K+hijNXK01FRI+fMTHEDZrSNdT9vBanrUYDDYq7TyDZ2oXispyQ6rhjyxF5a4DXI7mPLoq7HkLt7jn6aLS0TdnZacdWynjzE+Q6rFEgawOJ3XOWoL9yuM52tS49VcxkeXONyTcrzh4vVCQtzNhBkcPAa/ey42dI8NaC5xNgBuVpUsbYMtPHUcOre7Ull2O/sv/AJqut8RJ5qwTKxroKmljjL2l8RY4mxJtrr4r5+tkEtXK8bFxt5LZxCpMcb5ZIWRz6tdlN7u2+g19QsBcq3EKURZUREQEREEIiICIiAtWgq2TBsU+r26NObLnHS/I9D6LKUqy4j6KWRvyoKOHIcxY0Enc83cvQrhXUtOyeSPMI5GFo7Haa4kchuFSpMSkhLeJd2X9L2mzm+vP1VyOop5ZmzNkYXB/Eyu7Fz4jb6hblS/umXCapjY2vfCMx7GZ1ifdc34XLEWcaRgDrkZDm2312Hurte+nr545XiVuXRwa5rgR4a6L26sghofhYWljB/uke2/7ptTI4wUz4aL46kAAaQddXOAOo8PRdq6eNzY3h3Djis5rCwfKPnzPh7rOdibYI3xxPe4ONy1pIBPnv7WWdPUSTkZzoNmjQBS1XqrqTUSX1DBo0E/5quCIsKIiICIiAiIghERFERSghFKICBFtfG0BdTl2Qwty3h4OrTbUk89UGKi3fjqFmdw4b5eG0F/BsHOzakC2mirYrVUlTDaAMDmzOIysy9gjT6qaMtFr4hUUEmGtihc10rXNIPDym1tRt1WQqCIiIIiICIioIpRB5REUVv0MsbqSLLghnsLGQAdoj0Uz1tLTFonwMR5tswAv9FZwx7Gfw210k76doee2wXI7S40r4anG6ZoqpapjWucDKLWcsql00TYuK7+HyGWvcgbey4MxKhkdljwZjj0FvwvU+I4o3GaiOAOkIJAiIuLdbLxhwBzBzA1xJc5o01udFZNS3HqTEaKK3EwVjb7Xtr9EZiFG4ZhgrCOun4U4tFCyJzW2ILM1r7FU+IH5BmfE+2gtoVnq56dfo8zvdWhilC++XBmG2+2n0RuKUJBLcHjIG9rafRUog5j6gON3W3HNcqUE08x8P2U+Tp9nzJ/P9NQYhRuAcMFYQfL8LyMToS7IMGZmHLT8Km11qWK8nDtz6qWyMlrLs1AZa/VT5Vr7HPjz7xbdiNEwXdgrAOpt+EdidA0AuwdgB2vbX6Kk+VkcUjXS8Qu2HRcqrWCDy/ZWdM9fS5ktl9NI4nQhoccHZlOx0/Cr19bTTUhbFhrYHOItILfhV6RweDC8XB1C51cvElsNGt0AVlu4xeOZx89/1XUoi6OAiIg8qVCKK+lwxmLRYfE2nFKYnDM3OdddUqqHGaqWKVxpo3xG7TG6y+cDnDYn3TM7vH3Uw19Y4Y45hH8o15FjID2lnR4JiUbrh0Jub6yc1iZnd4+6Znd4+6s2DblwTEpdHGED/s3XtuEYmAL/AA5I2JdqsHM7vH3TM7vH3Us321z3efwtpuB4kJTJmhzHft//ABe34NiT2Fv8u0Hez91hZnd4+6nM7vH3U+K/c6zNbDsBxF8bWHgWbt20iwHEInZmmC9ravWPmd3j7pmd3j7q4nzu61nfw7XuJN4df712bg+JtaG/y+gsLuWHmd3j7pmd3j7pZpz3efTabgmIiYy/IzH+/RVK7CKukhdPNw8t9crr7qhmd3j7oSTuT7pJiXq3xUIiLTIiIg8oiKKlERARERBERAREVEoiIIUoiAiIgIiICIiD/9k="></div><p>INTELIGÊNCIA FINANCEIRA</p><nav><a href="#overview">▦ Visão geral</a><a href="#conciliation">⇄ Conciliação</a><a href="{{url_for('index',**args.to_dict(flat=True))}}#branch-view">▤ Faturamento por filial</a><a href="{{url_for('index',**args.to_dict(flat=True))}}#transporter-view">▤ Faturamento por transportador</a><a href="#ranking">▥ Faturamento e rankings</a><a href="{{url_for('categories',**args.to_dict(flat=True))}}">♻ Resíduos e serviços</a><a href="#map">◈ Mapas e regiões</a><a href="#audit">✓ Auditoria</a><a href="/sisgr/mensal">↻ Importar SISGR</a></nav></aside>
<header><h1>ECOSUPPLY | EFI</h1><div>Inteligência de Faturamento • Dados efetivamente importados</div></header>
<main><section id="overview"><form class="filters" method="get">
<input name="year" value="{{year}}" size="5" aria-label="Ano">
<select name="month"><option value="">Todos os meses</option>{% for m in months %}<option value="{{m}}" {% if args.get('month')==m %}selected{% endif %}>{{m}}</option>{% endfor %}</select>
{% for k, values in options.items() %}<select name="{{k}}"><option value="">{{labels[k]}}: todos</option>{% for value in values %}<option value="{{value}}" {% if args.get(k)==value %}selected{% endif %}>{{"6 · ECOSUPPLY RECICLADORA DO NORTE LTDA." if k=="transporter" and value=="6" else "5 · ECOSUPPLY RECICLADORA LTDA." if k=="transporter" and value=="5" else value}}</option>{% endfor %}</select>{% endfor %}
<select name="dim">{% for k in dims %}<option value="{{k}}" {% if dim==k %}selected{% endif %}>Por {{labels[k]}}</option>{% endfor %}</select>
<button>Aplicar filtros</button></form></section>
<section><div class="metrics"><article><div class="muted">Receita atribuída aos itens</div><div class="big">{{money(total)}}</div></article>
<article><div class="muted">Faturamentos</div><div class="big">{{bills}}</div></article>
<article><div class="muted">Itens do BM / receita</div><div class="big">{{count}}</div></article>
<article><div class="muted">Itens sem placa</div><div class="big">{{missing}}</div></article></div>
{% if count == 0 %}<p class="muted">Nenhum faturamento importado. A base financeira está vazia; não são exibidos valores fictícios.</p>{% endif %}</section>
<section><h2>Faturamento por mês — {{year}}</h2><div style="overflow-x:auto"><div class="monthly-chart">{% for m,v in monthly %}<div class="month-col"><div class="month-value">{{money(v)}}</div><div class="month-track"><div class="month-fill" title="{{money(v)}}" style="height:{{pct(v,peak) if v>0 else 0}}%"></div></div><b>{{['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'][loop.index0]}}</b></div>{% endfor %}</div></div><small>Valores negativos, se existentes, são exibidos no rótulo.</small></section>
<section id="conciliation"><h2>Filial faturadora × transportadora</h2><p class="muted">Receita dos mesmos itens em duas perspectivas; não somar os totais entre si. Empresa faturadora conforme cadastro SISGR.</p><div class="metrics">{% for title,groups in [('Faturamento por filial',branches),('Receita por transportadora',transporters)] %}<article id="{{'branch-view' if loop.first else 'transporter-view'}}"><h2>{{title}}</h2><table>{% for name,value in groups %}<tr><td>{{name}}</td><td>{{money(value)}}</td></tr>{% endfor %}</table></article>{% endfor %}</div><h2>Matriz de conciliação</h2><div style="overflow:auto"><table><tr><th>Faturadora</th>{% for t,v in transporters %}<th>{{t}}</th>{% endfor %}<th>Total</th></tr>{% for b,v in branches %}<tr><th>{{b}}</th>{% for t,tv in transporters %}<td>{{money(matrix.get((b,t),0))}}</td>{% endfor %}<th>{{money(v)}}</th></tr>{% endfor %}<tr><th>Total</th>{% for t,v in transporters %}<th>{{money(v)}}</th>{% endfor %}<th>{{money(total)}}</th></tr></table></div></section><section id="ranking"><h2>Faturamento por {{labels[dim]}}</h2><table><thead><tr><th>Grupo</th><th>Receita</th><th>Itens</th></tr></thead>
<tbody>{% for name,value,n in ranking %}<tr><td>{% if dim=="city" and name=="Não identificada" %}<a href="{{url_for('unknown_city',**args.to_dict(flat=True))}}">{{name}}</a>{% else %}{{name}}{% endif %}</td><td>{{money(value)}}</td><td>{{n}}</td></tr>{% endfor %}</tbody></table></section>
<section id="map"><h2>Mapa de faturamento — Mato Grosso</h2><select id="mapmode"><option value="city">Por cidade</option><option value="region">Por região</option></select><p id="mapinfo" class="muted">Selecione uma cidade no mapa.</p><svg id="revenue-map" viewBox="0 0 800 650" style="width:100%;max-height:650px"></svg><p class="muted">Malha da logística: MT. Demais estados permanecem nas tabelas.</p></section><script>const mapRows={{maprows|tojson}};</script><script src="/static/revenue-map.js"></script><section><h2>Volumes por unidade</h2><table><thead><tr><th>Unidade</th><th>Quantidade dos itens de coleta</th><th>Coletado</th></tr></thead><tbody>
{% for unit,quantity,collected in volumes %}<tr><td>{{unit or 'Não informada'}}</td><td>{{number(quantity)}}</td><td>{{number(collected)}}</td></tr>{% endfor %}</tbody></table></section>
<section id="audit"><h2>Auditoria e importação</h2><p><a href="/sisgr/mensal">Importação mensal — lotes de teste</a></p><p><a href="/sisgr/diagnostico">Testar conexão SISGR</a></p><form action="/sisgr/piloto" method="post"><button>Atualizar SISGR — prévia piloto</button></form><form action="/sisgr/piloto" method="post"><input type="hidden" name="save" value="1"><button>Gravar faturamento piloto 105595</button></form><p><a href="{{url_for('export_csv',**args.to_dict(flat=True))}}">Exportar CSV detalhado</a> · <a href="/fleet">Consultar frota oficial importada</a></p>
<p class="muted">O valor exibido é a soma dos itens importados, não uma confirmação do faturamento fiscal completo no SISGR.</p>
<form class="upload" action="/import/revenue" method="post" enctype="multipart/form-data"><label>CSV de itens faturados <input type="file" name="file" accept=".csv" required></label><button>Importar ou atualizar</button></form></section></main></body></html>
"""

@app.get("/")
def index():
    where, params, year = conditions()
    base = base_query(where)
    months = [f"{n:02d}" for n in range(1, 13)]
    dim = request.args.get("dim", "city")
    if dim not in DIMS:
        dim = "city"
    with engine.connect() as db:
        total, count, bills, missing = db.execute(text(
            "SELECT COALESCE(SUM(r.amount),0),COUNT(*),COUNT(DISTINCT r.billing_id),"
            "COALESCE(SUM(CASE WHEN r.plate IS NULL OR r.plate='' THEN 1 ELSE 0 END),0)"
            + base), params).one()
        monthly_map = {row[0]:row[1] for row in db.execute(text(
            "SELECT substr(r.billing_date,6,2),COALESCE(SUM(r.amount),0)" + base + " GROUP BY 1"),params)}
        ranking = db.execute(text("SELECT "+DIMS[dim]+",COALESCE(SUM(r.amount),0),COUNT(*)"
            +base+" GROUP BY 1 ORDER BY 2 DESC LIMIT 200"),params).all()
        volumes = db.execute(text("SELECT r.unit,SUM(CASE WHEN UPPER(r.origin)='COLETA' THEN r.quantity ELSE 0 END),SUM(r.collected)"+base+" AND UPPER(TRIM(r.unit)) IN ('KG','L','M3','M³','T','TON') AND UPPER(COALESCE(r.service,'')) NOT LIKE '%FRANQUIA%' GROUP BY 1"),params).all()
        maprows=map_data(db,base,params)
        pairs=db.execute(text("SELECT "+DIMS["branch"]+","+DIMS["transporter"]+",SUM(r.amount)"+base+" GROUP BY 1,2"),params).all()
        matrix={(b,t):v for b,t,v in pairs}
        branches=sorted([(b,sum(v for x,t,v in pairs if x==b)) for b in {x for x,t,v in pairs}])
        transporters=sorted([(t,sum(v for b,x,v in pairs if x==t)) for t in {x for b,x,v in pairs}])
        options = {field:[r[0] for r in db.execute(text("SELECT DISTINCT "+field+
                   " FROM revenue WHERE "+field+" IS NOT NULL AND "+field+"<>'' ORDER BY 1 LIMIT 400"))]
                   for field in FILTERS}
        options["branch"]=[r[0] for r in db.execute(text("SELECT DISTINCT "+DIMS["branch"]+" FROM revenue r ORDER BY 1"))]
        options["transporter"]=sorted({("6" if v=="1" else v) for v in options["transporter"]})
    monthly = [(m,monthly_map.get(m,0)) for m in months]
    peak = max([float(v) for _,v in monthly]+[1])
    return render_template_string(HTML, year=year, args=request.args, options=options,
                                  dims=DIMS,dim=dim,labels=LABELS,months=months,
                                  total=total,count=count,bills=bills,missing=missing,
                                  monthly=monthly,ranking=ranking,volumes=volumes,money=money,
                                  branches=branches,transporters=transporters,matrix=matrix,maprows=maprows,number=number,peak=peak,pct=lambda v,p:100*float(v)/p)

@app.get("/residuos-servicos")
def categories():
    where,params,year=conditions()
    base=base_query(where)
    kind=request.args.get("kind","product")
    if kind not in ("product","service"):kind="product"
    franchise="(UPPER(COALESCE(r.product,'')) LIKE '%FRANQUIA%' OR UPPER(COALESCE(r.service,'')) LIKE '%FRANQUIA%')"
    dimension="CASE WHEN "+franchise+" THEN 'Franquias' ELSE "+DIMS[kind]+" END"
    with engine.connect() as db:
        rows=db.execute(text("SELECT "+dimension+",COALESCE(SUM(r.amount),0),COUNT(*)"+base+" GROUP BY 1 ORDER BY 2 DESC"),params).all()
        options={field:[r[0] for r in db.execute(text("SELECT DISTINCT "+field+" FROM revenue WHERE "+field+" IS NOT NULL AND "+field+"<>'' ORDER BY 1 LIMIT 400"))] for field in FILTERS}
        options["branch"]=[r[0] for r in db.execute(text("SELECT DISTINCT "+DIMS["branch"]+" FROM revenue r ORDER BY 1"))]
        options["transporter"]=sorted({("6" if v=="1" else v) for v in options["transporter"]})
    total=sum(r[1] for r in rows)
    peak=max([abs(float(r[1])) for r in rows]+[1])
    args=request.args.to_dict(flat=True)
    args.pop("kind",None)
    head=HTML.split("<header>",1)[0].replace('href="#overview"','href="/#overview"').replace('href="#conciliation"','href="/#conciliation"').replace('href="#ranking"','href="/#ranking"').replace('href="#map"','href="/#map"').replace('href="#audit"','href="/#audit"')
    template=head+"""<header><h1>Resíduos e serviços</h1><div>Inteligência financeira por categoria</div></header><main><section><form class="filters" method="get"><input type="hidden" name="kind" value="{{kind}}"><input name="year" value="{{year}}" size="5"><select name="month"><option value="">Todos os meses</option>{% for m in months %}<option value="{{m}}" {% if args.get('month')==m %}selected{% endif %}>{{m}}</option>{% endfor %}</select>{% for k,values in options.items() %}<select name="{{k}}"><option value="">{{labels[k]}}: todos</option>{% for v in values %}<option value="{{v}}" {% if args.get(k)==v %}selected{% endif %}>{{v}}</option>{% endfor %}</select>{% endfor %}<button>Aplicar filtros</button></form><div class="category-tabs"><a class="{{'active' if kind=='product' else ''}}" href="{{url_for('categories',kind='product',**clean)}}">Tipos de resíduos</a><a class="{{'active' if kind=='service' else ''}}" href="{{url_for('categories',kind='service',**clean)}}">Tipos de serviços</a></div><div class="metrics"><article>Receita<div class="big">{{money(total)}}</div></article><article>Categorias<div class="big">{{rows|length}}</div></article><article>Itens<div class="big">{{items}}</div></article></div></section><section><h2>Ranking de {{'resíduos' if kind=='product' else 'serviços'}}</h2><p class="muted">Perspectivas alternativas da mesma receita. Franquias separadas; categorias sem cadastro permanecem identificadas como pendentes de classificação.</p><table><tr><th>Categoria</th><th>Receita</th><th>Participação</th><th>Itens</th><th>Comparativo</th></tr>{% for name,value,n in rows %}<tr><td>{{name}}</td><td>{{money(value)}}</td><td>{{number(value/total*100) if total else '0,00'}}%</td><td>{{n}}</td><td style="width:25%"><div class="category-bar" style="width:{{pct(value)}}%"></div></td></tr>{% endfor %}</table></section></main></body></html>"""
    return render_template_string(template,kind=kind,year=year,args=request.args,clean=args,months=[f"{n:02d}" for n in range(1,13)],options=options,labels=LABELS,rows=rows,total=total,items=sum(r[2] for r in rows),money=money,number=number,pct=lambda v:100*abs(float(v))/peak)

@app.get("/auditoria/cidades")
def unknown_city():
    where,params,year=conditions()
    with engine.connect() as db:
        rows=db.execute(text("SELECT r.billing_id,r.bm_id,r.item_id,r.client,r.service,r.transporter,r.schedule_id,r.collect_id,r.amount"+base_query(where)+" AND COALESCE(TRIM(r.city),'')='' ORDER BY r.amount DESC"),params).all()
    return render_template_string("""<meta charset="utf-8"><h1>Cidades não identificadas</h1><a href="/">Voltar</a><p>{{rows|length}} itens — {{money(total)}}</p><button id="repair">Reprocessar faturamentos sem cidade</button> <span id="progress"></span><pre id="repairlog"></pre><script>const ids={{ ids|tojson }};document.addEventListener("DOMContentLoaded",()=>{document.getElementById("repair").onclick=async()=>{const b=document.getElementById("repair"),p=document.getElementById("progress"),l=document.getElementById("repairlog");b.disabled=true;for(let i=0;i<ids.length;i++){p.textContent=(i+1)+" / "+ids.length;try{const r=await fetch("/auditoria/reprocessar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:ids[i]})});const j=await r.json();l.textContent+=ids[i]+": "+JSON.stringify(j)+String.fromCharCode(10)}catch(e){l.textContent+=ids[i]+": erro de comunicação"+String.fromCharCode(10)}}p.textContent="Concluído. Atualize a página para conferir.";b.disabled=false;}});</script><table border="1" cellpadding="7"><tr>{% for h in ['Faturamento','BM','Item','Cliente','Serviço','Transportador','Agendamento','Coleta','Valor'] %}<th>{{h}}</th>{% endfor %}</tr>{% for r in rows %}<tr>{% for v in r %}<td>{{money(v) if loop.last else v}}</td>{% endfor %}</tr>{% endfor %}</table>""",rows=rows,ids=list(dict.fromkeys(str(r[0]) for r in rows)),money=money,total=sum(r[-1] or 0 for r in rows))

@app.post("/auditoria/reprocessar")
def reprocess_city():
    from sisgr_sync import Client,build
    from sisgr_month import slots
    bid=str((request.get_json(silent=True) or {}).get("id",""))
    if not bid.isdigit():return jsonify(status="erro",motivo="ID inválido"),400
    with slots:
        try:
            with engine.connect() as db:
                if not db.execute(text("SELECT 1 FROM revenue WHERE billing_id=:id AND COALESCE(TRIM(city),'')='' LIMIT 1"),{"id":bid}).scalar():
                    return jsonify(status="ignorado")
            client=Client()
            bill=client.detail("billing",bid)
            rows,audit=build(client,[bill],FIELDS,lambda n,t:None)
            if not rows or audit["issues"] or abs(Decimal(audit["bm_total"])-Decimal(audit["item_total"]))>Decimal("0.01"):
                return jsonify(status="pendente",audit=audit)
            statement=text("INSERT INTO revenue ("+",".join(FIELDS)+") VALUES ("+",".join(":"+f for f in FIELDS)+") ON CONFLICT(item_id) DO UPDATE SET "+",".join(f+"=excluded."+f for f in FIELDS[1:]))
            with engine.begin() as db:
                if engine.dialect.name=="postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:id))"),{"id":bid})
                for row in rows:
                    row["plate"]=db.execute(text("SELECT plate FROM fleet WHERE vehicle_id=:id"),{"id":row["vehicle_id"]}).scalar() or ""
                db.execute(text("DELETE FROM revenue WHERE billing_id=:id"),{"id":bid})
                db.execute(statement,rows)
            return jsonify(status="gravado",sem_cidade=sum(not r["city"] for r in rows),audit=audit)
        except Exception as exc:
            return jsonify(status="erro",tipo=type(exc).__name__),502

@app.get("/geografia")
def geografia():
    return Response((ROOT/"data"/"geografia.json").read_text(encoding="utf-8"),mimetype="application/json")

@app.get("/sisgr/diagnostico")
def sisgr_diagnostico():
    from sisgr_probe import probe
    return jsonify(probe())

@app.get("/sisgr/filtros")
def sisgr_filtros():
    from sisgr_probe import test_filters
    return jsonify(test_filters())

@app.post("/sisgr/piloto")
def sisgr_piloto():
    from sisgr_sync import Client,build
    try:
        client=Client()
        bill=client.detail("billing",105595)
        rows,audit=build(client,[bill],FIELDS,lambda n,total:None)
        with engine.connect() as db:
            for row in rows:
                plate=db.execute(text("SELECT plate FROM fleet WHERE vehicle_id=:id"),{"id":row["vehicle_id"]}).scalar()
                row["plate"]=plate or ""
        if request.form.get("save")=="1":
            if audit["issues"] or not rows or Decimal(audit["bm_total"])!=Decimal(audit["item_total"]):
                return jsonify(audit=audit,status="Gravação bloqueada pela auditoria"),409
            statement=text("INSERT INTO revenue ("+",".join(FIELDS)+") VALUES ("+",".join(":"+f for f in FIELDS)+") ON CONFLICT(item_id) DO UPDATE SET "+",".join(f+"=excluded."+f for f in FIELDS[1:]))
            with engine.begin() as db:
                db.execute(text("DELETE FROM revenue WHERE billing_id=:id"),{"id":"105595"})
                db.execute(statement,rows)
            return redirect("/?year=2026&month=11")
        return jsonify(audit=audit,rows=rows,mode="preview; nenhum dado gravado")
    except Exception as exc:
        return jsonify(status="erro",tipo=type(exc).__name__,http=getattr(exc,"code",None)),502

@app.get("/sisgr/mensal")
def monthly_page():
    return render_template_string("""<meta charset="utf-8"><h1>Importação mensal SISGR</h1><a href="/">Voltar ao dashboard</a><p>Importação experimental em lotes de 1 faturamento. Pendências não são gravadas.</p><form method="post"><label>Mês <input type="month" name="month" value="2026-11" required></label><label>Posição inicial <input type="number" name="offset" min="0" value="0" required></label><button>Importar lote</button></form><hr><label>Velocidade <select id="workers"><option value="1">Econômico</option><option value="3">Equilibrado</option><option value="5">Rápido</option><option value="10">Turbo — 10</option></select></label> <label>Modo <select id="mode"><option value="missing">Somente ausentes</option><option value="full">Completo — reprocessar</option></select></label> <button id="start">Iniciar importação automática</button> <button id="pause">Pausar</button><p id="progress"></p><progress id="bar" value="0" max="1"></progress><pre id="issues" style="white-space:pre-wrap"></pre><script src="/static/month-sync.js?v=08"></script>""")

@app.post("/sisgr/lote")
def monthly_batch_api():
    from sisgr_month import run_batch
    try:
        month=request.form["month"]
        offset=int(request.form.get("offset",0))
        if len(month)!=7 or not month.startswith("2026-") or offset<0:raise ValueError()
        return jsonify(run_batch(engine,FIELDS,month,offset,mode=request.form.get("mode","full")))
    except Exception as exc:
        return jsonify(status="erro",tipo=type(exc).__name__,http=getattr(exc,"code",None)),502

@app.post("/sisgr/mensal")
def monthly_import():
    from sisgr_month import run_batch
    try:
        month=request.form["month"]
        if len(month)!=7 or not month.startswith("2026-"):raise ValueError()
        offset=int(request.form.get("offset",0))
        if offset<0:raise ValueError()
        result=run_batch(engine,FIELDS,month,offset)
        return render_template_string("""<meta charset="utf-8"><h1>Resultado do lote</h1><p>Posição {{r.next}} de {{r.total}} faturamentos listados</p><pre style="white-space:pre-wrap">{{r.results|tojson(indent=2)}}</pre>{% if r.next<r.total %}<form method="post"><input type="hidden" name="month" value="{{month}}"><input type="hidden" name="offset" value="{{r.next}}"><button>Importar próximo lote</button></form>{% endif %}<a href="/">Voltar ao dashboard</a>""",r=result,month=month)
    except Exception as exc:
        return jsonify(status="erro",tipo=type(exc).__name__,http=getattr(exc,"code",None)),502

@app.get("/health")
def health():
    with engine.connect() as db:
        db.execute(text("SELECT 1"))
    return jsonify(status="ok", service="EFI")

@app.get("/fleet")
def fleet():
    with engine.connect() as db:
        rows = db.execute(text("SELECT vehicle_id,plate,vehicle_type FROM fleet ORDER BY plate,vehicle_id")).all()
    return render_template_string("""<meta charset="utf-8"><h1>Frota Ecosupply</h1>
    <a href="/">Voltar ao painel</a><p>{{rows|length}} registros SISGR</p>
    <table border="1" cellpadding="8"><tr><th>ID SISGR</th><th>Placa</th><th>Tipo</th></tr>
    {% for id,plate,kind in rows %}<tr><td>{{id}}</td><td>{{plate}}</td><td>{{kind}}</td></tr>{% endfor %}</table>""",rows=rows)

@app.post("/import/revenue")
def import_revenue():
    file = request.files.get("file")
    if not file:
        return "Arquivo CSV obrigatório", 400
    try:
        reader = csv.DictReader(io.StringIO(file.read().decode("utf-8-sig")))
        if not reader.fieldnames or not {"item_id","billing_id","billing_date","amount"}.issubset(reader.fieldnames):
            raise ValueError("Cabeçalhos obrigatórios: item_id,billing_id,billing_date,amount")
        statement = text("INSERT INTO revenue ("+",".join(FIELDS)+") VALUES ("+
                         ",".join(":"+name for name in FIELDS)+") ON CONFLICT(item_id) DO UPDATE SET "+
                         ",".join(name+"=excluded."+name for name in FIELDS[1:]))
        with engine.begin() as db:
            for number, row in enumerate(reader, start=2):
                values = {field:(row.get(field) or "").strip() for field in FIELDS}
                if not values["item_id"] or not values["billing_id"]:
                    raise ValueError("IDs obrigatórios ausentes na linha "+str(number))
                date.fromisoformat(values["billing_date"])
                if values["attendance_end"]:
                    values["operation_date"] = values["attendance_end"][:10]
                if values["operation_date"]:
                    date.fromisoformat(values["operation_date"])
                values["plate"] = norm_plate(values["plate"])
                for field in NUMERIC:
                    try:
                        values[field] = str(Decimal(values[field] or "0"))
                    except InvalidOperation:
                        raise ValueError("Número inválido na linha "+str(number)+": "+field)
                db.execute(statement, values)
    except (ValueError, UnicodeDecodeError, csv.Error, KeyError) as exc:
        return "Importação rejeitada: "+str(exc), 400
    return redirect("/")

@app.get("/export")
def export_csv():
    where,params,_ = conditions()
    result = io.StringIO()
    writer = csv.writer(result)
    writer.writerow(FIELDS+["vehicle_type","region"])
    with engine.connect() as db:
        columns = ",".join("r."+field for field in FIELDS)
        rows = db.execute(text("SELECT "+columns+","+DIMS["vehicle_type"]+","+DIMS["region"]+
                               base_query(where)+" ORDER BY r.billing_date,r.item_id"),params)
        writer.writerows(rows)
    return Response("\ufeff"+result.getvalue(),mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=efi-faturamento.csv"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
