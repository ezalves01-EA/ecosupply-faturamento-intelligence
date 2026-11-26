(()=>{
const month=document.querySelector('[name=month]'),offset=document.querySelector('[name=offset]'),progress=document.getElementById('progress'),bar=document.getElementById('bar'),issues=document.getElementById('issues'),start=document.getElementById('start'),workers=document.getElementById('workers'),mode=document.getElementById('mode');
let running=false,busy=false;
const key=()=> 'efi-position-'+month.value;
const restore=()=>{offset.value=localStorage.getItem(key())||'0';};
month.onchange=restore;restore();
document.getElementById('pause').onclick=()=>{running=false;progress.textContent+=' — aguardando lotes ativos';};
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function load(period,pos,kind){
const res=await fetch('/sisgr/lote',{method:'POST',body:new URLSearchParams({month:period,offset:String(pos),mode:kind})});
if(!res.ok)throw Error('HTTP '+res.status);
return res.json();
}
start.onclick=async()=>{
if(busy)return;busy=true;running=true;start.disabled=true;month.disabled=offset.disabled=workers.disabled=mode.disabled=true;
const period=month.value,kind=mode.value,initial=Number(offset.value),started=Date.now();let position=initial,total=null,saved=0,existing=0,pending=0,parallel=Number(workers.value),failures=0;
try{
while(running){
const size=total===null?1:Math.min(parallel,total-position);
if(size<=0)break;
const results=await Promise.allSettled(Array.from({length:size},(_,i)=>load(period,position+i,kind)));
let advanced=0,blocked=false;
for(let i=0;i<results.length;i++){
const x=results[i];
if(x.status==='rejected'){blocked=true;issues.textContent+='Posição '+(position+i)+': '+x.reason+'\n';continue;}
const r=x.value;total=r.total;
if(r.next!==position+i+1){blocked=true;continue;}
for(const item of r.results){
if(item.status==='gravado')saved++;else if(item.status==='existente')existing++;else{pending++;issues.textContent+=JSON.stringify(item)+'\n';localStorage.setItem('efi-pending-'+period+'-'+item.id,JSON.stringify(item));}
}
if(!blocked)advanced++;
}
position+=advanced;offset.value=position;localStorage.setItem(key(),String(position));
const elapsed=(Date.now()-started)/1000,rate=(position-initial)/Math.max(elapsed,1),remaining=rate>0?Math.max(0,(total||0)-position)/rate:0;
bar.max=Math.max(1,total||1);bar.value=position;
progress.textContent=position+' / '+(total??'?')+' | Gravados: '+saved+' | Existentes: '+existing+' | Pendências: '+pending+' | Paralelos: '+parallel+' | '+(rate*60).toFixed(1)+' fat./min | Restante: '+(rate?Math.ceil(remaining/60)+' min':'calculando')+' | Previsão: '+(rate?new Date(Date.now()+remaining*1000).toLocaleTimeString('pt-BR'):'—');
if(total!==null&&position>=total){progress.textContent+=' — concluído';break;}
if(blocked){parallel=Math.max(1,Math.floor(parallel/2));failures++;if(failures>=3)throw Error('Falhas repetidas; retome na posição '+position);if(running)await wait(5000*failures);}else{failures=0;if(running)await wait(750);}
}
}catch(e){progress.textContent+=' | '+e.message;}
finally{running=false;busy=false;start.disabled=false;month.disabled=offset.disabled=workers.disabled=mode.disabled=false;}
};
})();