/* Route segment conditions are calculated by the analytics worker and read via FastAPI. */
(() => {
  'use strict';
  const routeSelect = document.getElementById('analytics-route');
  const status = document.getElementById('analytics-status');
  const list = document.getElementById('analytics-list');
  const alerts = document.getElementById('analytics-alerts');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const minutes = value => value == null ? '—' : `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1}).format(value / 60)} мин`;
  const labels = {NORMAL:'Без сигнала', WATCH:'Нужно проверить', WARNING:'Замедление подтверждено', CRITICAL:'Сильное замедление'};
  let rows = [], activeAlerts = [], timer;

  function render() {
    const selected = routeSelect.value;
    const visible = rows.filter(row => selected ? row.route_id === selected : row.severity !== 'NORMAL');
    const ordered = visible.sort((a, b) => ({CRITICAL:3,WARNING:2,WATCH:1,NORMAL:0}[b.severity] - {CRITICAL:3,WARNING:2,WATCH:1,NORMAL:0}[a.severity]) || b.risk_index - a.risk_index);
    window.DispatcherSegmentMap?.update(rows, selected);
    const alerted = activeAlerts.filter(a => !selected || a.route_id === selected).filter(a => a.severity === 'WARNING' || a.severity === 'CRITICAL');
    alerts.innerHTML = alerted.length ? `<strong>! Предупреждения по участкам: ${alerted.length}</strong><span>Откройте карточку ниже, чтобы увидеть время и число автобусов.</span>` : '';
    if (!ordered.length) {
      list.innerHTML = `<p class="analytics-empty">${rows.length ? 'На выбранных маршрутах нет сигнала. Выберите конкретный маршрут, чтобы увидеть все его участки.' : 'Пока нет рассчитанных участков. Нужно вручную задать маршрут и назначения автобусов, затем запустить процесс аналитики.'}</p>`;
      return;
    }
    list.innerHTML = ordered.slice(0, 30).map(row => `<button type="button" class="analytics-card tone-${escape(row.severity.toLowerCase())}" data-segment="${escape(row.segment_id)}"><span class="analytics-card-top"><b>${escape(row.route_name)} · ${escape(row.direction_id)}${row.is_demo ? ' · ДЕМО' : ''}</b><em>${escape(labels[row.severity] || row.severity)}</em></span><strong>${escape(row.from_name)} → ${escape(row.to_name)}</strong><span class="analytics-numbers"><span>Обычно <b>${minutes(row.baseline_travel_time_sec)}</b></span><span>Сейчас ≈ <b>${minutes(row.median_current_travel_time_sec)}</b></span><span>Замедление <b>${row.slowdown_ratio == null ? '—' : `${Math.round((row.slowdown_ratio - 1) * 100)}%`}</b></span></span><small>Подтвердили: ${row.vehicles_confirming_slowdown} из ${row.vehicles_considered} · уверенность: ${row.confidence_level === 'HIGH' ? 'высокая' : row.confidence_level === 'MEDIUM' ? 'средняя' : 'низкая'} · ${row.baseline_source === 'planned' ? 'норма по плану' : row.baseline_source === 'observed' ? 'норма по истории' : 'норма неизвестна'}</small></button>`).join('');
    if (ordered.length > 30) list.insertAdjacentHTML('beforeend', `<p class="analytics-more">Показаны первые 30 из ${ordered.length} участков. Выберите маршрут для уточнения.</p>`);
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const [segmentsResponse, alertsResponse] = await Promise.all([
        fetch('/analytics/segments', {cache:'no-store',signal:AbortSignal.timeout(10000)}),
        fetch('/analytics/alerts', {cache:'no-store',signal:AbortSignal.timeout(10000)})
      ]);
      if (!segmentsResponse.ok || !alertsResponse.ok) throw new Error('API unavailable');
      rows = (await segmentsResponse.json()).segments || [];
      activeAlerts = (await alertsResponse.json()).alerts || [];
      const previous = routeSelect.value;
      const names = [...new Map(rows.map(row => [row.route_id, row.route_name])).entries()].sort((a,b) => a[1].localeCompare(b[1], 'ru'));
      routeSelect.innerHTML = '<option value="">Все маршруты с сигналом</option>' + names.map(([id,name]) => `<option value="${escape(id)}">${escape(name)}</option>`).join('');
      routeSelect.value = names.some(([id]) => id === previous) ? previous : '';
      const latest = rows.reduce((max, row) => row.calculated_at > max ? row.calculated_at : max, '');
      status.textContent = rows.length ? `${rows.length} участков · расчёт ${new Date(latest).toLocaleString('ru-RU', {timeZone:'Europe/Moscow'})} МСК` : 'Маршруты пока не рассчитаны';
      render();
    } catch {
      status.textContent = 'Аналитика участков пока недоступна';
      rows = []; activeAlerts = []; render();
    } finally {
      timer = setTimeout(refresh, 15000);
    }
  }

  routeSelect.addEventListener('change', render);
  list.addEventListener('click', event => {
    const button = event.target.closest('[data-segment]');
    const row = rows.find(item => item.segment_id === button?.dataset.segment);
    if (row) window.DispatcherSegmentMap?.focus(row);
  });
  refresh();
})();
