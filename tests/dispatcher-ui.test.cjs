// Script-level integration in an offline DOM stub; this is not visual browser QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Data = require('../frontend/dispatcher/assets/data.js');
const source=fs.readFileSync('frontend/dispatcher/assets/app.js','utf8');
const html=fs.readFileSync('frontend/dispatcher/index.html','utf8');

function harness() {
  const elements=new Map();
  function element() {return {textContent:'',innerHTML:'',disabled:false,hidden:false,value:'all',dataset:{},listeners:{},
    addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},
    style:{setProperty(){}},classList:{toggle(){},contains(){return false;}},querySelector(){return null;}};}
  for(const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1],element());
  const document={getElementById:id=>elements.get(id)||null,querySelector:()=>element(),querySelectorAll:()=>[],addEventListener(){},body:element()};
  const api={mode:'live',reference_time:new Date().toISOString(),demo_plan:false,vehicles:[]};
  let pending=null, calls=0, fail=false;
  const context={window:{DispatcherData:Data},document,console,Intl,Date,Number,Array,String,Boolean,Math,AbortSignal,
    requestAnimationFrame:fn=>fn(),clearTimeout(){},setTimeout:fn=>{pending=fn;return 1;},
    fetch:async(url)=>{assert.equal(url,'/vehicles/active');calls++;if(fail)throw Error('offline');return {ok:true,json:async()=>api};}};
  vm.runInNewContext(source,context);
  return {api,elements,tick:async()=>{await new Promise(setImmediate);},poll:()=>pending(),fail:()=>{fail=true;},recover:()=>{fail=false;},calls:()=>calls};
}

test('empty -> populated -> empty zone -> lost connection -> recovery', async()=>{
  const h=harness(); await h.tick();
  assert.equal(h.elements.get('stat-vehicles').textContent,0);
  assert.match(h.elements.get('vehicle-detail').innerHTML,/Нет выбранного/);
  assert.equal(h.elements.get('refresh-button').disabled,false);
  h.api.vehicles=[{tr_id:42,position:[55.75,37.5],event_time:new Date().toISOString(),telemetry_age_s:1,
    current_delay_s:70,speed_kmh:15,track:[[[55.75,37.5],[55.76,37.51]]],stops:[],has_schedule:false,prediction:null}];
  await h.poll();
  assert.match(h.elements.get('vehicle-list').innerHTML,/42/);
  assert.match(h.elements.get('vehicle-detail').innerHTML,/Прогноза пока нет/);
  h.elements.get('scope-select').listeners.change({target:{value:'east'}});
  assert.match(h.elements.get('vehicle-detail').innerHTML,/Нет выбранного/);
  h.fail();await h.poll();
  assert.match(h.elements.get('server-status').textContent,/Нет актуального ответа/);
  assert.equal(h.elements.get('refresh-button').disabled,false);
  h.recover();h.api.vehicles=[];await h.poll();
  assert.equal(h.elements.get('stat-vehicles').textContent,0);
  assert.equal(h.calls(),4);
});
