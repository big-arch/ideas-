'use strict';
let state,items=[],selected=null,category='all',favoriteOnly=false,currentDetail=null,activeTab='facts',analysisRunningFor=null,analysisQueuedFor=null,analysisLoading=false,analysisError='',photoIndex=0,requestVersion=0,visibleCount=10;
const el=id=>document.getElementById(id);
let listRequestVersion=0,listTotal=0,nextPage=null,listLoading=false;
const money=n=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:0}).format(n);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(data)});let value=await r.json();if(!r.ok)throw Error(value.error||'Не удалось завершить запрос');return value}
function toast(t){el('toast').textContent=t;el('toast').style.display='block';setTimeout(()=>el('toast').style.display='none',6000)}
function photo(id,index=0){return '/photo?id='+encodeURIComponent(id)+'&index='+index}
function plain(s){return String(s||'').replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g,'$1').replace(/^[#\s]+/gm,'').replace(/\*\*/g,'').trim()}
function reportSections(report){
  const sections=[];let section;
  for(const raw of (report?.text||'').split('\n')){
    const line=raw.trim(), heading=line.match(/^#{1,4}\s+(.+)$/)||line.match(/^\*\*([^*]+)\*\*\s*:?$/)||line.match(/^\d+[.)]\s+(Краткий вывод|Цена и аналоги|Что продаётся|Что видно на фото|Район и спрос|Пригодность для цели|Риски и проверки|Вопросы продавцу)\s*:?$/i);
    if(heading){section={title:plain(heading[1]).replace(/^\d+[.)]\s*/,''),body:''};sections.push(section)}
    else if(line||section){if(!section){section={title:'Вывод',body:''};sections.push(section)}section.body+=raw+'\n'}
  }
  return sections.filter(s=>s.body.trim()).map(s=>({...s,body:plain(s.body)}))
}
function brief(report){const sections=reportSections(report);return (sections.find(x=>/краткий вывод/i.test(x.title))||sections[0])?.body||''}
const LABELS={rooms:'Комнат',year:'Год',mileage_km:'Пробег, км',make:'Марка',model:'Модель',engine:'Двигатель',gearbox:'Коробка',drive:'Привод',building_type:'Тип дома',floor_group:'Этаж',land_area_m2:'Участок, м²',wall_material:'Материал стен',land_use:'Назначение земли',utilities:'Коммуникации',property_type:'Тип помещения',purpose:'Назначение'};
const VALUES={unknown:'Не указано',owned:'Заявлена собственность',lease:'Аренда',share:'Доля',membership:'Членство',capital:'Капитальный',metal:'Металлический',parking:'Машино-место',normal:'Обычное',needs_repair:'Требует ремонта',front:'Передний',rear:'Задний',all:'Полный',first:'Первый',last:'Последний',middle:'Средний',brick:'Кирпич',monolith:'Монолит',panel:'Панель',block:'Блок',wood:'Дерево',frame:'Каркас',resale:'Вторичное',AT:'Автомат',MT:'Механика',CVT:'Вариатор',AMT:'Робот'};
function fact(k,v){return `<div class="fact"><dt>${esc(k)}</dt><dd>${esc(VALUES[v]||v||'Не указано')}</dd></div>`}
function outsideRostov(item){const city=String(item.locality||'').trim();return !!city&&!/^(?:г\.?\s*)?ростов-на-дону(?:\s*,|$)/i.test(city)}
function cityBadge(item){return `<span class="city-badge${outsideRostov(item)?' outside-city':''}">${outsideRostov(item)?'<span class="city-dot" aria-hidden="true"></span>':''}${esc(item.locality)}${outsideRostov(item)?' · область':''}</span>`}
function comparisonBadge(item){
  const evidence=item.price_analysis||{},discount=evidence.discount;
  let text='Нет данных для процента',kind='unknown';
  if(typeof discount==='number'&&!['insufficient','excluded'].includes(evidence.status)){
    const pct=Math.round(Math.abs(discount)*100);
    kind=pct===0?'level':discount>0?'lower':'higher';
    text=pct===0?'0% · на уровне аналогов':`${discount>0?'−':'+'}${pct}% · ${discount>0?'дешевле':'дороже'}`;
  }
  const explanation=evidence.reason||item.comparison||'Недостаточно похожих объектов';
  return `<span class="row-comparison ${kind}" title="${esc(explanation)}">${esc(text)}</span>${kind==='unknown'?`<small class="comparison-note">${esc(explanation)} · аналогов: ${esc(evidence.comparables??0)}</small>`:`<small class="comparison-note">По ценам объявлений · ${esc(evidence.comparables??0)} аналогов${evidence.scope==='city'?' · по городу':''}</small>`}`;
}
function analysisAvailable(){return !!(state?.chatgpt?.connected||state?.local_ai)}
function evidenceStrip(report){
  const facts=report?.decision_facts;if(!facts)return '';
  const figures=[];
  if(Number.isFinite(facts.unit_rub_m2))figures.push(['Цена за м²',money(facts.unit_rub_m2)]);
  if(Number.isFinite(facts.difference_rub))figures.push([facts.difference_rub>=0?'Ниже ориентира':'Выше ориентира',money(Math.abs(facts.difference_rub))]);
  if(Number.isInteger(facts.peer_count)&&facts.peer_count>0)figures.push(['Похожих предложений',String(facts.peer_count)]);
  if(!figures.length)return '';
  return '<dl class="evidence-strip">'+figures.map(([title,value])=>'<div><dt>'+esc(title)+'</dt><dd>'+esc(value)+'</dd></div>').join('')+'</dl>'
}
function currentlyVisible(item){return item.active!==false&&(!item.available_until||Date.parse(item.available_until)>Date.now())}
function catalogStamp(item){
  if(!item.observed_at)return '';
  const when=new Date(item.observed_at);
  if(!Number.isFinite(when.getTime()))return '';
  return 'В каталоге '+new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'}).format(when)+' МСК'
}
function renderRows(){
  const pane=document.querySelector('.list'), position=pane.scrollTop;
  const list=items.filter(x=>currentlyVisible(x)&&(!favoriteOnly||x.favorite)&&(el('viewFilter').value!=='unseen'||!x.meta?.seen)&&(!el('ratedOnly').checked||(typeof x.price_analysis?.discount==='number'&&!['insufficient','excluded'].includes(x.price_analysis.status))));
  const rows=el('rows');rows.replaceChildren();
  for(const x of list){
    const button=document.createElement('button');button.className='row'+(x.id===selected?' selected':'');
    const photoHtml=x.photo_url?`<img class="thumb" loading="lazy" src="${photo(x.id)}" alt="">`:'<span class="thumb missing-photo">Без фото</span>';
    button.innerHTML=photoHtml+`<div class="row-body"><div class="row-price"><strong>${esc(money(x.price_rub))}</strong></div><div class="title">${esc(x.title)}</div>${comparisonBadge(x)}<div class="row-location">${cityBadge(x)}<small>${esc(x.source)}</small></div><small class="catalog-stamp">${esc(catalogStamp(x))}</small>${x.meta?.stage?'<small>'+esc(state.stages[x.meta.stage])+'</small>':''}${x.price_change?'<small class="row-state">Цена снизилась</small>':''}</div>`;
    button.onclick=()=>openItem(x.id);rows.append(button)
  }
  if(!list.length){const note=document.createElement('div');note.className='feed-empty';note.innerHTML='<strong>Свежих совпадений пока нет</strong><p>Попробуй другую категорию или измени фильтры. Новые предложения появятся после обновления источников.</p>';rows.append(note)}
  if(nextPage){const more=document.createElement('button');more.className='more-list';more.textContent=listLoading?'Загружаю…':'Ещё объявления';more.disabled=listLoading;more.onclick=()=>loadList({more:true});rows.append(more)}
  el('count').textContent='Показано '+list.length+' из '+Math.max(list.length,listTotal-(items.length-list.length))+' · за 7 дней';pane.scrollTop=position
}
async function loadList({background=false,more=false}={}) {
  if((more || background) && listLoading)return;
  if(more && !nextPage)return;
  const version=++listRequestVersion,query=new URLSearchParams(listQuery());
  const target=background?Math.max(10,visibleCount):10;
  let cursor=more?nextPage:null,loaded=more?[...items]:[],added=0;
  listLoading=true;
  if(!more && !background){
    items=[];nextPage=null;listTotal=0;visibleCount=10;
    el('rows').innerHTML='<div class="loading" role="status"><p>Загружаю актуальные объявления…</p></div>';
    el('count').textContent='Обновляю список…';
  }else if(more){renderRows()}
  try{
    do{
      query.set('limit',String(Math.min(50,target-added)));
      if(cursor)query.set('after',cursor);
      const data=await api('/api/list?'+query);
      if(version!==listRequestVersion)return;
      loaded.push(...data.items);added+=data.items.length;
      cursor=data.next||null;listTotal=data.count;
      if(!data.items.length)break;
    }while(background && cursor && added<target);
    items=[...new Map(loaded.map(item=>[item.id,item])).values()];nextPage=cursor;
    visibleCount=items.length;
    if(!background && !more)document.querySelector('.list').scrollTop=0;
  }catch(error){
    if(version!==listRequestVersion)return;
    if(!background && !more){el('rows').textContent=error.message;el('count').textContent='Не удалось загрузить подборку';return}
    toast(error.message);
  }finally{
    if(version===listRequestVersion){listLoading=false;if(more || background || items.length || el('rows').querySelector('.loading'))renderRows()}
  }
}
async function openItem(id){const version=++requestVersion;selected=id;currentDetail=null;analysisError='';photoIndex=0;el('photoViewer').close();renderRows();el('layout').classList.add('detail-open');el('detail').innerHTML='<div class="loading" role="status"><div class="skeleton photo-skeleton"></div><div class="skeleton short"></div><div class="skeleton"></div><p>Читаю полную страницу объявления…</p></div>';try{let data=await api('/api/item?id='+encodeURIComponent(id));if(version!==requestVersion)return;data.item.meta={...data.item.meta,seen:true};const row=items.find(x=>x.id===id);if(row)row.meta=data.item.meta;api('/api/meta',{id,changes:{seen:true}}).then(seen=>{if(selected===id&&currentDetail)currentDetail.item.meta=seen.meta;if(row)row.meta=seen.meta;renderRows()}).catch(()=>toast('Просмотр не удалось сохранить'));currentDetail=data;renderRows();activeTab='facts';analysisLoading=analysisAvailable()&&currentlyVisible(data.item)&&!data.analysis;renderDetail();watchGallery(id,version);if(analysisLoading)runAnalysis()}catch(e){if(version===requestVersion)el('detail').textContent=e.message}}
function mapPanel(item){
  const map=item.map;if(!map)return '';
  return `<details class="object-map"><summary><span>Адрес на карте</span><strong>${esc(map.address||map.query)}</strong></summary><p class="muted">${esc(map.note)}</p><iframe title="Адрес объявления на карте: ${esc(map.address||map.query)}" src="${esc(map.embed)}" loading="lazy" referrerpolicy="no-referrer" allowfullscreen></iframe><a href="${esc(map.url)}" target="_blank" rel="noopener noreferrer">Открыть этот адрес в Яндекс Картах ↗</a></details>`;
}
function renderDetail(){const x=currentDetail.item,d=currentDetail.detail,a=currentDetail.analysis,comparison=items.find(i=>i.id===x.id)?.comparison||x.comparison||'';const photos=d.photos||[];photoIndex=Math.min(photoIndex,Math.max(0,photos.length-1));let summary=brief(a)|| (!currentlyVisible(x)?'Срок проверки истёк или источник снял объявление. Обнови выдачу перед обращением к продавцу.':analysisLoading?'Готовлю разбор цены, описания и вопросов для проверки.':analysisError|| (analysisAvailable()?'Разбор ещё не готов. Можно повторить запрос.':'Подключи ChatGPT, чтобы получать разбор текста и фотографий.'));el('detail').innerHTML=`<button class="mobile-back" id="back">← К объявлениям</button><article class="object-card"><div class="card-top"><div><strong>${esc(x.source)}</strong><small>${esc(catalogStamp(x))}</small></div><button id="favorite" class="save" aria-label="${x.favorite?'Убрать из избранного':'Сохранить объект'}" aria-pressed="${x.favorite}">${x.favorite?'★':'☆'}</button></div><div class="card-body"><div class="eyebrow">${esc(state.categories[x.category])}${x.area_m2?' · '+esc(x.area_m2)+' м²':''}</div><div class="price">${esc(money(x.price_rub))}</div><div class="comparison">${comparisonBadge(x)}</div><h2>${esc(x.title)}</h2><div class="card-city">${cityBadge(x)}</div>${!currentlyVisible(x)?'<p class="notice">Источник больше не подтверждает актуальность этого объекта.</p>':''}<p class="address">${esc(x.attributes.visible_address||x.locality)}</p>${mapPanel(x)}<section class="ai-brief" aria-live="polite"><div class="eyebrow">${analysisLoading&&!a?'ИИ изучает объект':'Вывод ИИ'}</div><p>${esc(summary)}</p>${a?`<div class="brief-meta">${a.provider==='ollama'?'Ollama · анализ текста. Фото и район не проверялись.':`ChatGPT · изучено фото ${esc(a.photos_analyzed)} · внешних источников ${esc(a.citations?.length||0)}`}</div>`:''}${!a&&!analysisLoading&&currentlyVisible(x)?`<button id="retryAnalysis" style="margin-top:12px">${analysisAvailable()?'Повторить разбор':'Подключить ChatGPT'}</button>`:''}</section><div class="tabs" role="tablist" aria-label="Подробности объявления">${[['facts','Объект'],['analysis','Анализ ИИ']].map(([key,label])=>`<button id="tab-${key}" role="tab" aria-controls="tabContent" data-tab="${key}" aria-selected="${activeTab===key}">${label}</button>`).join('')}</div><section id="tabContent" role="tabpanel" aria-labelledby="tab-${activeTab}"></section><div class="actions object-actions"><a class="primary" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Открыть на ${esc(x.source)} ↗</a><button id="askAboutItem">Спросить ИИ</button></div><div class="card-date">${esc(x.attributes.site_date_at?'Дата на сайте '+new Date(x.attributes.site_date_at).toLocaleDateString('ru-RU'):'Дата публикации неизвестна')}.</div></div></article>`;
el('back').onclick=()=>el('layout').classList.remove('detail-open');el('favorite').onclick=toggleFavorite;el('askAboutItem').onclick=()=>el('openChat').click();el('retryAnalysis')?.addEventListener('click',()=>analysisAvailable()?runAnalysis():openSettings());document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>selectDetailTab(b.dataset.tab));renderObjectGallery();renderTab();renderWorkflow();}
function renderObjectGallery(){
  if(!currentDetail)return;
  const card=document.querySelector('.object-card');if(!card)return;
  card.querySelectorAll(':scope > .hero-wrap,:scope > .gallery,:scope > .gallery-shell,:scope > .notice').forEach(n=>n.remove());
  const x=currentDetail.item,d=currentDetail.detail,photos=d.photos||[];
  photoIndex=Math.min(photoIndex,Math.max(0,photos.length-1));
  const shell=document.createElement('section');shell.className='gallery-shell';shell.setAttribute('aria-label','Фотографии объявления');
  shell.innerHTML=photos.length?`<div class="hero-wrap"><button class="hero-button" id="enlargePhoto" aria-label="Увеличить оригинальное фото"><img id="hero" class="hero" src="${photo(x.id,photoIndex)}" alt="Фото ${photoIndex+1} из объявления"></button><span class="photo-count" id="photoCount">${photoIndex+1} / ${photos.length}</span>${photos.length>1?'<button class="gallery-arrow previous" id="previousPhoto" aria-label="Предыдущее фото">‹</button><button class="gallery-arrow next" id="nextPhoto" aria-label="Следующее фото">›</button>':''}</div>${photos.length>1?`<div class="gallery" aria-label="Выбрать фотографию">${photos.map((_,i)=>`<button data-photo="${i}" aria-label="Фото ${i+1}" aria-pressed="${i===photoIndex}"><img src="${photo(x.id,i)}" alt="" loading="lazy"></button>`).join('')}</div>`:''}<p class="gallery-status" role="status">${d.pending?'Загружаю остальные фотографии…':photos.length<4?`Источник предоставил ${photos.length} фото. ${d.error?'Остальная галерея пока недоступна.':''}`:'Реальные фотографии из объявления'}</p>`:'<p class="notice" role="status">'+(d.pending?'Загружаю фотографии…':'Источник не предоставил доступные фотографии.')+'</p>';
  card.querySelector('.card-body').before(shell);
  document.querySelectorAll('[data-photo]').forEach(b=>b.onclick=()=>selectPhoto(Number(b.dataset.photo)));
  el('previousPhoto')?.addEventListener('click',()=>selectPhoto(photoIndex-1));
  el('nextPhoto')?.addEventListener('click',()=>selectPhoto(photoIndex+1));
  el('enlargePhoto')?.addEventListener('click',()=>{selectPhoto(photoIndex);el('photoViewer').showModal()});
  let toolbar=el('largePhotoToolbar');
  if(!toolbar){toolbar=document.createElement('div');toolbar.id='largePhotoToolbar';toolbar.className='photo-viewer-tools';toolbar.innerHTML='<button id="largePrevious" aria-label="Предыдущее фото">‹</button><span id="largePhotoCount"></span><button id="largeNext" aria-label="Следующее фото">›</button>';el('largePhoto').after(toolbar);el('largePrevious').onclick=()=>selectPhoto(photoIndex-1);el('largeNext').onclick=()=>selectPhoto(photoIndex+1);el('photoViewer').addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();selectPhoto(photoIndex+(event.key==='ArrowLeft'?-1:1))}})}
}
function selectPhoto(index){
  const count=currentDetail?.detail.photos?.length||0;if(!count)return;
  photoIndex=(index+count)%count;
  const url=photo(selected,photoIndex);
  el('hero').src=url;el('hero').alt='Фото '+(photoIndex+1)+' из объявления';el('largePhoto').src=url;
  el('photoCount').textContent=el('largePhotoCount').textContent=(photoIndex+1)+' / '+count;
  el('largePrevious').disabled=el('largeNext').disabled=count<2;
  document.querySelectorAll('[data-photo]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.photo)===photoIndex)));
}
async function watchGallery(id,version){
  for(let attempt=0;attempt<12&&currentDetail?.detail.pending;attempt++){
    await new Promise(resolve=>setTimeout(resolve,2500));
    if(version!==requestVersion||selected!==id)return;
    try{const data=await api('/api/item?id='+encodeURIComponent(id));if(version!==requestVersion||selected!==id)return;currentDetail.detail=data.detail;renderObjectGallery();if(activeTab==='facts'){const description=el('tabContent').querySelector?.('.seller-text .prose'),status=el('tabContent').querySelector?.('.detail-status');if(description)description.textContent=data.detail.description||'Описание не получено';if(status)status.textContent=data.detail.error||(data.detail.scope==='detail'?'Прочитана полная страница объявления.':'Доступны сведения каталога или метаданные.')}}
    catch{if(selected===id){const note=document.querySelector('.gallery-status');if(note)note.textContent='Остальные фотографии пока недоступны. Можно открыть оригинал объявления.'}return}
  }
  if(version===requestVersion&&currentDetail?.detail.pending){const note=document.querySelector('.gallery-status');if(note)note.textContent='Пока показаны доступные фото. Остальные появятся при обновлении карточки.'}
}
function selectDetailTab(key){activeTab=key;if(key==='analysis'&&!currentDetail.analysis&&!analysisRunningFor){if(analysisAvailable()&&currentlyVisible(currentDetail.item)){analysisLoading=true;renderDetail();runAnalysis();return}analysisError=currentlyVisible(currentDetail.item)?'Модель для разбора не подключена.':'Для этого объявления нет свежего подтверждения источника.'}renderDetail()}
function renderTab(){const x=currentDetail.item,d=currentDetail.detail,a=currentDetail.analysis,container=el('tabContent');if(activeTab==='facts'){let facts=(x.area_m2?fact('Площадь',x.area_m2+' м²'):'')+fact('Город',x.locality)+ (x.category!=='car'?fact('Право на объект',x.ownership):'');if(x.category==='garage')facts+=fact('Тип',x.garage_type);if(['garage','house','land'].includes(x.category))facts+=fact('Права на землю',x.land_rights);for(let[k,label]of Object.entries(LABELS))if(x.attributes[k]!==undefined)facts+=fact(label,x.attributes[k]);container.innerHTML=`<dl class="facts">${facts}</dl><p class="muted">Сведения источника. Заявления продавца требуют проверки.</p><details class="seller-text"><summary>Полный текст продавца</summary><p class="prose">${esc(d.description||'Описание не получено')}</p></details>${d.params?`<details class="seller-text"><summary>Дополнительные характеристики</summary><p class="prose">${esc(d.params)}</p></details>`:''}<p class="muted detail-status">${esc(d.error|| (d.scope==='detail'?'Прочитана полная страница объявления.':'Доступны только сведения каталога или метаданные.'))}</p>`;return}if(!a){const message=analysisLoading?'Разбор запущен. Проверяю данные объекта. Результат появится здесь автоматически.':analysisError||'Разбор ещё не запускался.';container.innerHTML=`<p class="notice" role="status">${esc(message)}</p>${!analysisLoading?`<button id="tabAnalysisAction" class="primary">${analysisAvailable()?'Запустить анализ ИИ':'Открыть настройки ИИ'}</button>`:''}`;el('tabAnalysisAction')?.addEventListener('click',()=>analysisAvailable()?runAnalysis():openSettings());return}let sections=reportSections(a);sections=sections.filter(s=>!/краткий вывод/i.test(s.title));container.innerHTML=sections.map((s,i)=>`<details class="report-fold"${i===0?' open':''}><summary>${esc(s.title)}</summary><p class="prose">${esc(s.body)}</p></details>`).join('')||'<p class="notice">В готовом разборе этот раздел не выделен. Полный ответ доступен во вкладке «Анализ ИИ».</p>';container.insertAdjacentHTML('afterbegin',evidenceStrip(a));container.insertAdjacentHTML('beforeend','<div class="sources"><h3>Источники разбора</h3></div>');let valid=0;for(const c of a.citations||[]){try{const u=new URL(c.url);if(!['https:','http:'].includes(u.protocol))continue;let link=document.createElement('a');link.href=u.href;link.textContent=c.title||u.hostname;link.target='_blank';link.rel='noopener noreferrer';container.querySelector('.sources').append(link);valid++}catch{}}if(!valid)container.querySelector('.sources').remove();container.insertAdjacentHTML('beforeend','<p class="muted">Вывод ИИ не заменяет проверку документов, осмотр и диагностику.</p>');}
async function toggleFavorite(){const x=currentDetail.item;try{const r=await api('/api/favorite',{id:x.id});x.favorite=r.favorite;x.meta=r.meta;const row=items.find(i=>i.id===x.id);if(row){row.favorite=r.favorite;row.meta=r.meta;}renderRows();renderDetail();toast(r.favorite?'Сохранено в избранное':'Убрано из избранного')}catch(e){toast(e.message)}}
async function runAnalysis(){const id=selected;if(!id||!currentDetail||!currentlyVisible(currentDetail.item)||currentDetail.analysis)return;if(analysisRunningFor){if(analysisRunningFor!==id)analysisQueuedFor=id;return}analysisRunningFor=id;analysisLoading=true;analysisError='';renderDetail();try{const a=await api('/api/analyze',{id});if(selected===id&&currentDetail)currentDetail.analysis=a}catch(e){if(selected===id){analysisError=e.message;toast(e.message)}}finally{analysisRunningFor=null;const next=analysisQueuedFor;analysisQueuedFor=null;if(next&&next===selected&&currentDetail&&!currentDetail.analysis){analysisLoading=true;runAnalysis()}else{analysisLoading=false;if(selected&&currentDetail)renderDetail()}}}
async function connect(){try{el('connect').disabled=true;const data=await api('/api/connect',{});location.assign(data.url)}catch(e){toast(e.message);el('connect').disabled=false}}
async function openSettings(){if(!el('settings').open)el('settings').showModal();try{state=await api('/api/state')}catch(e){toast(e.message)}renderServiceState();const progress=state.auth_progress||{};el('accountStatus').textContent=state.chatgpt.connected?'Подключён аккаунт '+(state.chatgpt.email||'ChatGPT'):(state.chatgpt.login_required?'Сеанс ChatGPT истёк. Войди повторно на компьютере, чтобы вернуть анализ фотографий.':progress.message||'Вход в ChatGPT доступен на компьютере с Радаром.');el('connect').hidden=state.chatgpt.connected;el('modelSection').hidden=!state.chatgpt.connected;if(state.chatgpt.connected){try{const d=await api('/api/models');state.model=d.selected;el('model').replaceChildren();for(const m of d.models){const o=document.createElement('option');o.value=m.id;o.textContent=m.name;o.selected=m.id===state.model;el('model').append(o)}}catch(e){el('accountStatus').textContent+=' Не удалось загрузить модели: '+e.message}}}
el('disconnect').onclick=async()=>{try{const r=await api('/api/disconnect',{});state.chatgpt.connected=false;el('modelSection').hidden=true;el('connect').hidden=false;el('connection').textContent='ChatGPT отключён';el('accountStatus').textContent=r.remote_confirmed?'Подключение отключено.':'Локальный доступ отключён. Отзыв на сервере не подтверждён; отключи приложение также в настройках ChatGPT.'}catch(e){toast(e.message)}};
el('settingsButton').onclick=openSettings;el('closeSettings').onclick=()=>el('settings').close();el('connect').onclick=connect;el('saveModel').onclick=async()=>{try{await api('/api/model',{model:el('model').value});state.model=el('model').value;toast('Модель сохранена.')}catch(e){toast(e.message)}};el('sort').onchange=async()=>{await api('/api/preferences',{sort:el('sort').value});await loadList()};el('favoriteFilter').onclick=()=>{favoriteOnly=!favoriteOnly;visibleCount=10;el('favoriteFilter').classList.toggle('active',favoriteOnly);loadList()};el('closePhoto').onclick=()=>el('photoViewer').close();
function renderServiceState(){
  el('connection').textContent=state.chatgpt.connected?'ChatGPT подключён':state.local_ai?'Ollama · анализ текста':'ИИ не подключён';
  const box=el('deliveryStatus'),delivery=state.delivery;
  if(!box||!delivery)return;
  box.querySelector('strong').textContent=delivery.enabled?delivery.schedule+' · МСК':'Рассылка выключена';
  box.querySelector('p').textContent=(delivery.volume==='all'?'Все новые автоматически':'До 10 новых за выпуск')+' · '+delivery.categories.join(' · ');
  box.querySelector('small').textContent=delivery.last_check?'Последняя проверка '+delivery.last_check:'Ожидается первая проверка';
}
(async()=>{try{state=await api('/api/state');renderServiceState();for(const[k,label]of Object.entries({all:'Все категории',...state.categories})){const b=document.createElement('button');b.textContent=label;b.dataset.category=k;b.className=k===category?'active':'';b.onclick=async()=>{await api('/api/search/activate',{id:'',category:k});state.active_profile='';profileDraft=null;renderProfiles();fillSearch(null);selectCategory(k);favoriteOnly=false;el('favoriteFilter').classList.remove('active');await loadList()};el('categories').append(b)}initWorkspace();await loadList();const params=new URLSearchParams(location.search);if(params.get('item'))await openItem(params.get('item'));if(state.notice){toast(state.notice);openSettings()}if(params.has('connect')){history.replaceState(null,'','/');await connect()}}catch(e){toast(e.message)}})();
