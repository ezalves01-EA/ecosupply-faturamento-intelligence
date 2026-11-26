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
    "branch": "COALESCE(NULLIF(r.branch,''),'Não identificada')",
    "client": "COALESCE(NULLIF(r.client,''),'Não identificado')",
    "generator": "COALESCE(NULLIF(r.generator,''),'Não identificada')",
    "origin": "COALESCE(NULLIF(r.origin,''),'Não identificada')",
    "product": "COALESCE(NULLIF(r.product,''),'Não identificado')",
    "service": "COALESCE(NULLIF(r.service,''),'Não identificado')",
    "plate": "COALESCE(NULLIF(r.plate,''),'Sem vínculo')",
    "vehicle_type": "COALESCE(NULLIF(f.vehicle_type,''),NULLIF(fp.vehicle_type,''),'Não classificado')",
    "driver": "COALESCE(NULLIF(r.driver,''),'Sem vínculo')",
    "transporter": "COALESCE(NULLIF(r.transporter,''),'Sem vínculo')",
}
LABELS = {"city":"Cidade", "state":"UF", "region":"Região", "branch":"Filial",
          "client":"Cliente", "generator":"Geradora", "origin":"Origem",
          "product":"Produto", "service":"Serviço", "plate":"Placa",
          "vehicle_type":"Tipo de veículo", "driver":"Motorista", "transporter":"Transportadora"}

def norm_plate(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

def setup_database():
    with engine.begin() as db:
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
            sql.append("r." + field + "=:" + field)
            params[field] = value
    return " AND ".join(sql), params, year

def base_query(where):
    return (" FROM revenue r LEFT JOIN fleet f ON r.vehicle_id=f.vehicle_id"
            " LEFT JOIN fleet_plates fp ON r.plate=fp.plate"
            " LEFT JOIN regions rg ON UPPER(TRIM(r.city))=rg.city AND UPPER(TRIM(r.state))=rg.state"
            " WHERE " + where)

def money(value):
    return "R$ " + f"{float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

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
</style></head><body>
<header><h1>ECOSUPPLY | EFI</h1><div>Inteligência de Faturamento • Dados efetivamente importados</div></header>
<main><section><form class="filters" method="get">
<input name="year" value="{{year}}" size="5" aria-label="Ano">
<select name="month"><option value="">Todos os meses</option>{% for m in months %}<option value="{{m}}" {% if args.get('month')==m %}selected{% endif %}>{{m}}</option>{% endfor %}</select>
{% for k, values in options.items() %}<select name="{{k}}"><option value="">{{labels[k]}}: todos</option>{% for value in values %}<option value="{{value}}" {% if args.get(k)==value %}selected{% endif %}>{{value}}</option>{% endfor %}</select>{% endfor %}
<select name="dim">{% for k in dims %}<option value="{{k}}" {% if dim==k %}selected{% endif %}>Por {{labels[k]}}</option>{% endfor %}</select>
<button>Aplicar filtros</button></form></section>
<section><div class="metrics"><article><div class="muted">Receita atribuída aos itens</div><div class="big">{{money(total)}}</div></article>
<article><div class="muted">Faturamentos</div><div class="big">{{bills}}</div></article>
<article><div class="muted">Itens do BM / receita</div><div class="big">{{count}}</div></article>
<article><div class="muted">Itens sem placa</div><div class="big">{{missing}}</div></article></div>
{% if count == 0 %}<p class="muted">Nenhum faturamento importado. A base financeira está vazia; não são exibidos valores fictícios.</p>{% endif %}</section>
<section><h2>Faturamento por mês — {{year}}</h2><div class="bars">{% for m,v in monthly %}<div class="barrow"><span>{{m}}</span><b>{{money(v)}}</b></div><div class="barbg"><div class="bar" style="width:{{pct(v,peak)}}%"></div></div>{% endfor %}</div></section>
<section><h2>Faturamento por {{labels[dim]}}</h2><table><thead><tr><th>Grupo</th><th>Receita</th><th>Itens</th></tr></thead>
<tbody>{% for name,value,n in ranking %}<tr><td>{{name}}</td><td>{{money(value)}}</td><td>{{n}}</td></tr>{% endfor %}</tbody></table></section>
<section><h2>Volumes por unidade</h2><table><thead><tr><th>Unidade</th><th>Faturado</th><th>Coletado</th></tr></thead><tbody>
{% for unit,quantity,collected in volumes %}<tr><td>{{unit or 'Não informada'}}</td><td>{{quantity or 0}}</td><td>{{collected or 0}}</td></tr>{% endfor %}</tbody></table></section>
<section><h2>Auditoria e importação</h2><p><a href="/sisgr/diagnostico">Testar conexão SISGR</a></p><form action="/sisgr/piloto" method="post"><button>Atualizar SISGR — prévia piloto</button></form><p><a href="{{url_for('export_csv',**args.to_dict(flat=True))}}">Exportar CSV detalhado</a> · <a href="/fleet">Consultar frota oficial importada</a></p>
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
        volumes = db.execute(text("SELECT r.unit,SUM(r.quantity),SUM(r.collected)"+base+" GROUP BY 1"),params).all()
        options = {field:[r[0] for r in db.execute(text("SELECT DISTINCT "+field+
                   " FROM revenue WHERE "+field+" IS NOT NULL AND "+field+"<>'' ORDER BY 1 LIMIT 400"))]
                   for field in FILTERS}
    monthly = [(m,monthly_map.get(m,0)) for m in months]
    peak = max([float(v) for _,v in monthly]+[1])
    return render_template_string(HTML, year=year, args=request.args, options=options,
                                  dims=DIMS,dim=dim,labels=LABELS,months=months,
                                  total=total,count=count,bills=bills,missing=missing,
                                  monthly=monthly,ranking=ranking,volumes=volumes,money=money,
                                  peak=peak,pct=lambda v,p:100*float(v)/p)

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
        return jsonify(audit=audit,rows=rows,mode="preview; nenhum dado gravado")
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
