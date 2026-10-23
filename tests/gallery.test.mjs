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
