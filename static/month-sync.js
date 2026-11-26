(()=>{
const month=document.querySelector('[name=month]'),offset=document.querySelector('[name=offset]'),progress=document.getElementById('progress'),bar=document.getElementById('bar'),issues=document.getElementById('issues'),start=document.getElementById('start');
let running=false,busy=false;
document.getElementById('pause').onclick=()=>{running=false;progress.textContent+=' — pausa solicitada';};
start.onclick=async()=>{
if(busy)return;busy=true;running=true;start.disabled=true;
const period=month.value;month.disabled=true;offset.disabled=true;
let position=Number(offset.value),saved=0,pending=0;
try{
while(running){
const response=await fetch('/sisgr/lote',{method:'POST',body:new URLSearchParams({month:period,offset:String(position)})});
if(!response.ok)throw Error('HTTP '+response.status+' — retome na posição '+position);
const r=await response.json();
for(const item of r.results){if(item.status==='gravado')saved++;else{pending++;issues.textContent+=JSON.stringify(item)+'\n';}}
bar.max=Math.max(1,r.total);bar.value=r.next;
progress.textContent=r.next+' / '+r.total+' consultados | Gravados nesta execução: '+saved+' | Pendências: '+pending;
if(r.next<=position&&position<r.total)throw Error('Paginação sem avanço');
position=r.next;offset.value=position;
if(position>=r.total){progress.textContent+=' — consulta concluída';break;}
if(running)await new Promise(resolve=>setTimeout(resolve,750));
}
}catch(e){progress.textContent+=' | '+e.message;}
finally{running=false;busy=false;start.disabled=false;month.disabled=false;offset.disabled=false;}
};
})();