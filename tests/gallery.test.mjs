import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../garage_radar/ui.js',import.meta.url),'utf8');
function runtime(){
  const nodes=new Map(),thumbs=Array.from({length:4},(_,i)=>({dataset:{photo:String(i)},setAttribute(key,value){this[key]=value}}));
  const context=vm.createContext({Intl,URL,URLSearchParams,Map,setTimeout:callback=>callback(),
    document:{getElementById(id){if(!nodes.has(id))nodes.set(id,{});return nodes.get(id)},querySelectorAll(){return thumbs}}});
  vm.runInContext(source.slice(0,source.indexOf("el('disconnect').onclick")),context);
  vm.runInContext("selected='one';requestVersion=1;currentDetail={item:{id:'one'},detail:{pending:true,photos:['a','b','c','d']}};",context);
  return {context,nodes,thumbs,run:code=>vm.runInContext(code,context)};
}
test('photo navigation wraps, updates full viewer and selected thumbnail',()=>{
  const r=runtime();r.run('selectPhoto(-1)');
  assert.equal(r.nodes.get('photoCount').textContent,'4 / 4');
  assert.equal(r.nodes.get('largePhoto').src,'/photo?id=one&index=3');
  assert.equal(r.thumbs[3]['aria-pressed'],'true');
  r.run('selectPhoto(4)');assert.equal(r.nodes.get('photoCount').textContent,'1 / 4');
});
test('pending gallery becomes visible without reopening card',async()=>{
  const r=runtime();let requests=0,renders=0;
  r.context.mockApi=async()=>{requests++;return {detail:{photos:['a','b','c','d'],pending:false}}};
  r.context.onRender=()=>renders++;
  r.run('api=mockApi;renderObjectGallery=onRender');
  await r.run("watchGallery('one',1)");
  assert.equal(requests,1);assert.equal(renders,1);assert.equal(r.run('currentDetail.detail.photos.length'),4);
});
test('late response never replaces another listing gallery',async()=>{
  const r=runtime();let renders=0;
  r.context.mockApi=async()=>{r.run("selected='two';requestVersion=2;currentDetail={detail:{photos:['other'],pending:false}};");return {detail:{photos:['wrong'],pending:false}}};
  r.context.onRender=()=>renders++;
  r.run('api=mockApi;renderObjectGallery=onRender');
  await r.run("watchGallery('one',1)");
  assert.equal(renders,0);assert.equal(r.run('currentDetail.detail.photos[0]'),'other');
});

test('closed and expired offers disappear even if the browser holds an old list',()=>{
  const r=runtime();
  assert.equal(r.run("currentlyVisible({active:false})"),false);
  assert.equal(r.run("currentlyVisible({available_until:'2001-01-01T00:00:00Z'})"),false);
  assert.equal(r.run("currentlyVisible({available_until:new Date(Date.now()+60000).toISOString()})"),true);
});

test('numbered and markdown AI sections display an independent brief',()=>{
  const r=runtime();
  const report={text:'1. Краткий вывод\nИзмерить ворота.\n\n## 2. Цена и аналоги\nДва аналога.'};
  r.context.sample=report;
  assert.equal(r.run('brief(sample)'),'Измерить ворота.');
  assert.equal(r.run('reportSections(sample)[1].title'),'Цена и аналоги');
});

test('catalog navigation appends the next page once and reaches the last item',async()=>{
  const r=runtime(), calls=[];
  r.context.document.querySelector=()=>({scrollTop:0});
  r.context.document.getElementById('rows').querySelector=()=>null;
  r.context.fetchPage=async path=>{
    const query=new URL(path,'https://example.org').searchParams;
    calls.push(query.get('after'));
    const offset=Number(query.get('after')||0);
    return {items:Array.from({length:Math.min(10,23-offset)},(_,i)=>({id:String(offset+i)})),
      next:offset<20?String(offset+10):null,count:23};
  };
  r.run("api=fetchPage;renderRows=()=>{};globalThis.listQuery=()=> 'category=all&sort=price_asc&view=all';");
  await r.run('loadList()');
  await r.run('Promise.all([loadList({more:true}),loadList({more:true})])');
  await r.run('loadList({more:true})');
  await r.run('loadList({more:true})');
  assert.deepEqual(calls,[null,'10','20']);
  assert.equal(r.run('items.length'),23);
  assert.equal(r.run('items[22].id'),'22');
});
