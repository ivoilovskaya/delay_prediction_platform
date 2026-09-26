/* Static replay adapter. FastAPI integration can replace REPLAY without changing the view. */
(() => {
  'use strict';
  const REPLAY = window.DISPATCHER_REPLAY;
  const $ = id => document.getElementById(id);
  const state = { frame: 0, selected: '131672', filter: 'forecasts', query: '', playing: false };
  let timer = null, map = null, vehicleLayer = null, trackLayer = null, tiles = null;
  const icon = '<svg aria-hidden="true"><use href="#bus-icon"/></svg>';
  const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const frame = () => REPLAY.frames[state.frame];
  const delayText = seconds => `${seconds > 0 ? '+' : seconds < 0 ? '−' : ''}${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Math.abs(seconds) / 60)} мин`;
  const status = v => {
    if (v.age > 120) return { label: 'Координаты устарели', tone: 'gray' };
    if (!v.forecast) return { label: 'Нет прогноза', tone: 'gray' };
    if (v.forecast.delay >= 300) return { label: 'Ожидается опоздание', tone: 'red' };
    if (v.forecast.delay > 120) return { label: 'Ожидается опоздание', tone: 'amber' };
    if (v.forecast.delay < -60) return { label: 'Ожидается опережение', tone: 'blue' };
    return { label: 'Небольшое отклонение', tone: 'green' };
  };
  const ageText = seconds => seconds < 60 ? `${seconds} с назад` : `${Math.floor(seconds / 60)} мин назад`;

  function mapMessage(text) {
    $('map-message').textContent = text;
    $('map-message').hidden = !text;
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
  }

  function visibleVehicles() {
    const q = state.query.trim().toLocaleLowerCase('ru');
    return frame().vehicles.filter(v => (state.filter === 'all' || v.forecast) && (!q || v.id.includes(q) || (v.forecast?.address || '').toLocaleLowerCase('ru').includes(q) || v.stops.some(s => s.address.toLocaleLowerCase('ru').includes(q))))
      .sort((a, b) => (b.forecast?.delay ?? -Infinity) - (a.forecast?.delay ?? -Infinity) || a.id.localeCompare(b.id));
  }

  function renderList() {
    const vehicles = visibleVehicles();
    $('forecast-count').textContent = frame().vehicles.filter(v => v.forecast).length;
    $('all-count').textContent = frame().vehicles.length;
    $('count-label').textContent = `${frame().vehicles.length} автобусов в записи · ${frame().time}`;
    document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === state.filter)));
    if (!vehicles.length) {
      $('vehicle-list').innerHTML = '<p class="empty-list">Ничего не найдено. Попробуйте другой ID или адрес.</p>';
      return;
    }
    $('vehicle-list').innerHTML = vehicles.map(v => {
      const s = status(v), f = v.forecast;
      const address = f?.address || v.stops[0]?.address || (v.hasSchedule ? 'Нет ближайших точек расписания' : 'Расписание отсутствует');
      return `<button type="button" class="vehicle-card" data-vehicle="${escapeHTML(v.id)}" aria-pressed="${v.id === state.selected}" aria-controls="vehicle-detail"><div class="vehicle-top"><span class="bus-mini">${icon}</span><div><div class="vehicle-name">Автобус ${escapeHTML(v.id)}</div><div class="vehicle-caption">${f ? 'Учебный прогноз' : 'Данные телеметрии'}</div></div></div><p class="vehicle-address">${escapeHTML(address)}</p><span class="status-chip ${s.tone}">${s.label}</span><div class="card-bottom" style="margin-top:14px"><span>${f ? `К ${f.plan} · через ${f.horizon} мин` : `Координаты: ${v.lastSeen}`}</span><strong>${f ? delayText(f.delay) : '—'}</strong></div></button>`;
    }).join('');
  }

  function renderDetails() {
    const v = frame().vehicles.find(v => v.id === state.selected);
    if (!v) { $('vehicle-detail').innerHTML = '<p>Выберите автобус на карте или в списке.</p>'; return; }
    const s = status(v), f = v.forecast;
    let body = `<div class="detail-top"><strong>Автобус ${escapeHTML(v.id)}</strong><span class="status-chip ${s.tone}">${s.label}</span></div>`;
    if (f) {
      body += `<p class="detail-address">${escapeHTML(f.address)}</p><p class="detail-sub">Цель прогноза через ${f.horizon} мин · учебная оценка</p><div class="arrival-grid"><div><span>По расписанию</span><b>${f.plan}</b></div><div><span>Ожидается ≈</span><b>${f.arrival}</b></div><div><span>Отклонение</span><b>${delayText(f.delay)}</b></div></div>`;
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
    frame().vehicles.forEach(v => {
      const selected = v.id === state.selected;
      const html = `<div class="bus-marker ${selected ? 'selected' : ''} ${v.age > 120 ? 'old' : ''}">${icon}</div>`;
      const marker = L.marker(v.position, { icon: L.divIcon({html,className:'bus-pin',iconSize:selected?[46,46]:[40,40],iconAnchor:selected?[23,23]:[20,20]}),title:`Автобус ${v.id}${v.age > 120 ? ', координаты устарели' : ''}`,alt:`Автобус ${v.id}`,zIndexOffset:selected?1000:0 }).addTo(vehicleLayer);
      marker.on('click', () => choose(v.id));
    });
    const v = frame().vehicles.find(v => v.id === state.selected);
    if (!v) return;
    v.track.forEach(segment => {
      L.polyline(segment, {color:'#fff',weight:9,opacity:.95,interactive:false}).addTo(trackLayer);
      L.polyline(segment, {color:'#2466df',weight:5,opacity:.95,interactive:false}).addTo(trackLayer);
    });
    v.stops.forEach(s => {
      const tooltip = document.createElement('div');
      tooltip.textContent = `${s.address} · по расписанию ${s.time}`;
      L.circleMarker(s.position,{radius:5,color:'#2466df',weight:2,fillColor:'#fff',fillOpacity:1}).bindTooltip(tooltip).addTo(trackLayer);
    });
    if (v.forecast?.target) {
      const tooltip = document.createElement('div');
      tooltip.textContent = `${v.forecast.address} · ${delayText(v.forecast.delay)} к ${v.forecast.plan}`;
      L.circleMarker(v.forecast.target,{radius:10,color:'#dc3447',weight:3,fillColor:'#fff',fillOpacity:1}).bindTooltip(tooltip,{permanent:true,direction:'bottom',offset:[0,12]}).addTo(trackLayer);
    }
    if (focus) {
      const coords = [v.position, ...v.track.flat(), ...(v.forecast?.target ? [v.forecast.target] : [])];
      map.fitBounds(L.latLngBounds(coords), {paddingTopLeft:[50,175],paddingBottomRight:[65,300],maxZoom:15,animate:false});
    }
  }

  function render(focus = false) {
    $('current-time').textContent = frame().time;
    $('time-slider').value = state.frame;
    $('time-slider').setAttribute('aria-valuetext',`${frame().time}, 6 января 2026 года, время записи`);
    renderList(); renderDetails(); drawMap(focus);
  }

  function choose(id) {
    state.selected = id;
    render(true);
    $('announcement').textContent = `Выбран автобус ${id}. Подробности прогноза обновлены.`;
  }

  function stopPlayback() {
    clearInterval(timer); timer = null; state.playing = false;
    $('play-symbol').textContent = '▶'; $('play-label').textContent = 'Смотреть запись';
    $('play-button').setAttribute('aria-label','Воспроизвести запись');
  }

  if (!REPLAY?.frames?.length) {
    $('count-label').textContent = 'Запись не загрузилась';
    mapMessage('Не удалось прочитать данные записи. Обновите страницу.');
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
  $('search').addEventListener('input', event => { state.query = event.target.value; renderList(); });
  $('overview-button').addEventListener('click', () => {
    if (map) map.fitBounds(L.latLngBounds(frame().vehicles.map(v => v.position)),{padding:[55,55],maxZoom:12,animate:false});
  });
  $('time-slider').addEventListener('input', event => {
    stopPlayback(); state.frame = Number(event.target.value); render();
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
  $('close-about').addEventListener('click', () => $('about-dialog').close());
  $('understood').addEventListener('click', () => $('about-dialog').close());
})();
