/* Historical replay is delivered by FastAPI; worker status is reported separately. */
(async () => {
  'use strict';
  const REPLAY = await fetch('/dispatcher/replay', {cache:'no-store'})
    .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
    .catch(() => null);
  const $ = id => document.getElementById(id);
  const state = { frame: 0, selected: '131672', filter: 'forecasts', query: '', playing: false, mapStyle: 'positron', gps: true, stops: true, segmentIndex: null, scenario: 'observed', scope: 'all', threshold: 300 };
  let timer = null, map = null, vehicleLayer = null, trackLayer = null, tiles = null, styleLayer = null;
  const issueColors = { normal:'#a7a4a0', warning:'#e5a32b', critical:'#ca2437', blocked:'#4d2630' };
  const icon = '<svg aria-hidden="true"><use href="#bus-icon"/></svg>';
  const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const frame = () => REPLAY.frames[state.frame];
  const delayText = seconds => `${seconds > 0 ? '+' : seconds < 0 ? '−' : ''}${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Math.abs(seconds) / 60)} мин`;
  const inScope = v => state.scope === 'all' || state.scope === 'east' && v.position[1] >= 37.7 || state.scope === 'south' && v.position[0] < 55.7;
  const scopedVehicles = () => frame().vehicles.filter(inScope);
  const signal = v => {
    if (state.scenario === 'risk-one' && v.id === '131672') return { kind:'risk', predicted:420, demo:true };
    if (state.scenario === 'risk-many') {
      if (['122613','129964'].includes(v.id)) return { kind:'late', current:420, demo:true };
      if (['131672','135081'].includes(v.id)) return { kind:'risk', predicted:420, demo:true };
    }
    if (state.scenario === 'breakdown' && v.id === '131672') return { kind:'blocked', demo:true };
    if (v.age > 120 || !v.forecast) return { kind:'none' };
    // The supplied sample forecast repeats the known current deviation.
    return v.forecast.delay >= state.threshold ? { kind:'late', current:v.forecast.delay } : { kind:'none' };
  };
  const status = v => {
    const s = signal(v);
    if (s.kind === 'late') return { label:'Уже опаздывает', tone:'red' };
    if (s.kind === 'risk') return { label:'Высокий прогноз задержки', tone:'amber' };
    if (s.kind === 'blocked') return { label:'Поломка · демо', tone:'red' };
    if (v.age > 120) return { label:'Координаты устарели', tone:'gray' };
    if (!v.forecast) return { label:'Нет оценки', tone:'gray' };
    return { label:'Ниже порога', tone:'green' };
  };
  const ageText = seconds => seconds < 60 ? `${seconds} с назад` : `${Math.floor(seconds / 60)} мин назад`;
  const workspace = document.querySelector('.workspace');
  const mapStage = document.querySelector('.map-stage');
  let cardsWereHidden = false;

  function resizeMap() {
    requestAnimationFrame(() => map?.invalidateSize({pan:false}));
  }

  function setSidebarOpen(open) {
    workspace.classList.toggle('sidebar-collapsed', !open);
    $('transport-sidebar').hidden = !open;
    $('sidebar-toggle').setAttribute('aria-expanded', String(open));
    $('sidebar-open').setAttribute('aria-expanded', String(open));
    $('sidebar-open').hidden = open;
    resizeMap();
  }

  function setCleanMap(clean) {
    mapStage.classList.toggle('clean-map', clean);
    $('map-cards-toggle').setAttribute('aria-pressed', String(clean));
    $('map-cards-toggle').textContent = clean ? 'Показать карточки' : 'Скрыть карточки';
  }

  function setMapExpanded(expanded) {
    if (expanded) {
      cardsWereHidden = mapStage.classList.contains('clean-map');
      setCleanMap(true);
    } else {
      setCleanMap(cardsWereHidden);
    }
    workspace.classList.toggle('map-expanded', expanded);
    document.body.classList.toggle('map-focus', expanded);
    $('map-expand-toggle').setAttribute('aria-pressed', String(expanded));
    $('map-expand-toggle').textContent = expanded ? 'Свернуть карту' : 'Развернуть карту';
    resizeMap();
  }

  function mapMessage(text) {
    $('map-message').textContent = text;
    $('map-message').hidden = !text;
  }

  function setMapStyle(style) {
    state.mapStyle = style;
    $('map').className = `map-theme-${style}`;
    document.querySelectorAll('[data-map-style]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mapStyle === style)));
    if (!map) return;
    if (!L.maplibreGL) {
      const fallbackFilters = { positron:'grayscale(.78) brightness(1.08)', bright:'none' };
      tiles.getContainer().style.filter = fallbackFilters[style];
      return;
    }
    if (!map.hasLayer(tiles)) tiles.addTo(map);
    if (styleLayer) map.removeLayer(styleLayer);
    styleLayer = L.maplibreGL({ style: `https://tiles.openfreemap.org/styles/${style}` }).addTo(map);
    const currentLayer = styleLayer;
    const glMap = styleLayer.getMaplibreMap?.();
    glMap?.on('load', () => { if (currentLayer === styleLayer && map.hasLayer(tiles)) map.removeLayer(tiles); mapMessage(''); });
    glMap?.on('error', () => { if (currentLayer === styleLayer) mapMessage('Векторная карта загружается с ошибкой. Доступна резервная карта OpenStreetMap.'); });
  }

  function setupMap() {
    if (!window.L) {
      mapMessage('Карта не загрузилась. Проверьте подключение к интернету и обновите страницу. Данные записи доступны в списке.');
      $('overview-button').disabled = true;
      return;
    }
    map = L.map('map', { zoomControl: false, scrollWheelZoom: true }).setView([55.7512, 37.6184], 11);
    L.control.zoom({ position: 'topright' }).addTo(map);
    tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors' }).addTo(map);
    let tileErrors = false;
    tiles.on('loading', () => { tileErrors = false; });
    tiles.on('tileerror', () => {
      tileErrors = true;
      mapMessage('Часть карты не загрузилась. Проверьте интернет. Список автобусов остаётся доступен.');
    });
    tiles.on('load', () => { if (!tileErrors) mapMessage(''); });
    vehicleLayer = L.layerGroup().addTo(map);
    trackLayer = L.layerGroup().addTo(map);
    setMapStyle(state.mapStyle);
  }

  function routeSegments(v) {
    if (!v?.stops?.length) return [];
    const points = v.age > 120 ? [...v.stops] : [{ position: v.position, address: 'Положение автобуса', time: frame().time }, ...v.stops];
    const segments = points.slice(1).map((stop, index) => ({ index, from: points[index], to: stop }));
    if (!segments.length) return segments;
    const targetIndex = v.stops.findIndex(stop => stop.position[0] === v.forecast?.target?.[0] && stop.position[1] === v.forecast?.target?.[1]);
    const incidentIndex = Math.min(segments.length - 1, Math.max(0, targetIndex - (v.age > 120 ? 1 : 0)));
    let incident = null;
    if (state.scenario === 'observed' && signal(v).kind === 'late') {
      incident = { tone:'critical', label:'Автобус уже опаздывает', note:`${delayText(v.forecast.delay)} текущего отклонения · причина не определена` };
    } else if (state.scenario === 'risk-one') {
      incident = { tone:'warning', label:'1 ТС под риском', note:'Демонстрационный сигнал; вероятность модель пока не отдаёт' };
    } else if (state.scenario === 'risk-many') {
      incident = { tone:'critical', label:'Несколько ТС под риском', note:'Пример группового сбоя, возможная пробка; причина не подтверждена' };
    } else if (state.scenario === 'breakdown') {
      incident = { tone:'blocked', label:'Пример: поломка', note:'Демонстрационный сигнал, не из данных проекта' };
    }
    return segments.map(segment => ({...segment, issue:segment.index === incidentIndex ? incident : null, color:segment.index === incidentIndex && incident ? issueColors[incident.tone] : issueColors.normal }));
  }

  function renderSegments() {
    const v = frame().vehicles.find(item => item.id === state.selected);
    const latest = v?.track?.at(-1);
    const lastPair = latest?.length >= 2 ? latest.slice(-2) : null;
    const currentColor = signal(v).kind === 'late' ? '#ca2437' : signal(v).kind === 'risk' ? '#e5a32b' : '#4e1930';
    $('current-gps-segment').style.setProperty('--current-color', currentColor);
    $('current-gps-segment').innerHTML = lastPair && v.age <= 120
      ? `<span class="gps-now-badge">СЕЙЧАС / GPS</span><strong>Текущий отрезок движения автобуса ${escapeHTML(v.id)}</strong><span>Последние две записанные точки · ${escapeHTML(v.lastSeen)} · ${escapeHTML(v.speed)} км/ч</span><small>Название улицы и границы дорожного участка в данных не указаны. Отрезок выделен на карте.</small>`
      : `<span class="gps-now-badge">СЕЙЧАС / GPS</span><strong>Текущий дорожный отрезок не определён</strong><small>${v?.age > 120 ? 'Координаты устарели.' : 'Недостаточно последовательных GPS-точек.'}</small>`;
    const segments = routeSegments(v);
    if (!segments.length) {
      $('segment-list').innerHTML = '<p class="segment-empty">Для этого автобуса нет ближайших точек расписания. Можно посмотреть записанный GPS‑путь на карте.</p>';
      return;
    }
    $('segment-list').innerHTML = segments.map(s => `<button type="button" class="segment-card ${s.issue ? `segment-${s.issue.tone}` : ''}" data-segment-index="${s.index}" aria-pressed="${s.index === state.segmentIndex}" style="--segment-color:${s.color}"><span class="segment-number">${String(s.index + 1).padStart(2, '0')}</span><span class="segment-line"><i></i><i></i></span><strong>${escapeHTML(s.from.address)} → ${escapeHTML(s.to.address)}</strong><small>${escapeHTML(s.from.time)} — ${escapeHTML(s.to.time)} · ${s.issue ? escapeHTML(s.issue.label) : 'без сигнала'}</small></button>`).join('');
  }

  function renderIncident() {
    const v = frame().vehicles.find(item => item.id === state.selected);
    const segment = routeSegments(v).find(item => item.issue);
    const banner = $('incident-banner');
    if (!segment) {
      banner.className = 'incident-banner incident-none';
      banner.disabled = true;
      banner.innerHTML = '<strong>Нет выделенного проблемного участка</strong><span>Выберите автобус с прогнозом или демонстрационный сценарий.</span>';
      return;
    }
    banner.className = `incident-banner incident-${segment.issue.tone}`;
    banner.disabled = false;
    banner.innerHTML = `<span class="incident-symbol">${segment.issue.tone === 'blocked' ? '×' : '!'}</span><span><strong>${escapeHTML(segment.issue.label)}</strong><small>${escapeHTML(segment.from.address)} → ${escapeHTML(segment.to.address)}</small><em>${escapeHTML(segment.issue.note)}</em></span><b aria-hidden="true">↗</b>`;
  }

  function renderVerdict() {
    const assessed = scopedVehicles().filter(v => v.forecast || signal(v).kind !== 'none');
    const late = assessed.filter(v => signal(v).kind === 'late').length;
    const risk = assessed.filter(v => signal(v).kind === 'risk').length;
    const blocked = assessed.filter(v => signal(v).kind === 'blocked').length;
    const demo = state.scenario !== 'observed';
    const item = { tone:blocked || late + risk >= 2 ? 'critical' : late || risk ? 'warning' : 'normal', title:blocked ? 'ТС остановилось на линии' : late + risk >= 2 ? 'Высокий риск сбоя движения' : risk ? 'Внимание: ТС под риском' : late ? 'Внимание: автобус опаздывает' : 'Нет сигналов выше порога', late, risk:demo ? risk : null, blocked, total:assessed.length, source:demo ? 'ДЕМО · ГРУППИРОВКА ПО МАРШРУТУ УСЛОВНАЯ' : 'ДАННЫЕ ЗАПИСИ · МАРШРУТЫ НЕ УКАЗАНЫ', detail:demo ? 'Жёлтый — прогноз задержки, красный — уже опаздывает. Причина сбоя не подтверждена.' : `Порог: ${state.threshold / 60} мин. В выборке доступно текущее отклонение; отдельного прогноза риска нет.` };
    const total = Math.max(item.total, 1);
    const blocks = Array.from({length:total}, (_,index) => `<i class="${index < item.late ? 'late' : index < item.late + (item.risk || 0) ? 'risk' : index < item.late + (item.risk || 0) + (item.blocked || 0) ? 'blocked' : ''}"></i>`).join('');
    $('verdict-panel').className = `verdict-panel verdict-${item.tone}`;
    $('verdict-panel').innerHTML = `<div class="verdict-main"><span class="verdict-overline">ВЕРДИКТ / ${escapeHTML(item.source)}</span><strong>${escapeHTML(item.title)}</strong><p>${escapeHTML(item.detail)}</p></div><div class="verdict-stats"><div><b>${item.late}</b><span>Уже<br>опаздывают</span></div><div><b>${item.risk === null ? '—' : item.risk}</b><span>С высоким<br>риском</span></div>${item.blocked ? `<div><b>${item.blocked}</b><span>Поломка</span></div>` : ''}<div class="verdict-scale"><span>${item.total ? `Сигналы: ${item.late + (item.risk || 0) + (item.blocked || 0)} из ${item.total} ТС` : 'Нет оценок'}</span><div>${blocks}</div></div></div>`;
  }

  function visibleVehicles() {
    const q = state.query.trim().toLocaleLowerCase('ru');
    return scopedVehicles().filter(v => (state.filter === 'all' || v.forecast || signal(v).kind !== 'none') && (!q || v.id.includes(q) || (v.forecast?.address || '').toLocaleLowerCase('ru').includes(q) || v.stops.some(s => s.address.toLocaleLowerCase('ru').includes(q))))
      .sort((a, b) => ({blocked:4,late:3,risk:2,none:1}[signal(b).kind] - {blocked:4,late:3,risk:2,none:1}[signal(a).kind]) || (b.forecast?.delay ?? -Infinity) - (a.forecast?.delay ?? -Infinity) || a.id.localeCompare(b.id));
  }

  function renderList() {
    const vehicles = visibleVehicles();
    $('forecast-count').textContent = scopedVehicles().filter(v => v.forecast || signal(v).kind !== 'none').length;
    $('all-count').textContent = scopedVehicles().length;
    $('stat-vehicles').textContent = scopedVehicles().length;
    $('stat-forecasts').textContent = scopedVehicles().filter(v => v.forecast || signal(v).kind !== 'none').length;
    $('count-label').textContent = `${scopedVehicles().length} автобусов в зоне · ${frame().time}`;
    document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === state.filter)));
    if (!vehicles.length) {
      $('vehicle-list').innerHTML = '<p class="empty-list">Ничего не найдено. Попробуйте другой ID или адрес.</p>';
      return;
    }
    $('vehicle-list').innerHTML = vehicles.map(v => {
      const s = status(v), f = v.forecast;
      const address = f?.address || v.stops[0]?.address || (v.hasSchedule ? 'Нет ближайших точек расписания' : 'Расписание отсутствует');
      const sig = signal(v);
      return `<button type="button" class="vehicle-card signal-${sig.kind}" data-vehicle="${escapeHTML(v.id)}" aria-pressed="${v.id === state.selected}" aria-controls="vehicle-detail"><div class="vehicle-top"><span class="bus-mini">${icon}</span><div><div class="vehicle-name">Автобус ${escapeHTML(v.id)}</div><div class="vehicle-caption">${sig.demo ? 'Демонстрационный сигнал' : f ? 'Учебная оценка' : 'Данные телеметрии'}</div></div></div><p class="vehicle-address">${escapeHTML(address)}</p><span class="status-chip ${s.tone}">${s.label}</span><div class="card-bottom" style="margin-top:14px"><span>${f ? `К ${f.plan} · через ${f.horizon} мин` : `Координаты: ${v.lastSeen}`}</span><strong>${sig.predicted ? delayText(sig.predicted) : sig.current ? delayText(sig.current) : f ? delayText(f.delay) : '—'}</strong></div></button>`;
    }).join('');
  }

  function renderDetails() {
    const v = frame().vehicles.find(v => v.id === state.selected);
    if (!v) { $('vehicle-detail').innerHTML = '<p>Выберите автобус на карте или в списке.</p>'; return; }
    const s = status(v), f = v.forecast, sig = signal(v);
    let body = `<div class="detail-top"><strong>Автобус ${escapeHTML(v.id)}</strong><span class="status-chip ${s.tone}">${s.label}</span></div>`;
    if (f) {
      body += `<p class="detail-address">${escapeHTML(f.address)}</p><p class="detail-sub">Целевая остановка через ${f.horizon} мин · ${sig.demo ? 'демонстрационный сценарий' : 'учебная оценка повторяет текущее отклонение'}</p><div class="arrival-grid"><div><span>По расписанию</span><b>${f.plan}</b></div><div><span>Текущее отклонение</span><b>${delayText(f.delay)}</b></div><div><span>${sig.predicted ? 'Прогноз · демо' : 'Порог'}</span><b>${sig.predicted ? delayText(sig.predicted) : `${state.threshold / 60} мин`}</b></div></div>`;
    } else {
      body += `<p class="detail-empty">На выбранный момент прогноза нет. ${v.stops.length ? 'Доступны положение автобуса и ближайшие точки расписания.' : v.hasSchedule ? 'Ближайших точек расписания на следующие 25 минут нет.' : 'Для этого автобуса в датасете нет расписания.'}</p>`;
    }
    if (v.age > 120) body += `<p class="detail-warning">Последние достоверные координаты — ${v.lastSeen}. Положение автобуса на выбранный момент неизвестно.</p>`;
    body += `<div class="detail-foot"><span>Координаты: ${ageText(v.age)}</span><span>Скорость в ${v.lastSeen}: ${v.speed} км/ч</span></div>`;
    $('vehicle-detail').innerHTML = body;
  }

  function drawMap(focus = false) {
    if (!map) return;
    vehicleLayer.clearLayers(); trackLayer.clearLayers();
    scopedVehicles().forEach(v => {
      const selected = v.id === state.selected;
      const sig = signal(v);
      const html = `<div class="bus-marker signal-${sig.kind} ${selected ? 'selected' : ''} ${v.age > 120 && sig.kind === 'none' ? 'old' : ''}">${icon}</div>`;
      const marker = L.marker(v.position, { icon: L.divIcon({html,className:'bus-pin',iconSize:selected?[46,46]:[40,40],iconAnchor:selected?[23,23]:[20,20]}),title:`Автобус ${v.id}${v.age > 120 ? ', координаты устарели' : ''}`,alt:`Автобус ${v.id}`,zIndexOffset:selected?1000:0 }).addTo(vehicleLayer);
      marker.on('click', () => choose(v.id));
    });
    const v = frame().vehicles.find(v => v.id === state.selected);
    if (!v) return;
    if (state.gps) v.track.forEach(segment => {
      L.polyline(segment, {color:'#fff',weight:11,opacity:.98,interactive:false}).addTo(trackLayer);
      L.polyline(segment, {color:'#ac1832',weight:6,opacity:.98,interactive:false}).addTo(trackLayer);
    });
    const latest = v.track?.at(-1);
    if (state.gps && v.age <= 120 && latest?.length >= 2) {
      const pair = latest.slice(-2);
      L.polyline(pair,{color:'#fff',weight:17,opacity:1,interactive:false}).addTo(trackLayer);
      const currentColor = signal(v).kind === 'late' ? '#ca2437' : signal(v).kind === 'risk' ? '#e5a32b' : '#4e1930';
      L.polyline(pair,{color:currentColor,weight:11,opacity:1,interactive:false}).bindTooltip('Текущий GPS-отрезок').addTo(trackLayer);
    }
    if (state.stops) {
      routeSegments(v).forEach(s => {
        const active = s.index === state.segmentIndex;
        L.polyline([s.from.position, s.to.position], {color:'#fff',weight:s.issue || active?13:8,opacity:.96,interactive:false}).addTo(trackLayer);
        L.polyline([s.from.position, s.to.position], {color:s.color,weight:s.issue || active?8:4,opacity:active?1:.9,dashArray:s.issue?'10 5':'5 9',interactive:false}).addTo(trackLayer);
        if (s.issue) {
          const midpoint = [(s.from.position[0] + s.to.position[0]) / 2, (s.from.position[1] + s.to.position[1]) / 2];
          const sign = `<span class="issue-marker ${s.issue.tone}">${s.issue.tone === 'blocked' ? '×' : state.scenario === 'risk-many' ? '4' : '!'}</span>`;
          L.marker(midpoint,{icon:L.divIcon({html:sign,className:'issue-pin',iconSize:[34,34],iconAnchor:[17,17]}),title:s.issue.label,zIndexOffset:1500}).bindTooltip(s.issue.label).addTo(trackLayer);
        }
      });
      v.stops.forEach((s, index) => {
        const html = `<span class="stop-marker">${index + 1}</span>`;
        L.marker(s.position, {icon:L.divIcon({html,className:'stop-pin',iconSize:[26,26],iconAnchor:[13,13]}),title:`Точка расписания ${index + 1}: ${s.address}, ${s.time}`}).bindTooltip(`${escapeHTML(s.address)} · ${escapeHTML(s.time)}`).addTo(trackLayer);
      });
    }
    if (v.forecast?.target) {
      const tooltip = document.createElement('div');
      tooltip.textContent = `${v.forecast.address} · ${signal(v).predicted ? `демо-прогноз ${delayText(signal(v).predicted)}` : `текущее отклонение ${delayText(v.forecast.delay)}`} к ${v.forecast.plan}`;
      L.circleMarker(v.forecast.target,{radius:10,color:'#dc3447',weight:3,fillColor:'#fff',fillOpacity:1}).bindTooltip(tooltip,{permanent:true,direction:'bottom',offset:[0,12]}).addTo(trackLayer);
    }
    if (focus) {
      const issue = routeSegments(v).find(segment => segment.issue);
      const coords = issue ? [issue.from.position, issue.to.position] : [v.position, ...v.stops.map(s => s.position)];
      map.fitBounds(L.latLngBounds(coords), {paddingTopLeft:[45,175],paddingBottomRight:[55,215],maxZoom:15,animate:false});
    }
  }

  function render(focus = false) {
    $('current-time').textContent = frame().time;
    $('time-slider').value = state.frame;
    $('time-slider').setAttribute('aria-valuetext',`${frame().time}, 6 января 2026 года, время записи`);
    renderList(); renderDetails(); renderSegments(); renderIncident(); renderVerdict(); drawMap(focus);
  }

  function choose(id) {
    state.selected = id;
    state.segmentIndex = null;
    render(true);
    $('announcement').textContent = `Выбран автобус ${id}. Подробности прогноза обновлены.`;
  }

  function stopPlayback() {
    clearInterval(timer); timer = null; state.playing = false;
    $('play-symbol').textContent = '▶'; $('play-label').textContent = 'Смотреть запись';
    $('play-button').setAttribute('aria-label','Воспроизвести запись');
  }

  async function pollServer() {
    const badge = $('server-status');
    try {
      const [health, prediction] = await Promise.all([
        fetch('/health', {cache:'no-store'}).then(response => response.json()),
        fetch('/predictions/latest', {cache:'no-store'}).then(response => response.json())
      ]);
      if (health.status !== 'ok') throw new Error('API unavailable');
      const stateText = prediction.status === 'ready' ? 'worker считает' : prediction.status === 'stale' ? 'прогноз устарел' : 'прогноза пока нет';
      badge.className = `server-status server-${prediction.status}`;
      badge.textContent = `FastAPI подключён · ${stateText} · серверный прогноз относится к учебному генератору, не к автобусам на карте`;
    } catch {
      badge.className = 'server-status server-error';
      badge.textContent = 'Нет связи с FastAPI · историческая карта может оставаться доступной';
    }
  }
  pollServer();
  setInterval(pollServer, 3000);

  if (!REPLAY?.frames?.length) {
    $('count-label').textContent = 'Запись не загрузилась';
    mapMessage('Не удалось получить запись через FastAPI. Проверьте сервер и обновите страницу.');
    $('play-button').disabled = true; $('time-slider').disabled = true;
    return;
  }
  setupMap(); render(true);
  $('vehicle-list').addEventListener('click', event => {
    const button = event.target.closest('[data-vehicle]');
    if (!button) return;
    choose(button.dataset.vehicle);
    $('vehicle-list').querySelector(`[data-vehicle="${state.selected}"]`)?.focus({preventScroll:true});
  });
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {state.filter=button.dataset.filter;renderList();}));
  document.querySelectorAll('[data-scenario]').forEach(button => button.addEventListener('click', () => {
    state.scenario = button.dataset.scenario;
    state.segmentIndex = null;
    document.querySelectorAll('[data-scenario]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    render();
  }));
  $('scope-select').addEventListener('change', event => {
    state.scope = event.target.value;
    if (!scopedVehicles().some(v => v.id === state.selected)) state.selected = scopedVehicles().find(v => v.forecast)?.id || scopedVehicles()[0]?.id;
    state.segmentIndex = null;
    render();
    if (map && scopedVehicles().length) map.fitBounds(L.latLngBounds(scopedVehicles().map(v => v.position)),{padding:[55,55],maxZoom:12,animate:false});
  });
  $('threshold-select').addEventListener('change', event => { state.threshold = Number(event.target.value) * 60; render(); });
  document.querySelectorAll('[data-map-style]').forEach(button => button.addEventListener('click', () => setMapStyle(button.dataset.mapStyle)));
  document.querySelectorAll('[data-overlay]').forEach(button => button.addEventListener('click', () => {
    state[button.dataset.overlay] = !state[button.dataset.overlay];
    button.setAttribute('aria-pressed', String(state[button.dataset.overlay]));
    drawMap();
  }));
  $('segment-list').addEventListener('click', event => {
    const button = event.target.closest('[data-segment-index]');
    if (!button) return;
    state.segmentIndex = Number(button.dataset.segmentIndex);
    state.stops = true;
    document.querySelector('[data-overlay="stops"]').setAttribute('aria-pressed', 'true');
    renderSegments(); drawMap();
    const v = frame().vehicles.find(item => item.id === state.selected);
    const segment = routeSegments(v)[state.segmentIndex];
    if (map && segment) map.fitBounds(L.latLngBounds([segment.from.position, segment.to.position]), {padding:[90,90],maxZoom:15,animate:false});
    document.querySelector('.map-stage').scrollIntoView({behavior:'smooth',block:'center'});
  });
  $('incident-banner').addEventListener('click', () => {
    const v = frame().vehicles.find(item => item.id === state.selected);
    const segment = routeSegments(v).find(item => item.issue);
    if (!segment) return;
    state.segmentIndex = segment.index;
    renderSegments(); drawMap();
    map?.fitBounds(L.latLngBounds([segment.from.position, segment.to.position]), {padding:[90,90],maxZoom:15,animate:false});
  });
  $('search').addEventListener('input', event => { state.query = event.target.value; renderList(); });
  $('overview-button').addEventListener('click', () => {
    if (map && scopedVehicles().length) map.fitBounds(L.latLngBounds(scopedVehicles().map(v => v.position)),{padding:[55,55],maxZoom:12,animate:false});
  });
  $('time-slider').addEventListener('input', event => {
    stopPlayback(); state.frame = Number(event.target.value); state.segmentIndex = null; render();
  });
  $('play-button').addEventListener('click', () => {
    if (state.playing) { stopPlayback(); return; }
    if (state.frame === REPLAY.frames.length-1) state.frame = 0;
    state.playing = true;
    $('play-symbol').textContent = 'Ⅱ'; $('play-label').textContent = 'Пауза';
    $('play-button').setAttribute('aria-label','Приостановить запись');
    render();
    timer = setInterval(() => {
      state.frame += 1; render();
      if (state.frame >= REPLAY.frames.length-1) stopPlayback();
    }, 3000);
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopPlayback(); });
  $('about-button').addEventListener('click', () => $('about-dialog').showModal());
  $('sidebar-toggle').addEventListener('click', () => setSidebarOpen(false));
  $('sidebar-open').addEventListener('click', () => setSidebarOpen(true));
  $('map-cards-toggle').addEventListener('click', () => setCleanMap(!mapStage.classList.contains('clean-map')));
  $('map-expand-toggle').addEventListener('click', () => setMapExpanded(!workspace.classList.contains('map-expanded')));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && workspace.classList.contains('map-expanded')) setMapExpanded(false);
  });
  $('close-about').addEventListener('click', () => $('about-dialog').close());
  $('understood').addEventListener('click', () => $('about-dialog').close());
})();
