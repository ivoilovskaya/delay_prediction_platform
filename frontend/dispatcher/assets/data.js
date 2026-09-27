/* Adapt the FastAPI fleet snapshot to the map. No illustrative predictions. */
(function (root) {
  'use strict';
  const clock = value => value ? new Intl.DateTimeFormat('ru-RU', {hour:'2-digit', minute:'2-digit', second:'2-digit', timeZone:'Europe/Moscow'}).format(new Date(value)) : '—';
  const date = value => value ? new Intl.DateTimeFormat('ru-RU', {dateStyle:'medium', timeZone:'Europe/Moscow'}).format(new Date(value)) : '—';
  const position = value => Array.isArray(value) && value.length === 2 && value.every(Number.isFinite) && Math.abs(value[0]) <= 90 && Math.abs(value[1]) <= 180;
  const stop = value => ({id:value.id, address:value.name || `Точка расписания ${value.id}`, position:value.position, time:clock(value.planned_time)});
  function adapt(data) {
    if (!data || !Array.isArray(data.vehicles) || !['live','historical'].includes(data.mode) ||
        (data.reference_time != null && !Number.isFinite(Date.parse(data.reference_time)))) throw new Error('Некорректный ответ API');
    const reference = Date.parse(data.reference_time);
    return {time:clock(data.reference_time), vehicles:data.vehicles.filter(v => position(v.position)).map(v => {
      const p = v.prediction;
      const f = p && Number.isFinite(p.predicted_delay_s) ? {
        delay:p.predicted_delay_s, address:p.target_stop_name || `Точка расписания ${p.target_stop_id}`,
        target:position(p.target_position) ? p.target_position : null, targetId:p.target_stop_id,
        plan:clock(p.target_time_plan), arrival:clock(p.predicted_arrival),
        horizon:Number.isFinite(reference) ? Math.round((Date.parse(p.target_time_plan) - reference) / 60000) : null,
        status:p.status, degraded:p.degraded, model:p.model_used, reason:p.fallback_reason,
        interval:[p.interval_lo_s, p.interval_hi_s], calculated:clock(p.predicted_at),
        forecastAt:p.t_forecast, predictedAt:p.predicted_at
      } : null;
      return {id:String(v.tr_id), unitId:v.unit_id, position:v.position, speed:v.speed_kmh,
        age:v.telemetry_age_s, eventTime:v.event_time, lastSeen:clock(v.event_time),
        currentDelay:v.current_delay_s, routeStatus:v.status, currentStop:v.current_stop ? stop(v.current_stop) : null,
        nextStop:v.next_stop ? stop(v.next_stop) : null, hasSchedule:v.has_schedule,
        track:(v.track || []).map(segment=>segment.filter(position)).filter(segment=>segment.length),
        stops:(v.stops || []).filter(s=>position(s.position)).map(stop), forecast:f};
    })};
  }
  function fresh(v, connected=true) {
    return Boolean(connected && v && v.age <= 120 && v.forecast?.status === 'ready');
  }
  function signal(v, threshold, connected=true) {
    if (!v || !connected || v.age > 120) return {kind:'none'};
    if (Number.isFinite(v.currentDelay) && v.currentDelay >= threshold) return {kind:'late', current:v.currentDelay};
    if (fresh(v, connected) && v.forecast.delay >= threshold) return {kind:'risk', predicted:v.forecast.delay};
    return {kind:'none'};
  }
  const api = {adapt, signal, fresh, clock, date};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DispatcherData = api;
})(globalThis);
