/* One dispatcher view: route risk, vehicle positions, and incident details. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const Data = window.DispatcherData;
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const minutes = value => Number.isFinite(value) ? `${value >= 0 ? '+' : '−'}${new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1}).format(Math.abs(value) / 60)} мин` : '—';
  const duration = value => Number.isFinite(value) ? `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1}).format(value / 60)} мин` : '—';
  const position = value => Array.isArray(value) && value.length === 2 && value.every(Number.isFinite);
  const state = {route:'', selected:null, fleet:{mode:'live', reference_time:null, vehicles:[]}, segments:[], assignments:[], error:null};
  let map, routeLayer, vehicleLayer, vehicleRenderer, mapReady = false, pollTimer, loading = false;
  const severityOrder = {CRITICAL:3, WARNING:2, WATCH:1, NORMAL:0};

  function tone(segment) {
    if (segment.severity === 'CRITICAL' || segment.severity === 'WARNING') return 'red';
    if (segment.severity === 'WATCH') return 'yellow';
    if (segment.vehicles_considered > 0) return 'green';
    return 'gray';
  }

  function matchedVehicles() {
    const reference = Date.parse(state.fleet.reference_time);
    const fleet = new Map(Data.adapt(state.fleet).vehicles.map(vehicle => [vehicle.id, vehicle]));
    return state.assignments.flatMap(row => {
      const vehicle = fleet.get(String(row.tr_id));
      if (!vehicle || row.match_status !== 'MATCHED' || !row.segment_id ||
          !Number.isFinite(reference) || Math.abs(reference - Date.parse(row.event_time)) > 120000) return [];
      return [{...vehicle, routeId:row.route_id, directionId:row.direction_id, segmentId:row.segment_id}];
    });
  }

  function forecastUsable(vehicle) {
    const forecast = vehicle.forecast;
    if (!forecast || !Number.isFinite(forecast.delay) || vehicle.age > 120) return false;
    if (state.fleet.mode === 'live') return forecast.status === 'ready';
    const difference = Math.abs(Date.parse(forecast.forecastAt) - Date.parse(state.fleet.reference_time));
    return Number.isFinite(difference) && difference <= 120000;
  }

  function incidents() {
    const matched = matchedVehicles();
    const rows = state.segments.filter(segment => segment.severity === 'WATCH' ||
      segment.severity === 'WARNING' || segment.severity === 'CRITICAL').map(segment => ({
      id:`segment:${segment.segment_id}`, segment, tone:tone(segment),
      vehicles:matched.filter(vehicle => vehicle.segmentId === segment.segment_id),
    }));
    const reported = new Set(rows.flatMap(incident => incident.vehicles.map(vehicle => vehicle.id)));
    const fleet = Data.adapt(state.fleet).vehicles;
    for (const vehicle of fleet) {
      if (reported.has(vehicle.id) || !forecastUsable(vehicle) || vehicle.forecast.delay < 300) continue;
      const route = matched.find(item => item.id === vehicle.id);
      rows.push({id:`forecast:${vehicle.id}`, segment:state.segments.find(item => item.segment_id === route?.segmentId) || null,
                 tone:'yellow', vehicles:[vehicle], forecastOnly:true});
    }
    return rows.filter(item => !state.route || item.segment?.route_id === state.route)
      .sort((a,b) => ({red:2,yellow:1}[b.tone] - {red:2,yellow:1}[a.tone]) ||
        (severityOrder[b.segment?.severity] || 0) - (severityOrder[a.segment?.severity] || 0));
  }

  function selectedSegments() {
    return state.segments.filter(segment => !state.route || segment.route_id === state.route);
  }

  function routeName(id) {
    return state.segments.find(segment => segment.route_id === id)?.route_name || id;
  }

  function renderHeader() {
    const segments = selectedSegments();
    const counts = {red:0,yellow:0,green:0};
    segments.forEach(segment => {if (tone(segment) in counts) counts[tone(segment)] += 1;});
    $('count-red').textContent = counts.red;
    $('count-yellow').textContent = counts.yellow;
    $('count-green').textContent = counts.green;
    $('summary').textContent = state.error || (segments.length ? `${segments.length} участков · ${state.fleet.mode === 'historical' ? 'исторические данные' : 'текущие данные'}` : 'Маршруты пока не рассчитаны');
    $('data-mode').textContent = state.fleet.mode === 'historical' ? 'Исторический режим' : 'Живые данные';
    $('reference-time').textContent = `Состояние: ${state.fleet.reference_time ? new Date(state.fleet.reference_time).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}) + ' МСК' : 'нет данных'}`;
    $('map-title').textContent = state.route ? routeName(state.route) : 'Все маршруты';
    const matched = matchedVehicles();
    const all = Data.adapt(state.fleet).vehicles;
    $('vehicle-count').textContent = state.route ? `${matched.filter(vehicle => vehicle.routeId === state.route).length} ТС на маршруте` : `${all.length} ТС на карте`;
  }

  function renderRouteFilter() {
    const options = [...new Map(state.segments.map(segment => [segment.route_id, segment.route_name])).entries()]
      .sort((a,b) => a[1].localeCompare(b[1], 'ru'));
    if (state.route && !options.some(([id]) => id === state.route)) state.route = '';
    $('route-filter').innerHTML = '<option value="">Все маршруты</option>' +
      options.map(([id,name]) => `<option value="${escapeHTML(id)}">${escapeHTML(name)}</option>`).join('');
    $('route-filter').value = state.route;
  }

  function renderIncidents(items) {
    $('incident-count').textContent = items.length;
    if (!items.length) {
      $('incident-list').innerHTML = `<p class="empty">${state.segments.length ? 'Нет активных предупреждений для выбранного маршрута.' : 'Маршруты пока не загружены. Карта покажет их после расчёта.'}</p>`;
      $('incident-detail').hidden = true;
      return;
    }
    if (!items.some(item => item.id === state.selected)) state.selected = items[0].id;
    $('incident-list').innerHTML = items.slice(0,30).map(item => {
      const segment = item.segment;
      const title = segment ? `${segment.from_name} → ${segment.to_name}` : `ТС ${item.vehicles[0]?.id}`;
      const subtitle = item.forecastOnly ? 'Прогноз опоздания' : item.tone === 'red' ? 'Высокий риск' : 'Требует внимания';
      const source = segment ? `${segment.route_name} · ${segment.direction_id}` : 'Участок не установлен';
      return `<button type="button" class="incident-item" data-id="${escapeHTML(item.id)}" data-tone="${item.tone}" aria-pressed="${item.id === state.selected}"><small>${escapeHTML(subtitle)} · ${escapeHTML(source)}</small><strong>${escapeHTML(title)}</strong><span>${item.vehicles.length ? `ТС: ${item.vehicles.slice(0,4).map(v => escapeHTML(v.id)).join(', ')}${item.vehicles.length > 4 ? '…' : ''}` : 'ТС на участке не определены'}</span></button>`;
    }).join('');
    if (items.length > 30) $('incident-list').insertAdjacentHTML('beforeend', `<p class="empty">Показаны первые 30 из ${items.length} сигналов. Выберите маршрут.</p>`);
    renderDetail(items.find(item => item.id === state.selected));
  }

  function renderDetail(item) {
    const card = $('incident-detail');
    if (!item) {card.hidden = true;return;}
    const segment = item.segment;
    const forecast = item.vehicles.filter(forecastUsable).sort((a,b) => b.forecast.delay - a.forecast.delay)[0];
    const vehicleText = item.vehicles.length ? item.vehicles.map(v => `ТС ${v.id}`).join(', ') : 'ТС на участке не определены';
    const reason = segment && segment.vehicles_confirming_slowdown >= 2 && segment.slowdown_ratio >= 1.4
      ? 'Замедление движения по GPS; внешняя причина не установлена' : 'Причина пока не установлена';
    card.dataset.tone = item.tone;
    card.innerHTML = `<button class="detail-close" type="button" aria-label="Закрыть карточку">×</button><span class="detail-kicker">${item.forecastOnly ? 'ПРОГНОЗ ТС' : item.tone === 'red' ? 'ВЫСОКИЙ РИСК' : 'ВНИМАНИЕ'}</span><h3>${escapeHTML(segment ? `${segment.from_name} → ${segment.to_name}` : `ТС ${item.vehicles[0]?.id}`)}</h3><p class="detail-route">${escapeHTML(segment ? `${segment.route_name} · ${segment.direction_id}${segment.is_demo ? ' · КАНДИДАТ' : ''}` : 'Участок маршрута не определён')}</p><div class="detail-grid"><div><small>Обычно / сейчас</small><b>${segment ? `${duration(segment.baseline_travel_time_sec)} / ${duration(segment.median_current_travel_time_sec)}` : '—'}</b></div><div><small>Прогноз опоздания ТС</small><b>${forecast ? minutes(forecast.forecast.delay) : 'нет прогноза'}</b></div></div><div class="detail-line"><b>Транспорт</b>${escapeHTML(vehicleText)}</div><div class="detail-line"><b>Предполагаемый фактор</b>${escapeHTML(reason)}</div><div class="detail-line"><b>Участок</b>${escapeHTML(segment ? `${segment.from_name} → ${segment.to_name}` : 'Не определён')}</div><p class="detail-foot">${forecast ? `Прогноз для остановки «${escapeHTML(forecast.forecast.address)}». Его связь с данным участком не подтверждена. ` : ''}${segment ? `Подтверждений замедления: ${segment.vehicles_confirming_slowdown} из ${segment.vehicles_considered}. ` : ''}Причины ДТП, пробки или поломки по этим данным не определяются.</p>`;
    card.hidden = false;
  }

  function setupMap() {
    if (!window.L) {
      $('map-message').hidden = false;
      $('map-message').textContent = 'Карта не загрузилась. Проверьте интернет; список инцидентов остаётся доступен.';
      return;
    }
    map = L.map('map', {zoomControl:false, preferCanvas:true}).setView([55.7512,37.6184],11);
    L.control.zoom({position:'topright'}).addTo(map);
    const fallback = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
    if (L.maplibreGL) {
      const modern = L.maplibreGL({style:'https://tiles.openfreemap.org/styles/positron'}).addTo(map);
      modern.getMaplibreMap?.()?.on('load', () => {if (map.hasLayer(fallback)) map.removeLayer(fallback);});
    }
    routeLayer = L.layerGroup().addTo(map);
    vehicleLayer = L.layerGroup().addTo(map);
    vehicleRenderer = L.canvas({padding:.3});
  }

  function drawMap(items, fit = false) {
    if (!map) return;
    routeLayer.clearLayers(); vehicleLayer.clearLayers();
    const visible = selectedSegments();
    const bySegment = new Map(items.filter(item => item.segment).map(item => [item.segment.segment_id,item]));
    for (const segment of visible) {
      const coords = [[segment.from_lat,segment.from_lon],[segment.to_lat,segment.to_lon]];
      if (!coords.flat().every(Number.isFinite)) continue;
      const color = {red:'#d72e35',yellow:'#e9ac25',green:'#379064',gray:'#aab1b4'}[tone(segment)];
      L.polyline(coords,{color:'#fff',weight:10,opacity:.9,interactive:false}).addTo(routeLayer);
      const line = L.polyline(coords,{color,weight:6,opacity:.95}).addTo(routeLayer);
      const label = document.createElement('span');
      label.textContent = `${segment.route_name}: ${segment.from_name} → ${segment.to_name}`;
      line.bindTooltip(label);
      const incident = bySegment.get(segment.segment_id);
      if (incident) line.on('click', () => selectIncident(incident.id, true));
    }
    const matched = matchedVehicles();
    const candidates = state.route ? matched.filter(vehicle => vehicle.routeId === state.route) : Data.adapt(state.fleet).vehicles;
    for (const vehicle of candidates) {
      if (!position(vehicle.position)) continue;
      const marker = L.circleMarker(vehicle.position,{renderer:vehicleRenderer,radius:5,color:'#fff',weight:2,fillColor:'#343b3e',fillOpacity:1}).addTo(vehicleLayer);
      const label = document.createElement('span'); label.textContent = `ТС ${vehicle.id}`;
      marker.bindTooltip(label);
      const incident = items.find(item => item.vehicles.some(v => v.id === vehicle.id));
      if (incident) marker.on('click', () => selectIncident(incident.id, true));
    }
    if (fit && !mapReady) {
      const coords = visible.flatMap(segment => [[segment.from_lat,segment.from_lon],[segment.to_lat,segment.to_lon]])
        .filter(position);
      if (coords.length) {map.fitBounds(L.latLngBounds(coords),{padding:[70,70],maxZoom:15,animate:false});mapReady = true;}
    }
  }

  function selectIncident(id, focus) {
    state.selected = id;
    const items = incidents();
    renderIncidents(items);
    const item = items.find(row => row.id === id);
    if (focus && item?.segment && map) map.fitBounds(L.latLngBounds([
      [item.segment.from_lat,item.segment.from_lon],[item.segment.to_lat,item.segment.to_lon]
    ]),{padding:[100,100],maxZoom:15,animate:false});
    $('announcement').textContent = item?.segment ? `Выбран участок ${item.segment.from_name} — ${item.segment.to_name}` : 'Выбран инцидент';
  }

  function render(fit = false) {
    renderRouteFilter(); renderHeader();
    const items = incidents();
    renderIncidents(items); drawMap(items,fit);
  }

  async function request(url) {
    const response = await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    return response.json();
  }

  async function refresh() {
    if (loading) return;
    loading = true; clearTimeout(pollTimer); $('refresh').disabled = true;
    try {
      const [fleet, segments, assignments] = await Promise.all([
        request('/vehicles/active'),request('/analytics/segments'),request('/analytics/vehicles')
      ]);
      Data.adapt(fleet);
      state.fleet = fleet; state.segments = segments.segments || []; state.assignments = assignments.vehicles || [];
      state.error = null;
      if (map) $('map-message').hidden = true;
      $('last-update').textContent = `Обновлено ${Data.clock(new Date().toISOString())}`;
      render(true);
    } catch {
      state.error = 'Данные временно недоступны. Повторяем запрос.';
      $('data-mode').textContent = 'Нет связи';
      $('summary').textContent = state.error;
      $('map-message').hidden = false;
      $('map-message').textContent = 'Нет актуального ответа FastAPI. Показанные данные могут устареть.';
    } finally {
      loading = false; $('refresh').disabled = false;
      pollTimer = setTimeout(refresh,15000);
    }
  }

  $('route-filter').addEventListener('change', event => {state.route = event.target.value; state.selected = null; mapReady = false; render(true);});
  $('incident-list').addEventListener('click', event => {
    const button = event.target.closest('[data-id]');
    if (button) selectIncident(button.dataset.id,true);
  });
  $('incident-detail').addEventListener('click', event => {if (event.target.closest('.detail-close')) $('incident-detail').hidden = true;});
  $('refresh').addEventListener('click',refresh);
  setupMap(); refresh();
})();
