/* Native browser client — no Streamlit, CDN or JavaScript framework. */
"use strict";
const $ = (id) => document.getElementById(id);
const state = {page:"overview",assetcode:"",secid:"",quantity:1,bootstrap:null,dashboard:null,methodology:null,serial:0};
const h=(value)=>String(value??"").replace(/[&<>"']/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num=(v)=>(v===null||v===undefined||v===""||!Number.isFinite(Number(v)))?null:Number(v);
function toast(message){
  const box=$("toast");box.textContent=message;box.classList.remove("hidden");
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>box.classList.add("hidden"),5500);
}
async function getJSON(url){
  const response=await fetch(url,{headers:{"Accept":"application/json"},cache:"no-store"});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.detail||"Сервис недоступен");
  return data;
}
function makeCards(cards,target){
  $(target).innerHTML=(cards||[]).map((c)=>
    '<div class="data-card'+(c.special?' special':'')+'">'+
      (c.code?'<div class="card-code">'+h(c.code)+'</div>':'')+
      '<div class="card-label">'+h(c.label)+'</div>'+
      '<div class="card-value">'+h(c.value)+'</div>'+
      (c.note?'<div class="card-note">'+h(c.note)+'</div>':'')+
    '</div>'
  ).join("");
}
function selectOptions(element,choices,selected){
  element.innerHTML=choices.map((item)=>'<option value="'+h(item.code)+'">'+h(item.label)+'</option>').join("");
  if(choices.some((item)=>item.code===selected))element.value=selected;
  element.disabled=!choices.length;
}
function dateMoscow(){
  const parts=Object.fromEntries(
    new Intl.DateTimeFormat("en-GB",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit",
      hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).formatToParts(new Date()).map((p)=>[p.type,p.value])
  );
  return {date:parts.year+"-"+parts.month+"-"+parts.day,time:parts.hour+":"+parts.minute};
}
function specialDateTime(){
  return $("check-date").value&&$("check-time").value?$("check-date").value+"T"+$("check-time").value:"";
}
function buildQuery(){
  const q=new URLSearchParams({assetcode:state.assetcode,quantity:String(state.quantity)});
  if(state.secid)q.set("secid",state.secid);
  return q.toString();
}
function displayLoading(){
  ["overview-cards","concentration-cards","contract-cards","shifts-cards","weekend-cards","margin-head","margin-tiers"].forEach((id)=>{
    $(id).innerHTML=Array.from({length:id==="overview-cards"?5:3},()=>'<div class="skeleton"></div>').join("");
  });
}
function renderOverview(data){
  $("contract-heading").innerHTML='<strong>'+h(data.label)+' · '+h(data.contract.name)+'</strong><small>'+
    h(data.contract.selected)+'</small>';
  makeCards(data.overview,"overview-cards");makeCards(data.concentration,"concentration-cards");
  makeCards(data.contract_parameters,"contract-cards");
  $("overview-footnote").textContent=data.missing.length?
    "Не получены: "+data.missing.join(", ")+". Прочерки не заменяются предположениями.":
    "Источники: MOEX ISS / НКЦ. Данные относятся к выбранной серии и базисному активу.";
}
function renderBounds(data){
  const b=data.boundaries;
  $("bounds-source").textContent=(data.contract.selected||data.assetcode)+" · Цена ISS: "+b.source+" · Дата торгов: "+b.tradedate;
  $("low-price").textContent=b.low;$("high-price").textContent=b.high;$("current-price").textContent=b.price;
  $("corridor-width").textContent="Ширина: "+b.width;
  $("to-low").textContent=b.to_low+" · "+b.to_low_pct;
  $("to-high").textContent=b.to_high+" · "+b.to_high_pct;
  $("position-pct").textContent=b.ratio===null?"—":b.ratio.toFixed(1)+"%";
  $("price-marker").hidden=b.ratio===null;
  if(b.ratio!==null)$("price-marker").style.left=String(b.ratio)+"%";
  makeCards(b.shifts,"shifts-cards");makeCards(b.weekend,"weekend-cards");
  $("weekend-warning").innerHTML=b.weekend_error?
    '<div class="alert alert-warn">Расчётные границы не получены: '+h(b.weekend_error)+'.</div>':"";
  $("morning-wrap").hidden=!b.morning_currency;
  if(b.morning_currency)makeCards(b.morning,"morning-cards");
}
function renderMargin(data){
  const m=data.margin;
  $("margin-description").textContent=data.contract.name+" · LK1 ≈ "+
    (num(m.lk1)===null?"—":Math.floor(m.lk1))+" контр. · LK2 ≈ "+
    (num(m.lk2)===null?"—":Math.floor(m.lk2))+" контр. · INITIALMARGIN "+m.initial;
  const vals={
    lk1:num(m.lk1)===null?null:Math.floor(m.lk1),
    lk1plus:num(m.lk1)===null?null:Math.floor(m.lk1)+1,
    lk2:num(m.lk2)===null?null:Math.floor(m.lk2),
    lk2plus:num(m.lk2)===null?null:Math.floor(m.lk2)+1
  };
  document.querySelectorAll("#quick-buttons button").forEach((b)=>{
    b.disabled=b.dataset.quantity!=="1"&&vals[b.dataset.quantity]===null;
  });
  $("margin-error").innerHTML=m.error?'<div class="alert alert-warn">'+h(m.error)+'</div>':"";
  makeCards([
    {code:"TOTAL MARGIN",label:"Оценка ГО",value:m.total},
    {code:"MR LEVEL",label:"Достигнутый уровень",value:m.highest},
    {code:"POSITION_BA",label:"Объём в единицах БА",value:m.position_ba}
  ],"margin-head");
  const levels=m.levels||[];
  const sum=levels.reduce((s,v)=>s+(Number(v.count)||0),0);
  $("position-bar").innerHTML=levels.map((row,i)=>
    '<div class="segment seg-'+i+'" style="width:'+(sum>0?Math.max(0,row.count)/sum*100:0)+
    '%" title="'+h(row.code)+': '+h(row.contracts)+' контр."></div>'
  ).join("");
  makeCards(levels.map((row,i)=>({
    code:"LEVEL "+(i+1),label:row.code+" · "+row.label,
    value:row.margin,note:row.contracts+" контр. · ставка "+row.rate
  })),"margin-tiers");
  $("margin-table").innerHTML=levels.map((row)=>
    "<tr><td>"+h(row.code)+"</td><td>"+h(row.label)+"</td><td>"+h(row.contracts)+
    "</td><td>"+h(row.rate)+"</td><td>"+h(row.margin)+"</td></tr>"
  ).join("");
}
function renderDashboard(data){
  state.dashboard=data;state.secid=data.contract.selected;
  const existing=Array.from($("contract-select").options).map((o)=>o.value);
  const desired=data.contract.options.map((o)=>o.code);
  if(existing.join("|")!==desired.join("|")){
    selectOptions($("contract-select"),data.contract.options,data.contract.selected);
  }
  $("contract-select").value=data.contract.selected;
  renderOverview(data);renderBounds(data);renderMargin(data);
  $("connection-label").textContent="Данные получены";$("connection-label").className="header-pill ok";
}
async function loadDashboard(){
  if(!state.assetcode)return;
  const key=++state.serial;
  $("connection-label").textContent="Обновление данных";$("connection-label").className="header-pill";
  try{
    const data=await getJSON("/api/dashboard?"+buildQuery());
    if(key!==state.serial)return;
    renderDashboard(data);
  }catch(error){
    if(key!==state.serial)return;
    $("connection-label").textContent="Ошибка загрузки";$("connection-label").className="header-pill warn";
    toast(error.message);
  }
}
function sourceWarning(payload){
  const bad=Object.values(payload.sources||{}).filter((s)=>s.state!=="live");
  const box=$("source-warning");
  if(!bad.length){box.classList.add("hidden");return;}
  box.classList.remove("hidden");
  box.textContent="Часть источников использует резервные данные или недоступна: "+
    bad.map((s)=>s.name+" ("+s.state+")").join(", ")+". Данные могут быть устаревшими — проверяйте даты.";
}
function tableFromRows(rows,columns){
  return '<div class="table-wrap"><table><thead><tr>'+
    columns.map((c)=>"<th>"+h(c[1])+"</th>").join("")+"</tr></thead><tbody>"+
    rows.map((row)=>"<tr>"+columns.map((c)=>"<td>"+h(row[c[0]])+"</td>").join("")+"</tr>").join("")+
    "</tbody></table></div>";
}
async function loadSpecial(){
  if(state.page!=="special")return;
  const code=$("special-select").value;if(!code)return;
  const query=new URLSearchParams({assetcode:code,check_at:specialDateTime()});
  try{
    const res=await getJSON("/api/special?"+query.toString());
    if(code!==$("special-select").value||state.page!=="special")return;
    makeCards([
      {code:"SPECIAL RISK",label:"Специальный режим действует",value:res.label},
      {code:"PARAMETERS",label:"Активных настроек",value:String(res.parameters.length)},
      {code:"VALIDITY",label:"Период действия",value:res.period}
    ],"special-summary");
    $("special-message").textContent=res.message;
    $("special-message").className="alert "+
      (res.state==="ACTIVE"?"alert-warn":res.state==="INACTIVE"?"alert-green":"alert-neutral");
    const active=res.parameters||[];
    $("special-active").classList.toggle("hidden",active.length===0);
    $("special-active").innerHTML=active.length?tableFromRows(active,[
      ["code","Параметр"],["label","Что меняется"],["value","Значение"],
      ["unit","Единица"],["event","Событие"]
    ]):"";
    $("special-future").classList.toggle("hidden",!(res.future||[]).length);
    $("special-future-content").innerHTML=tableFromRows(res.future||[],[
      ["from","Начало"],["to","Окончание"],["event","Событие"],
      ["parameter","Параметр"],["value","Значение"]
    ]);
  }catch(error){
    $("special-message").textContent=error.message;$("special-message").className="alert alert-danger";
  }
}
async function loadFullCalendar(){
  const btn=$("calendar-load");btn.disabled=true;btn.textContent="Загрузка…";
  try{
    const data=await getJSON("/api/special/calendar");
    const cols=(data.columns||[]).map((key)=>[key,key]);
    $("calendar-content").innerHTML=data.rows?.length?tableFromRows(data.rows,cols):
      "<p>"+h(data.message||"Нет записей")+"</p>";
    btn.classList.add("hidden");
  }catch(error){toast(error.message);btn.disabled=false;btn.textContent="Повторить загрузку";}
}
async function loadMethodology(){
  if(state.methodology)return;
  try{
    const data=await getJSON("/api/methodology");state.methodology=data;
    $("method-cards").innerHTML=(data.records||[]).map((r)=>
      '<article class="method-card"><div class="method-card-head"><span class="param-tag">'+h(r.parameter)+
      '</span><span class="method-unit">'+h(r.unit)+'</span></div><h3>'+h(r.title)+
      '</h3><p>'+h(r.short_description)+'</p><details><summary>Как применять</summary><div>'+
      h(r.how_to_read)+'<p class="footnote">'+h(r.source_note)+'</p></div></details></article>'
    ).join("");
  }catch(error){$("method-cards").textContent="Не удалось загрузить справочник: "+error.message;}
}
function switchPage(page){
  state.page=page;
  document.querySelectorAll("#main-tabs button").forEach((btn)=>btn.setAttribute("aria-selected",String(btn.dataset.tab===page)));
  document.querySelectorAll(".content-panel").forEach((panel)=>{
    const active=panel.id==="panel-"+page;panel.hidden=!active;panel.classList.toggle("active",active);
  });
  $("instrument-selector").classList.toggle("hidden",["special","method"].includes(page));
  if(page==="method")loadMethodology();
  if(page==="special")loadSpecial();
}
async function init(){
  displayLoading();
  const dt=dateMoscow();$("check-date").value=dt.date;$("check-time").value=dt.time;
  document.querySelectorAll("#main-tabs button").forEach((btn)=>
    btn.addEventListener("click",()=>switchPage(btn.dataset.tab))
  );
  document.querySelectorAll(".sub-tabs button").forEach((btn)=>btn.addEventListener("click",()=>{
    document.querySelectorAll(".sub-tabs button").forEach((b)=>b.classList.toggle("active",b===btn));
    $("method-reference").hidden=btn.dataset.subtab!=="reference";
    $("method-formulas").hidden=btn.dataset.subtab!=="formulas";
  }));
  $("asset-select").addEventListener("change",()=>{
    state.assetcode=$("asset-select").value;state.secid="";loadDashboard();
  });
  $("contract-select").addEventListener("change",()=>{
    state.secid=$("contract-select").value;loadDashboard();
  });
  let timer;
  $("quantity").addEventListener("input",()=>{
    clearTimeout(timer);timer=setTimeout(()=>{
      const value=Number($("quantity").value);
      if(!Number.isSafeInteger(value)||value<0||value>10000000)return;
      state.quantity=value;loadDashboard();
    },250);
  });
  $("quick-buttons").addEventListener("click",(event)=>{
    const btn=event.target.closest("button[data-quantity]");
    if(!btn||btn.disabled||!state.dashboard)return;
    const code=btn.dataset.quantity,m=state.dashboard.margin;
    const amount=code==="1"?1:
      Math.floor(code.startsWith("lk1")?m.lk1:m.lk2)+(code.endsWith("plus")?1:0);
    if(!Number.isSafeInteger(amount)||amount<0||amount>10000000)return;
    state.quantity=amount;$("quantity").value=String(amount);loadDashboard();
  });
  $("special-select").addEventListener("change",loadSpecial);
  $("check-date").addEventListener("change",loadSpecial);
  $("check-time").addEventListener("change",loadSpecial);
  $("calendar-load").addEventListener("click",loadFullCalendar);
  try{
    const bootstrap=await getJSON("/api/bootstrap");state.bootstrap=bootstrap;
    if(!bootstrap.assets?.length)throw new Error("В доступных источниках нет активных инструментов");
    const codes=bootstrap.assets.map((item)=>item.code);
    state.assetcode=codes.includes("RTS")?"RTS":codes[0];
    selectOptions($("asset-select"),bootstrap.assets,state.assetcode);
    selectOptions($("special-select"),bootstrap.special_assets.map((code)=>({
      code:code,label:bootstrap.assets.find((a)=>a.code===code)?.label||code
    })),state.assetcode);
    sourceWarning(bootstrap);await loadDashboard();
    setInterval(async()=>{
      try{
        const update=await getJSON("/api/bootstrap");state.bootstrap=update;sourceWarning(update);
        await loadDashboard();if(state.page==="special")await loadSpecial();
      }catch(error){toast("Не удалось обновить источники: "+error.message);}
    },300000);
  }catch(error){
    $("connection-label").textContent="Нет данных";$("connection-label").className="header-pill warn";
    $("contract-heading").textContent="Не удалось получить данные: "+error.message;
    toast(error.message);
  }
}
document.addEventListener("DOMContentLoaded",init);
