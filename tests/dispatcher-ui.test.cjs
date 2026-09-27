const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Data = require('../frontend/dispatcher/assets/data.js');
const source = fs.readFileSync('frontend/dispatcher/assets/app.js', 'utf8');
const html = fs.readFileSync('frontend/dispatcher/index.html', 'utf8');

function harness() {
  const elements = new Map();
  function element() {
    return {textContent:'',innerHTML:'',disabled:false,hidden:false,value:'',dataset:{},listeners:{},
      addEventListener(type,fn){this.listeners[type]=fn;},insertAdjacentHTML(_where,markup){this.innerHTML+=markup;}};
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1],element());
  const document = {getElementById:id=>elements.get(id)||null};
  const reference = '2026-09-27T09:03:50+00:00';
  const responses = {
    '/vehicles/active': {mode:'historical',reference_time:reference,vehicles:[]},
    '/analytics/segments': {segments:[]},
    '/analytics/vehicles': {vehicles:[]},
  };
  let pending, offline = false;
  const urls=[];
  const context = {window:{DispatcherData:Data},document,console,Intl,Date,Number,Array,String,Boolean,Math,AbortSignal,
    clearTimeout(){},setTimeout:fn=>{pending=fn;return 1;},
    fetch:async url=>{urls.push(url);if(offline)throw Error('offline');return {ok:true,json:async()=>responses[url]};}};
  vm.runInNewContext(source,context);
  return {elements,responses,urls,tick:()=>new Promise(setImmediate),poll:()=>pending(),offline:value=>{offline=value;}};
}

test('shows route risk, incident details and unavailable-data state', async () => {
  const h = harness(); await h.tick();
  assert.deepEqual(h.urls.sort(), ['/analytics/segments','/analytics/vehicles','/vehicles/active']);
  assert.match(h.elements.get('summary').textContent,/Маршруты пока не рассчитаны/);
  const segment = {segment_id:'r:0',route_id:'r',route_name:'Маршрут Р',direction_id:'в центр',
    from_name:'А',to_name:'Б',severity:'WARNING',vehicles_considered:2,vehicles_confirming_slowdown:2,
    baseline_travel_time_sec:300,median_current_travel_time_sec:480,slowdown_ratio:1.6};
  h.responses['/analytics/segments'].segments=[segment];
  h.responses['/analytics/vehicles'].vehicles=[{tr_id:42,event_time:h.responses['/vehicles/active'].reference_time,
    route_id:'r',direction_id:'в центр',segment_id:'r:0',match_status:'MATCHED'}];
  h.responses['/vehicles/active'].vehicles=[{tr_id:42,position:[55.75,37.6],event_time:h.responses['/vehicles/active'].reference_time,
    telemetry_age_s:0,track:[],stops:[],has_schedule:false,prediction:null}];
  await h.poll();
  assert.equal(h.elements.get('count-red').textContent,1);
  assert.match(h.elements.get('incident-list').innerHTML,/А → Б/);
  assert.match(h.elements.get('incident-detail').innerHTML,/ТС 42/);
  assert.match(h.elements.get('incident-detail').innerHTML,/Замедление движения по GPS/);
  h.offline(true);await h.poll();
  assert.match(h.elements.get('summary').textContent,/Данные временно недоступны/);
  assert.equal(h.elements.get('refresh').disabled,false);
});
