(async()=>{
const svg=document.getElementById("revenue-map"),info=document.getElementById("mapinfo"),mode=document.getElementById("mapmode");
const norm=v=>String(v||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toUpperCase().trim();
const money=v=>v.toLocaleString("pt-BR",{style:"currency",currency:"BRL"}),num=v=>v.toLocaleString("pt-BR",{maximumFractionDigits:2});
try{
const response=await fetch("/geografia");if(!response.ok)throw Error("Mapa indisponível");
const geo=await response.json(),cities=new Map(),regions=new Map();
const add=(a,b)=>{for(const k of ["amount","kg","liters"])a[k]+=b[k];return a},zero=()=>({amount:0,kg:0,liters:0});
for(const r of mapRows)if(["MT","MATO GROSSO"].includes(norm(r.state))) {const k=norm(r.city);cities.set(k,add(cities.get(k)||zero(),r));}
const polygons=f=>f.geometry.type==="Polygon"?[f.geometry.coordinates]:f.geometry.coordinates;
let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
for(const f of geo.features){for(const p of polygons(f))for(const ring of p)for(const [x,y] of ring){minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);}
const k=f.properties.Regiao;regions.set(k,add(regions.get(k)||zero(),cities.get(norm(f.properties.Cidade))||zero()));}
const scale=Math.min(760/(maxX-minX),610/(maxY-minY)),project=([x,y])=>[400+(x-(minX+maxX)/2)*scale,325-(y-(minY+maxY)/2)*scale];
const node=(tag,attrs)=>{const e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e};
function render(){svg.replaceChildren();const regional=mode.value==="region",max=Math.max(1,...[...(regional?regions:cities).values()].map(r=>Math.abs(r.amount)));
for(const f of geo.features){const p=f.properties,k=regional?p.Regiao:norm(p.Cidade),v=(regional?regions:cities).get(k)||zero(),label=regional?p.Regiao:p.Cidade;
const d=polygons(f).flatMap(poly=>poly.map(ring=>"M"+ring.map(pt=>project(pt).map(n=>n.toFixed(2)).join(",")).join("L")+"Z")).join(" ");
const path=node("path",{d,fill:v.amount?p.fillColor||"#16866b":"#e5ece9","fill-opacity":v.amount?0.35+0.65*Math.sqrt(Math.abs(v.amount)/max):0.65,stroke:"#fff","stroke-width":0.6,"fill-rule":"evenodd",tabindex:0,role:"button"});
const detail=()=>{info.textContent=label+" | "+money(v.amount)+" | "+num(v.kg)+" kg | "+num(v.liters)+" L"+(v.kg>0&&v.liters===0?" | "+money(v.amount/v.kg)+"/kg":v.liters>0&&v.kg===0?" | "+money(v.amount/v.liters)+"/L":"");};
const title=node("title",{});title.textContent=label+" — "+money(v.amount);path.append(title);path.addEventListener("click",detail);path.addEventListener("focus",detail);svg.append(path);
if(!regional&&v.amount&&p.center){const [cx,cy]=project(p.center);const circle=node("circle",{cx,cy,r:3+12*Math.sqrt(Math.abs(v.amount)/max),fill:"#075d48","fill-opacity":0.8,stroke:"#fff","stroke-width":1});circle.addEventListener("click",detail);svg.append(circle);}
}}
mode.addEventListener("change",render);render();
}catch(e){info.textContent="Não foi possível carregar o mapa.";}
})();