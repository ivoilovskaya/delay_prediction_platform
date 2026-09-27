// Script-level integration in an offline DOM stub; this is not visual browser QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Data = require('../frontend/dispatcher/assets/data.js');
const source=fs.readFileSync('frontend/dispatcher/assets/app.js','utf8');
const html=fs.readFileSync('frontend/dispatcher/index.html','utf8');

function harness(withMap=false) {
  const elements=new Map();
  function element() {return {textContent:'',innerHTML:'',disabled:false,hidden:false,value:'all',dataset:{},listeners:{},
    addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},
    style:{setProperty(){}},classList:{toggle(){},contains(){return false;}},querySelector(){return null;}};}
  for(const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1],element());
  const document={createElement:()=>element(),getElementById:id=>elements.get(id)||null,querySelector:()=>element(),querySelectorAll:()=>[],addEventListener(){},body:element()};
  const api={mode:'live',reference_time:new Date().toISOString(),demo_plan:false,vehicles:[]};
  let pending=null, calls=0, fail=false;
  const context={window:{DispatcherData:Data},document,console,Intl,Date,Number,Array,String,Boolean,Math,AbortSignal,
    requestAnimationFrame:fn=>fn(),clearTimeout(){},setTimeout:fn=>{pending=fn;return 1;},
    fetch:async(url)=>{assert.equal(url,'/vehicles/active');calls++;if(fail)throw Error('offline');return {ok:true,json:async()=>api};}};
  const shapes=[];
  if (withMap) {
    const shape=(kind,coords,options)=>({kind,coords,options,tooltip:null,
      addTo(layer){layer.items?.push(this);return this;},on(){return this;},
      bindTooltip(text){this.tooltip=text;return this;},getContainer(){return {style:{}};}});
    const leaflet={
      map:()=>({setView(){return this;},hasLayer(){return true;},fitBounds(){},invalidateSize(){}}),
      control:{zoom:()=>shape('control')},tileLayer:()=>shape('tiles'),
      layerGroup:()=>({items:[],addTo(){return this;},clearLayers(){this.items.length=0;}}),
      divIcon:options=>options,latLngBounds:coords=>coords,
      marker:(coords,options)=>shape('marker',coords,options),
      polyline:(coords,options)=>{const s=shape('line',coords,options);shapes.push(s);return s;},
      circleMarker:(coords,options)=>{const s=shape('forecast',coords,options);shapes.push(s);return s;},
    };
    context.window.L=leaflet;context.L=leaflet;
  }
  vm.runInNewContext(source,context);
  return {api,elements,shapes,tick:async()=>{await new Promise(setImmediate);},poll:()=>pending(),fail:()=>{fail=true;},recover:()=>{fail=false;},calls:()=>calls};
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

test('stale and disconnected forecasts disappear from all card fields and counters', async () => {
  const h=harness(); await h.tick();
  const now=new Date().toISOString();
  const prediction={status:'ready',predicted_delay_s:777,target_stop_id:9,target_stop_name:'Прогнозная остановка',
    target_time_plan:now,predicted_arrival:now,predicted_at:now,t_forecast:now,
    model_used:'INTERNAL_MODEL',fallback_reason:'INTERNAL_REASON',interval_lo_s:700,interval_hi_s:800};
  h.api.vehicles=[{tr_id:42,position:[55.75,37.5],event_time:now,telemetry_age_s:1,
    current_delay_s:0,track:[],stops:[],has_schedule:true,prediction}];
  await h.poll();
  assert.equal(h.elements.get('stat-forecasts').textContent,1);
  assert.match(h.elements.get('vehicle-detail').innerHTML,/Прогнозная остановка/);
  assert.doesNotMatch(h.elements.get('vehicle-detail').innerHTML,/INTERNAL_MODEL|INTERNAL_REASON/);
  h.fail(); await h.poll();
  assert.equal(h.elements.get('stat-forecasts').textContent,0);
  assert.doesNotMatch(h.elements.get('vehicle-detail').innerHTML,/Прогнозная остановка|Интервал задержки/);
  h.recover(); prediction.status='stale'; await h.poll();
  assert.equal(h.elements.get('stat-forecasts').textContent,0);
  assert.doesNotMatch(h.elements.get('vehicle-list').innerHTML,/Прогнозная остановка|INTERNAL_MODEL/);
  assert.doesNotMatch(h.elements.get('vehicle-detail').innerHTML,/Прогнозная остановка|Интервал задержки/);
});

test('map marks current segment red and target yellow, removes stale forecast tooltip', async () => {
  const h=harness(true);await h.tick();
  const now=new Date().toISOString();
  const p={status:'ready',predicted_delay_s:420,target_stop_id:9,target_stop_name:'Цель',target_position:[55.8,37.6],
    target_time_plan:now,predicted_arrival:now,predicted_at:now,t_forecast:now};
  h.api.vehicles=[{tr_id:42,position:[55.75,37.5],event_time:now,telemetry_age_s:1,current_delay_s:400,
    track:[],has_schedule:true,prediction:p,stops:[
      {id:8,position:[55.77,37.55],name:'Следующая',planned_time:now},
      {id:9,position:[55.8,37.6],name:'Цель',planned_time:now}]}];
  h.shapes.length=0;await h.poll();
  assert.ok(h.shapes.some(s=>s.kind==='line' && s.options.color==='#ca2437' && s.coords[1][0]===55.77));
  assert.ok(h.shapes.some(s=>s.kind==='line' && s.options.color==='#e5a32b' && s.coords[1][0]===55.8));
  assert.equal(h.shapes.filter(s=>s.kind==='forecast').length,1);
  p.status='stale';h.shapes.length=0;await h.poll();
  assert.equal(h.shapes.filter(s=>s.kind==='forecast').length,0);
  assert.ok(!h.shapes.some(s=>s.options.color==='#e5a32b'));
  assert.ok(h.shapes.some(s=>s.kind==='line' && s.options.color==='#ca2437'));
});
