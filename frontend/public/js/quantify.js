// Screen 4 — Quantify / Emissions. Reads GET /incidents/:id/sensor-series and /quantification;
// "Run estimate" calls POST /incidents/:id/quantify. No number is shown without a backend result.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const { API, INCIDENT_ID, get } = window.AtelicShell;

  const BADGE = {
    simulated: 'Simulated', synthetic: 'Simulated', model_estimated: 'Model estimated',
    experimental_setpoint: 'Experimental setpoint', provider_reported: 'Provider reported',
    hypothesis: 'Hypothesis', pending: 'Pending',
  };
  // Simulated / synthetic provenance is tracked in the data but not shown.
  const badge = (p, title) => p === 'simulated' || p === 'synthetic' ? '' : `<span class="badge ${p}"${title ? ` title="${esc(title)}"` : ''}>${BADGE[p] || 'Pending'}</span>`;
  // Stacked strips on one time axis; methane gets the most height.
  const STRIPS = [
    { q: 'methane_ppm', name: 'Methane concentration', unit: 'ppm', color: '--n-well', digits: 2, h: 160 },
    { q: 'wind_speed_m_s', name: 'Wind speed', unit: 'm/s', color: '--n-equip', digits: 2, h: 104 },
    { q: 'wind_dir_deg_from_north', name: 'Wind direction', unit: '° from north', color: '--n-wt', digits: 1, h: 104 },
    { q: 'pressure_bara', name: 'Process pressure', unit: 'bar absolute', color: '--n-station', digits: 3, h: 104 },
  ];
  const AXIS_H = 28;
  const MONO = 'IBM Plex Mono, monospace';
  const METHOD_LABEL = {};
  const hhmm = (t) => (t ? t.slice(11, 16) : '—');
  const hhmmss = (t) => (t ? t.slice(11, 19) : '—');

  let S = null, Q = null, method = null, traces = null, qcChart = null;

  // ----- result card -----
  function renderHero() {
    const r = Q && Q.result, title = $('res-title'), body = $('hero-body'), fields = $('fields');
    $('res-badges').innerHTML = '';
    fields.hidden = true;
    if (!Q) {
      body.innerHTML = '<div class="hero-empty">Backend not reachable: no estimate can be shown.</div>';
      return;
    }
    const rateBlock = (num, sub) => `<div class="rate"><span class="lbl">Rate (kg CH₄/h)</span>${num}${sub}</div>`;
    if (!r) {
      title.textContent = 'Emission rate not estimated yet';
      body.innerHTML = rateBlock('<div class="num none">—</div>', '') +
        '<div class="hero-empty">No estimate has run for this incident. Choose an available method on the right and select <b>Run estimate</b>.</div>';
      return;
    }
    if (r.rate_kg_ch4_h == null) {
      title.textContent = 'Emission rate unavailable';
      body.innerHTML = rateBlock('<div class="num none">No estimate</div>', '') +
        `<div class="hero-empty">${esc(r.failure)}<ul>${(r.missing || []).map((m) => `<li>${esc(m)}</li>`).join('')}</ul></div>`;
      return;
    }
    title.textContent = 'Emission-rate estimate';
    $('res-badges').innerHTML = badge(r.provenance, `Produced by ${r.model_version}`);
    const qc = r.qc || {};
    body.innerHTML = rateBlock(`<div class="num">${r.rate_kg_ch4_h.toFixed(2)}</div>`,
      `<div class="unit">kg CH₄/h</div><div class="sub">Median of ${qc.samples_used} per-sample estimates</div>`) + gauge(r);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const b = body.querySelector('.band');
      if (b) b.classList.remove('pre');
    }));

    const w = r.data_window || {}, iv = r.interval;
    const f = (k, v, sub) => `<div class="f"><span class="lbl">${k}</span><div class="v">${v}${sub ? `<small>${sub}</small>` : ''}</div></div>`;
    fields.innerHTML =
      f('Uncertainty interval', iv ? `${iv.low.toFixed(2)} – ${iv.high.toFixed(2)}` : 'Not computed', iv ? `kg/h · ${esc(iv.basis)}` : '') +
      f('Method', esc(METHOD_LABEL[r.method] || r.method), `Source: ${esc(r.source_asset_id)} (hypothesis)`) +
      f('Data window', `${hhmm(w.start_utc)}–${hhmm(w.end_utc)}`, `UTC · ${qc.samples_in_window} samples`) +
      f('Model version', esc(r.model_version), esc(r.model_note || '')) +
      f('Input quality', `${qc.samples_used} / ${qc.samples_in_window} used`, `${qc.samples_off_axis} off plume axis`) +
      f('Assumptions', `<a href="#assumptions-panel">${(r.assumptions || []).length} listed</a>`, 'Geometry is draft');
    fields.hidden = false;
  }

  function gauge(r) {
    const rates = (r.samples || []).filter((s) => s.used).map((s) => s.rate_kg_ch4_h);
    const ref = Q.release_check;
    const top = Math.max(...rates, r.interval ? r.interval.high : 0, ref ? ref.rate_kg_ch4_h : 0) * 1.1;
    const step = top > 8 ? 2 : top > 4 ? 1 : 0.5;
    const max = Math.ceil(top / step) * step;
    const pct = (v) => `${(Math.min(v, max) / max) * 100}%`;
    const ticks = [];
    for (let v = 0; v <= max + 1e-9; v += step) ticks.push(`<div class="g-tick" style="left:${pct(v)}"><span>${+v.toFixed(1)}</span></div>`);
    const dots = rates.map((v, i) => `<div class="g-dot" style="left:${pct(v)};top:${14 + (i % 4) * 6}px" title="${v.toFixed(2)} kg/h"></div>`).join('');
    const iv = r.interval;
    const band = iv ? `<div class="band pre" style="left:${pct(iv.low)};width:calc(${pct(iv.high)} - ${pct(iv.low)})"></div>` : '';
    const refEl = ref ? `<div class="ref" style="left:${pct(ref.rate_kg_ch4_h)}" title="Reference release rate"></div>` : '';
    return `<div class="gauge" aria-label="Per-sample estimates on a kg/h scale">
      <div class="g-track"><div class="g-axis"></div>${ticks.join('')}${band}${dots}${refEl}
        <div class="med" style="left:${pct(r.rate_kg_ch4_h)}" title="Median ${r.rate_kg_ch4_h} kg/h"></div></div>
      <div class="g-legend">
        <span><i class="lm"></i>Median</span>
        ${iv ? '<span><i class="lb"></i>P10–P90 interval</span>' : ''}
        <span><i class="ld"></i>Per-sample estimate</span>
        ${ref ? `<span><i class="lr"></i>Reference release rate · ${ref.rate_kg_ch4_h} kg/h</span>` : ''}
      </div></div>`;
  }

  // ----- stacked sensor strips -----
  function renderTraces() {
    const el = $('traces'), labels = $('labels');
    const have = STRIPS.filter((s) => S && (S.series[s.q] || []).length);
    if (!have.length) {
      labels.innerHTML = '';
      el.innerHTML = '<div class="traces-empty">No sensor data available for this incident.</div>';
      $('series-meta').textContent = 'No sensor data';
      return;
    }
    const r = Q && Q.result, w = r && r.data_window;
    const times = S.series[have[0].q].map((p) => p[0]);
    const cats = times.map(hhmm);
    const last = times.length - 1;
    $('series-meta').textContent = `${hhmm(S.site.replay_start_utc)}–${hhmm(S.site.replay_end_utc)} UTC · 30 s samples`;
    $('t-legend').hidden = !(w && w.start_utc);

    labels.style.paddingBottom = `${AXIS_H}px`;
    labels.innerHTML = have.map((s) => `
      <div class="lab" style="height:${s.h}px">
        <div class="nm">${s.name}</div>
        <div class="row">${esc((S.sensor_of || {})[s.q] || '')} · ${s.unit}</div>
        <div class="row">${badge(S.provenance, (S.source || []).join(', '))}</div>
        <div class="rd" data-rd="${s.q}">—<small>${s.unit}</small></div>
      </div>`).join('');

    el.style.height = `${have.reduce((a, s) => a + s.h, 0) + AXIS_H}px`;
    traces = traces || echarts.init(el, null, { renderer: 'canvas' });
    const used = new Map(((r && r.samples) || []).map((x) => [x.t, x.used]));
    const area = w && w.start_utc ? [[{ xAxis: hhmm(w.start_utc) }, { xAxis: hhmm(w.end_utc) }]] : [];
    let y = 0;
    const grids = [], xAxes = [], yAxes = [], series = [];
    have.forEach((s, i) => {
      const lastStrip = i === have.length - 1, col = css(s.color);
      grids.push({ left: 52, right: 22, top: y + 10, height: s.h - 20 });
      y += s.h;
      xAxes.push({
        gridIndex: i, type: 'category', data: cats, boundaryGap: false,
        axisLine: { lineStyle: { color: css('--ink-faint') } }, axisTick: { show: false },
        axisLabel: { show: lastStrip, color: css('--ink-dim'), fontFamily: MONO, fontSize: 10, interval: 9, margin: 10 },
        axisPointer: { label: { show: lastStrip, fontFamily: MONO, fontSize: 10, backgroundColor: css('--ink') } },
      });
      yAxes.push({
        gridIndex: i, type: 'value', scale: true, splitNumber: s.h > 120 ? 3 : 2,
        splitLine: { lineStyle: { color: css('--line') } },
        axisLabel: { color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5 },
      });
      const pts = S.series[s.q];
      series.push({
        type: 'line', xAxisIndex: i, yAxisIndex: i, data: pts.map((p) => p[1]), showSymbol: false,
        lineStyle: { width: 1.6, color: col }, itemStyle: { color: col },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: col + '22' }, { offset: 1, color: col + '00' }]) },
        markArea: { silent: true, itemStyle: { color: css('--accent-glow'), opacity: .5 }, data: area },
      });
      if (s.q === 'methane_ppm' && used.size) {
        // QC: which concentration samples the estimate used vs excluded (from the backend result).
        const mark = (u) => pts.map((p) => (used.get(p[0]) === u ? p[1] : null));
        series.push({ type: 'scatter', xAxisIndex: i, yAxisIndex: i, data: mark(true), symbolSize: 5, itemStyle: { color: col }, z: 3 });
        series.push({ type: 'scatter', xAxisIndex: i, yAxisIndex: i, data: mark(false), symbolSize: 5, itemStyle: { color: css('--panel'), borderColor: css('--ink-dim'), borderWidth: 1.2 }, z: 3 });
      }
    });
    traces.setOption({
      animation: false, grid: grids, xAxis: xAxes, yAxis: yAxes, series,
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: css('--ink-dim'), type: 'dashed' } },
      tooltip: { trigger: 'axis', showContent: false },
    }, true);
    traces.resize();

    const readAt = (idx, live) => {
      have.forEach((s) => {
        const v = S.series[s.q][idx];
        const node = labels.querySelector(`[data-rd="${s.q}"]`);
        if (node && v) node.innerHTML = `${v[1].toFixed(s.digits)}<small>${s.unit}</small>`;
      });
      const u = used.get(times[idx]);
      $('time-rd').textContent = `${live ? '' : 'Latest · '}${hhmmss(times[idx])} UTC${u === true ? ' · sample used in estimate' : u === false ? ' · sample excluded (off plume axis)' : ''}`;
    };
    readAt(last, false);
    traces.off('updateAxisPointer');
    traces.on('updateAxisPointer', (e) => {
      const ax = (e.axesInfo || [])[0];
      if (ax && ax.value != null) readAt(ax.value, true); else readAt(last, false);
    });
  }

  // ----- assumptions + QC -----
  function renderLower() {
    const r = Q && Q.result;
    if (qcChart) { qcChart.dispose(); qcChart = null; }
    if (!r || !r.qc) {
      $('assumptions').innerHTML = '<div class="pending-box">Shown after an estimate runs.</div>';
      $('qc').innerHTML = '<div class="pending-box">Shown after an estimate runs.</div>';
      $('assume-meta').textContent = '';
      return;
    }
    $('assume-meta').textContent = `${r.assumptions.length} · ${r.model_version}`;
    $('assumptions').innerHTML = `<ol class="assume">${r.assumptions.map((a) => `<li>${esc(a)}</li>`).join('')}</ol>`;
    const qc = r.qc;
    const row = (k, v) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`;
    $('qc').innerHTML = `<div class="qc-body">
      <div class="kv">
        ${row('Background', `${qc.background_ppm} ppm`)}
        ${row('Background basis', esc(qc.background_basis))}
        ${row('Window start rule', esc(qc.onset_rule))}
        ${row('Exclusion rule', esc(qc.off_axis_rule || ''))}
        ${row('Samples used / excluded', `${qc.samples_used} / ${qc.samples_off_axis}`)}
      </div>
      ${r.interval ? '<div class="qc-chart-h"><b>Per-sample estimates</b><span>kg CH₄/h · band = P10–P90 · line = median</span></div><div id="qc-chart"></div>' : ''}
    </div>`;
    if (!r.interval) return;
    qcChart = echarts.init($('qc-chart'), null, { renderer: 'canvas' });
    const smp = r.samples || [], vio = css('--violet');
    qcChart.setOption({
      animation: false,
      grid: { left: 40, right: 12, top: 10, bottom: 24 },
      tooltip: { trigger: 'item', textStyle: { fontFamily: MONO, fontSize: 11 }, formatter: (p) => `${p.name} UTC<br><b>${p.value.toFixed(2)} kg/h</b>` },
      xAxis: { type: 'category', data: smp.map((s) => hhmmss(s.t)), axisTick: { show: false }, axisLine: { lineStyle: { color: css('--ink-faint') } },
        axisLabel: { color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5, interval: 19, formatter: (v) => v.slice(0, 5) } },
      yAxis: { type: 'value', min: 0, splitNumber: 3, splitLine: { lineStyle: { color: css('--line') } }, axisLabel: { color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5 } },
      series: [{
        type: 'scatter', symbolSize: 6, itemStyle: { color: vio, opacity: .75 },
        data: smp.map((s) => (s.used ? s.rate_kg_ch4_h : null)),
        markArea: { silent: true, itemStyle: { color: 'rgba(46,99,153,.08)' }, data: [[{ yAxis: r.interval.low }, { yAxis: r.interval.high }]] },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: vio, width: 1.5, type: 'solid' }, data: [{ yAxis: r.rate_kg_ch4_h }] },
      }],
    });
  }

  // ----- method + inputs -----
  function renderMethods() {
    const ms = (Q && Q.methods) || [];
    ms.forEach((m) => { METHOD_LABEL[m.id] = m.label; });
    if (!method || !ms.some((m) => m.id === method && !m.missing.length)) {
      method = (ms.find((m) => !m.missing.length) || {}).id || null;
    }
    $('methods').innerHTML = ms.map((m) => {
      const off = m.missing.length > 0;
      return `<label class="meth${off ? ' off' : ''}${m.id === method ? ' sel' : ''}">
        <input type="radio" name="method" value="${esc(m.id)}"${m.id === method ? ' checked' : ''}${off ? ' disabled' : ''}>
        <b>${esc(m.label)}</b><span class="nt">${esc(m.note)}</span>
        ${off ? `<span class="why">Unavailable: ${m.missing.map(esc).join('; ')}</span>` : ''}
      </label>`;
    }).join('') || '<div class="note">No methods reported by the backend.</div>';
    $('btn-run').disabled = !method;
    $('btn-run').textContent = Q && Q.result ? 'Re-run estimate' : 'Run estimate';
    const ready = Q && Q.result && Q.result.rate_kg_ch4_h != null;
    $('next-note').textContent = ready ? 'Estimate ready for engineer review.' : 'Run an estimate first.';
    $('btn-plan').setAttribute('aria-disabled', String(!ready));
  }

  function renderInputs() {
    const r = Q && Q.result, so = (S && S.sensor_of) || {};
    const prov = S ? S.provenance : 'pending';
    const li = (id, t, b) => `<li><span class="id">${esc(id)}</span><span class="t" title="${esc(t)}">${esc(t)}</span>${b}</li>`;
    const items = [];
    if (so.methane_ppm) items.push(li(so.methane_ppm, 'Methane concentration', badge(prov)));
    if (so.wind_speed_m_s) items.push(li(so.wind_speed_m_s, 'Wind speed + direction', badge(prov)));
    if (so.pressure_bara) items.push(li(so.pressure_bara, 'Pressure (context only)', badge(prov)));
    items.push(li('Geometry', 'Draft positions + heights', badge('synthetic', 'scenario/geometry.json (draft)')));
    if (r && r.source_asset_id) items.push(li(r.source_asset_id, 'Assumed source', badge('hypothesis', 'Top Locate candidate, unconfirmed')));
    $('inputs').innerHTML = items.join('');
  }

  function renderAll() {
    renderMethods(); renderHero(); renderInputs(); renderTraces(); renderLower();
  }

  async function run() {
    const btn = $('btn-run'), msg = $('run-msg');
    btn.disabled = true;
    msg.className = 'run-msg'; msg.textContent = 'Running on backend…';
    try {
      const r = await fetch(`${API}/incidents/${INCIDENT_ID}/quantify`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error((body.detail && body.detail.failure) || body.detail || r.status);
      Q = body;
      msg.textContent = `Estimate run at ${Q.result.run_at_utc.slice(11, 19)} UTC`;
      renderAll();
      window.AtelicShell.refresh();
    } catch (e) {
      msg.className = 'run-msg err'; msg.textContent = `Estimate failed: ${e.message}`;
      btn.disabled = false;
    }
  }

  $('methods').addEventListener('change', (e) => { method = e.target.value; renderMethods(); });
  $('btn-run').addEventListener('click', run);
  $('btn-plan').addEventListener('click', (e) => { if ($('btn-plan').getAttribute('aria-disabled') === 'true') e.preventDefault(); });
  window.addEventListener('resize', () => { if (traces) traces.resize(); if (qcChart) qcChart.resize(); });
  // The QC chart fills its stretched panel, whose height settles after the assumptions render.
  new ResizeObserver(() => { if (qcChart) qcChart.resize(); }).observe($('qc'));

  async function init() {
    const [s, q] = await Promise.allSettled([
      get(`/incidents/${INCIDENT_ID}/sensor-series`), get(`/incidents/${INCIDENT_ID}/quantification`),
    ]);
    S = s.status === 'fulfilled' ? s.value : null;
    Q = q.status === 'fulfilled' ? q.value : null;
    if (typeof echarts === 'undefined') await new Promise((r) => window.addEventListener('load', r, { once: true }));
    renderAll();
  }
  init();
})();
