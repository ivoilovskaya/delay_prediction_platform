const {test} = require('node:test');
const assert = require('node:assert/strict');
const Data = require('../frontend/dispatcher/assets/data.js');
const input = () => ({mode:'live', reference_time:'2026-09-27T10:00:00Z', vehicles:[{
  tr_id:42, unit_id:100, position:[55.75,37.5], event_time:'2026-09-27T09:59:59Z',
  telemetry_age_s:1, speed_kmh:20, current_delay_s:30, status:'on_route', has_schedule:true,
  track:[[[55.75,37.5],[55.76,37.51]]],
  stops:[{id:9, position:[55.8,37.6], planned_time:'2026-09-27T10:12:00Z', name:'Стоп'}],
  prediction:{predicted_delay_s:420, target_stop_id:9, target_position:[55.8,37.6], target_stop_name:'Стоп',
    target_time_plan:'2026-09-27T10:12:00Z', predicted_arrival:'2026-09-27T10:19:00Z',
    t_forecast:'2026-09-27T10:00:00Z', predicted_at:'2026-09-27T10:00:00Z',
    status:'ready', model_used:'ensemble', degraded:false, fallback_reason:null, interval_lo_s:300, interval_hi_s:500}
}]});

test('API values reach cards unchanged; timestamps shown in Moscow',()=>{
  const frame=Data.adapt(input()), v=frame.vehicles[0];
  assert.equal(frame.time,'13:00:00'); assert.equal(v.id,'42');
  assert.equal(v.currentDelay,30); assert.equal(v.forecast.delay,420);
  assert.equal(v.forecast.arrival,'13:19:00'); assert.deepEqual(v.forecast.target,[55.8,37.6]);
  assert.equal(Data.signal(v,300).kind,'risk');
});
test('current delay and forecast are separate; stale and disconnected predictions do not trigger risk',()=>{
  const v=Data.adapt(input()).vehicles[0];
  v.currentDelay=400; assert.equal(Data.signal(v,300).kind,'late');
  v.currentDelay=null; v.forecast.status='stale'; assert.equal(Data.signal(v,300).kind,'none');
  v.forecast.status='ready'; assert.equal(Data.signal(v,300,false).kind,'none');
  v.age=121; assert.equal(Data.signal(v,300).kind,'none');
});
test('empty response, no selection, no prediction, invalid coordinates are safe',()=>{
  const data=input(); data.vehicles[0].prediction=null;
  assert.equal(Data.adapt(data).vehicles[0].forecast,null);
  assert.equal(Data.signal(undefined,300).kind,'none');
  data.vehicles=[]; assert.deepEqual(Data.adapt(data).vehicles,[]);
  data.vehicles=[{position:[NaN,37]}]; assert.deepEqual(Data.adapt(data).vehicles,[]);
  assert.throws(()=>Data.adapt({mode:'live',vehicles:null}));
});
test('fallback metadata is preserved and null delay is not converted to zero',()=>{
  const data=input(); data.vehicles[0].current_delay_s=null;
  Object.assign(data.vehicles[0].prediction,{model_used:'baseline',degraded:true,fallback_reason:'model failed'});
  const v=Data.adapt(data).vehicles[0];
  assert.equal(v.currentDelay,null); assert.equal(v.forecast.model,'baseline');
  assert.equal(v.forecast.reason,'model failed'); assert.equal(v.forecast.degraded,true);
});
