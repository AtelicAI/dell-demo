// Screen 4 — Quantify / Emissions. Reads GET /incidents/:id/sensor-series and /quantification;
// "Run" calls POST /incidents/:id/quantify. No number is shown without a backend result.
// One clock with the rest of the app: the sensor traces draw up to the shell's incident clock, and when the agent's
// estimate is dated ahead of it the clock catches up while the agent's steps play out (as Detect fast-forwards).
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const { API, INCIDENT_ID, get } = window.AtelicShell;
  const shell = window.AtelicShell;

  // Simulated / synthetic provenance is tracked in the data but not shown.
  const { badge } = window.AtelicShell;
  // Stacked strips on one time axis; methane gets the most height.
  const STRIPS = [
    { q: 'methane_ppm', name: 'Methane concentration', unit: 'ppm', color: '--n-well', digits: 2, h: 160 },
    { q: 'wind_speed_m_s', name: 'Wind speed', unit: 'm/s', color: '--n-equip', digits: 2, h: 104 },
    { q: 'wind_dir_deg_from_north', name: 'Wind direction', unit: '° from north', color: '--n-wt', digits: 1, h: 104 },
    { q: 'pressure_bara', name: 'Process pressure', unit: 'bar absolute', color: '--n-station', digits: 3, h: 104 },
    { q: 'obukhov_m', name: 'Stability (Obukhov length)', unit: 'm, sonic', color: '--violet', digits: 1, h: 104 },
  ];
  const ROLE = { primary: 'Agent’s choice', cross_check: 'Cross-check', waiting: 'Waiting', not_needed: 'Not needed here', blocked: 'Cannot run', recommended: 'Would fit', pending: 'Pending' };
  const AXIS_H = 28;
  const MONO = 'IBM Plex Mono, monospace';
  const METHOD_LABEL = {};
  const AGREE = [0.67, 1.5];
  const hhmm = (t) => (t ? t.slice(11, 16) : '—');
  const hhmmss = (t) => (t ? t.slice(11, 19) : '—');
  const iso = (ms) => new Date(ms).toISOString();
  const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
  // Method names without the catalogue acronyms.
  const lbl = (s) => String(s || '').replace(/ \((QOGI|bLs)\)$/, '');
  const mver = (r) => String(r.model_version || '').replace(/^qogi-/, 'camera-');
  const why = (s) => String(s || '').replace(/_on_this_edge_box/, '').replace(/_/g, ' ');

  // QD: the backend's quantification. Q: what the page shows at the current clock (QD, or the agent still working).
  let S = null, QD = null, Q = null, traces = null, qcChart = null, H = null, histChart = null, aChart = null;
  let alarmMs = null, catching = false;
  const nowMs = () => { const n = shell.replayNow(); return n ? n.getTime() : null; };

  // ----- one clock: the estimate appears when the incident clock reaches the moment the agent froze it -----
  const freezeMs = (r) => {
    if (!r) return null;
    const end = r.data_window && Date.parse(r.data_window.end_utc);
    if (end) return end;
    const att = r.attempts || [];
    return att.length ? Date.parse(att[att.length - 1].t) : null;
  };
  const visibleAttempts = () => {
    const att = (QD && QD.result && QD.result.attempts) || [], n = nowMs();
    return n == null ? att : att.filter((a) => Date.parse(a.t) <= n);
  };
  function pending() {
    const r = QD && QD.result, f = freezeMs(r), n = nowMs();
    return Boolean(r && r.rate_kg_ch4_h != null && f && n != null && n < f);
  }
  function view() {
    if (!QD || !pending()) { Q = QD; return; }
    const seen = visibleAttempts(), last = seen[seen.length - 1];
    Q = { ...QD, result: null, conditions: null,
      progress: { text: last ? `${hhmmss(last.t)} UTC · ${last.text}` : 'Reading the case: wind, stability, sensors and camera' } };
  }

  // ----- what has been run: the agent's choice plus every method the engineer ran -----
  function runSet() {
    const r = Q && Q.result;
    if (!r || r.rate_kg_ch4_h == null) return [];
    const band = (o) => (o && o.sensitivity_kg_h && o.sensitivity_kg_h.min != null ? { low: o.sensitivity_kg_h.min, high: o.sensitivity_kg_h.max } : null);
    return ((Q.methods) || []).filter((m) => m.connected).map((m) => {
      const prim = m.id === r.primary_tool, e = m.engineer_run;
      const v = e && e.rate_kg_h != null ? e.rate_kg_h : prim ? r.rate_kg_ch4_h : null;
      if (v == null) return null;
      return { m, v, prim, band: prim && r.interval ? { low: r.interval.low, high: r.interval.high } : band(m.output) };
    }).filter(Boolean).sort((a, b) => (b.prim - a.prim));
  }

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
    if (!r && Q.progress) {
      title.textContent = 'Quantify agent working';
      body.innerHTML = rateBlock('<div class="num none">…</div>', '') +
        `<div class="hero-empty"><span class="ag-spin"></span> ${esc(Q.progress.text)}.<br>Every 10 s the agent reads the case (wind, stability, sensors, camera), picks the tool built for it and tries again until that tool has a steady window. No number is shown before that.</div>`;
      return;
    }
    if (!r) {
      title.textContent = 'Emission rate not estimated yet';
      body.innerHTML = rateBlock('<div class="num none">—</div>', '') +
        '<div class="hero-empty">No estimate yet. The quantify agent starts after Locate and picks its tool from the case.</div>';
      return;
    }
    if (r.rate_kg_ch4_h == null) {
      title.textContent = 'Emission rate unavailable';
      body.innerHTML = rateBlock('<div class="num none">No estimate</div>', '') +
        `<div class="hero-empty">${esc(r.failure)}<ul>${(r.missing || []).map((m) => `<li>${esc(m)}</li>`).join('')}</ul></div>`;
      return;
    }
    const runs = runSet(), n = runs.length, vals = runs.map((x) => x.v);
    const avg = n > 1 ? vals.reduce((a, b) => a + b, 0) / n : r.rate_kg_ch4_h;
    title.textContent = n > 1 ? 'Emission-rate estimate · combined' : 'Emission-rate estimate';
    $('res-badges').innerHTML = badge(r.provenance, `Produced by ${mver(r)}`);
    const qc = r.qc || {}, cam = r.primary_tool === 'qogi';
    const lead = cam ? 'the camera' : lbl(METHOD_LABEL[r.primary_tool] || r.primary_tool);
    // Every other method run, against the agent's choice (agreement: 0.67–1.5×).
    const others = runs.filter((x) => !x.prim), ok = others.filter((x) => { const k = x.v / r.rate_kg_ch4_h; return k >= AGREE[0] && k <= AGREE[1]; });
    const ccLine = others.length ? `<div class="sub cc ${ok.length === others.length ? 'ok' : 'no'}">${ok.length} of ${others.length} ${others.length === 1 ? 'check agrees' : 'checks agree'} with ${esc(lead)}${ok.length < others.length
      ? `; ${others.filter((x) => !ok.includes(x)).map((x) => esc(lbl(x.m.label))).join(', ')} ${others.length - ok.length === 1 ? 'disagrees' : 'disagree'}` : ''}</div>` : '';
    const sub = n > 1
      ? `Range ${Math.min(...vals).toFixed(2)}–${Math.max(...vals).toFixed(2)} kg/h · each method weighted equally`
      : `${qc.samples_used} ${cam ? 'camera frames over a 90 s window' : 'sensor readings over 60 s'}`;
    body.innerHTML = rateBlock(`<div class="num">≈ ${avg.toFixed(2)}</div>`,
      `<div class="unit">kg CH₄/h · ${n > 1 ? `Average of ${n} methods` : esc(lbl(METHOD_LABEL[r.primary_tool] || r.primary_tool))}</div><div class="sub">${sub}</div>${ccLine}`) + compare(runs, n > 1 ? avg : null);
    requestAnimationFrame(() => requestAnimationFrame(() => body.querySelectorAll('.cband.pre').forEach((b) => b.classList.remove('pre'))));

    const w = r.data_window || {}, iv = r.interval;
    const f = (k, v, s) => `<div class="f"><span class="lbl">${k}</span><div class="v">${v}${s ? `<small>${s}</small>` : ''}</div></div>`;
    const case_ = r.case || {};
    fields.innerHTML =
      f('Uncertainty interval', iv ? `${iv.low.toFixed(2)} – ${iv.high.toFixed(2)}` : 'Not computed', iv ? `kg/h · ${esc(iv.basis)}` : '') +
      f('Tool chosen by the agent', esc(lbl(METHOD_LABEL[r.primary_tool] || r.primary_tool)), `Source: ${esc(r.source_asset_id)} (hypothesis)`) +
      f('Data window', cam ? '90 s of camera frames' : '60 s of sensor readings', w.end_utc ? `ending ${hhmmss(w.end_utc)} UTC` : '') +
      f('Model version', esc(mver(r)), esc(r.model_note || '')) +
      f('Case read by the agent', case_.wind !== 'available' ? 'Weather mast down' : `${esc((case_.stability || '').replace('_', ' '))} air · ${case_.methane_sensors_reporting} sensors`,
        case_.wind !== 'available' ? 'Camera sees the plume' : `${case_.camera_candidates} camera candidates · wind ${case_.wind_speed_m_s} m/s${cam ? ' · camera sees the plume' : ''}`) +
      f('Assumptions', `<a href="#assumptions-panel">${(r.assumptions || []).length} listed</a>`, 'Geometry is draft');
    fields.hidden = false;
  }

  // One strip per method run, on one kg/h scale: dot = its rate, band = its uncertainty, line = the average.
  // Methods not run yet sit underneath as Run buttons.
  function compare(runs, avg) {
    const r = Q.result, vals = runs.flatMap((x) => [x.v, x.band ? x.band.high : 0]);
    const top = Math.max(...vals, 0.1) * 1.15;
    const step = top > 8 ? 2 : top > 4 ? 1 : top > 1 ? 0.5 : top > 0.5 ? 0.1 : 0.05;
    const max = Math.ceil(top / step) * step;
    const pct = (v) => `${(Math.min(Math.max(v, 0), max) / max) * 100}%`;
    const ticks = [];
    for (let v = 0; v <= max + 1e-9; v += step * (max / step > 6 ? 2 : 1)) ticks.push(`<i class="ctk" style="left:${pct(v)}"><span>${+v.toFixed(2)}</span></i>`);
    const avgEl = avg != null ? `<i class="cavg" style="left:${pct(avg)}"></i>` : '';
    const rows = runs.map((x) => `<div class="crow${x.prim ? ' prim' : ''}">
        <span class="tn" title="${esc(lbl(x.m.label))}">${esc(lbl(x.m.label))}${x.prim ? '<small>agent</small>' : ''}</span>
        <span class="tt">${x.band ? `<i class="cband pre" style="left:${pct(x.band.low)};width:calc(${pct(x.band.high)} - ${pct(x.band.low)})" title="${x.band.low.toFixed(2)}–${x.band.high.toFixed(2)} kg/h"></i>` : ''}${avgEl}<i class="cdot" style="left:${pct(x.v)}" title="${x.v.toFixed(3)} kg/h"></i></span>
        <span class="tv">${x.v.toFixed(3)}</span></div>`).join('');
    const axis = `<div class="crow axis"><span></span><span class="tt">${ticks.join('')}</span><span class="tv">kg/h</span></div>`;
    const done = new Set(runs.map((x) => x.m.id));
    const rest = ((Q.methods) || []).filter((m) => m.connected && !done.has(m.id));
    const chip = (m) => {
      const e = m.engineer_run, failed = e && e.rate_kg_h == null;
      return `<button class="crun${failed ? ' failed' : ''}" type="button" data-tool="${esc(m.id)}" title="${esc(failed ? `No rate: ${why(e.reason || e.status)}` : m.reason || '')}">
        <span>${esc(lbl(m.label))}</span><b>${failed ? 'No rate · retry' : 'Run'}</b></button>`;
    };
    return `<div class="cmp" aria-label="Rate from each method run, kg/h">
      <div class="crows">${rows}${axis}</div>
      <div class="c-legend"><span><i class="ld"></i>Method estimate</span><span><i class="lb"></i>Uncertainty</span>${avg != null ? '<span><i class="la"></i>Average</span>' : ''}</div>
      ${rest.length ? `<div class="cmore"><span class="lbl">${runs.length > 1 ? 'Add another method to the average' : 'Run another method to compare and average'}</span><div class="chips">${rest.map(chip).join('')}</div></div>` : ''}
    </div>`;
  }

  // ----- stacked sensor strips, on the incident clock -----
  function seriesUpTo(pts, n) {
    return pts.filter((p) => n == null || Date.parse(p[0]) <= n).map((p) => [Date.parse(p[0]), p[1]]);
  }

  function traceOption(have) {
    const n = nowMs(), r = Q && Q.result, w = r && r.data_window;
    const t0 = Date.parse(S.site.replay_start_utc), t1 = Date.parse(S.site.replay_end_utc);
    const used = new Map(((r && r.samples) || []).map((x) => [Date.parse(x.t), x.used]));
    const area = w && w.start_utc && (n == null || Date.parse(w.end_utc) <= n)
      ? [[{ xAxis: Date.parse(w.start_utc) }, { xAxis: Date.parse(w.end_utc) }]] : [];
    const marks = [];
    if (alarmMs && (n == null || alarmMs <= n)) marks.push({ xAxis: alarmMs, lineStyle: { color: css('--bad') || '#B42318', type: 'solid', width: 1.4 }, label: { formatter: 'Alarm' } });
    if (n != null && n >= t0 && n <= t1) marks.push({ xAxis: n, lineStyle: { color: css('--ink-dim'), type: 'dashed', width: 1 }, label: { formatter: 'Now' } });
    let y = 0;
    const grids = [], xAxes = [], yAxes = [], series = [];
    have.forEach((s, i) => {
      const lastStrip = i === have.length - 1, first = i === 0, col = css(s.color);
      grids.push({ left: 52, right: 22, top: y + 10, height: s.h - 20 });
      y += s.h;
      xAxes.push({
        gridIndex: i, type: 'time', min: t0, max: t1,
        axisLine: { lineStyle: { color: css('--ink-faint') } }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: { show: lastStrip, color: css('--ink-dim'), fontFamily: MONO, fontSize: 10, margin: 10, formatter: (v) => iso(v).slice(11, 16), hideOverlap: true },
        axisPointer: { label: { show: lastStrip, fontFamily: MONO, fontSize: 10, backgroundColor: css('--ink'), formatter: (p) => `${iso(p.value).slice(11, 19)} UTC` } },
      });
      yAxes.push({
        gridIndex: i, type: 'value', scale: true, splitNumber: s.h > 120 ? 3 : 2,
        splitLine: { lineStyle: { color: css('--line') } },
        axisLabel: { color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5 },
      });
      const pts = seriesUpTo(S.series[s.q], n);
      series.push({
        type: 'line', xAxisIndex: i, yAxisIndex: i, data: pts, showSymbol: false,
        lineStyle: { width: 1.6, color: col }, itemStyle: { color: col },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: col + '22' }, { offset: 1, color: col + '00' }]) },
        markArea: { silent: true, itemStyle: { color: css('--accent-glow'), opacity: .5 }, data: area },
        markLine: { silent: true, symbol: 'none', data: marks.map((m) => ({ ...m, label: { show: first, position: 'insideEndTop', fontFamily: MONO, fontSize: 9.5, color: m.lineStyle.color, ...m.label } })) },
      });
      if (s.q === 'methane_ppm') {
        // QC: which concentration samples the estimate used vs excluded (from the backend result).
        const mark = (u) => pts.filter((p) => used.get(p[0]) === u);
        series.push({ type: 'scatter', xAxisIndex: i, yAxisIndex: i, data: mark(true), symbolSize: 5, itemStyle: { color: col }, z: 3 });
        series.push({ type: 'scatter', xAxisIndex: i, yAxisIndex: i, data: mark(false), symbolSize: 5, itemStyle: { color: css('--panel'), borderColor: css('--ink-dim'), borderWidth: 1.2 }, z: 3 });
      }
    });
    return { animation: false, grid: grids, xAxis: xAxes, yAxis: yAxes, series,
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: css('--ink-dim'), type: 'dashed' } },
      tooltip: { trigger: 'axis', showContent: false } };
  }

  let have = [], hovering = false;
  function seriesMeta() {
    const n = nowMs(), t0 = Date.parse(S.site.replay_start_utc), t1 = Date.parse(S.site.replay_end_utc);
    $('series-meta').innerHTML = n != null && n < t0 ? 'Waiting for the first readings'
      : n != null && n <= t1 ? `<span class="live-dot"></span>Live · ${iso(n).slice(11, 19)} UTC`
        : `${hhmm(S.site.replay_start_utc)}–${hhmm(S.site.replay_end_utc)} UTC · incident window`;
    const r = Q && Q.result;
    $('t-legend').hidden = !(r && r.data_window && r.data_window.start_utc);
  }

  // Readout beside each strip: the value under the pointer, else the latest reading on the clock.
  function readAt(ms, live) {
    let at = null;
    have.forEach((s) => {
      const pts = seriesUpTo(S.series[s.q], nowMs());
      if (!pts.length) return;
      let best = pts[pts.length - 1];
      if (ms != null) best = pts.reduce((b, p) => (Math.abs(p[0] - ms) < Math.abs(b[0] - ms) ? p : b), best);
      at = best[0];
      const node = $('labels').querySelector(`[data-rd="${s.q}"]`);
      if (node) node.innerHTML = `${best[1].toFixed(s.digits)}<small>${s.unit}</small>`;
    });
    const used = new Map((((Q && Q.result) || {}).samples || []).map((x) => [Date.parse(x.t), x.used]));
    const u = at != null ? used.get(at) : undefined;
    $('time-rd').textContent = at == null ? 'No readings yet'
      : `${live ? '' : 'Latest · '}${iso(at).slice(11, 19)} UTC${u === true ? ' · sample used in estimate' : u === false ? ' · sample excluded (off plume axis)' : ''}`;
  }

  function renderTraces() {
    const el = $('traces'), labels = $('labels');
    have = STRIPS.filter((s) => S && (S.series[s.q] || []).length);
    if (!have.length) {
      labels.innerHTML = '';
      el.innerHTML = '<div class="traces-empty">No sensor data available for this incident.</div>';
      $('series-meta').textContent = 'No sensor data';
      return;
    }
    seriesMeta();
    labels.style.paddingBottom = `${AXIS_H}px`;
    labels.innerHTML = have.map((s) => `
      <div class="lab" style="height:${s.h}px">
        <div class="nm">${s.name}</div>
        <div class="row">${esc((S.sensor_of || {})[s.q] || '')} · ${s.unit}</div>
        <div class="row">${badge(S.provenance, '')}</div>
        <div class="rd" data-rd="${s.q}">—<small>${s.unit}</small></div>
      </div>`).join('');
    el.style.height = `${have.reduce((a, s) => a + s.h, 0) + AXIS_H}px`;
    traces = traces || echarts.init(el, null, { renderer: 'canvas' });
    traces.setOption(traceOption(have), true);
    traces.resize();
    readAt(null, false);
    traces.off('updateAxisPointer');
    traces.on('updateAxisPointer', (e) => {
      const ax = (e.axesInfo || [])[0];
      hovering = Boolean(ax && ax.value != null);
      readAt(hovering ? ax.value : null, hovering);
    });
    traces.getZr().off('globalout');
    traces.getZr().on('globalout', () => { hovering = false; readAt(null, false); });
  }

  // Each second: the traces grow to the incident clock (no full redraw, the hover stays).
  function tickTraces() {
    if (!traces || !have.length || !S) return;
    traces.setOption(traceOption(have));
    seriesMeta();
    if (!hovering) readAt(null, false);
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
    $('assume-meta').textContent = `${r.assumptions.length} · ${mver(r)}`;
    $('assumptions').innerHTML = `<ol class="assume">${r.assumptions.map((a) => `<li>${esc(a)}</li>`).join('')}</ol>`;
    const qc = r.qc;
    const row = (k, v) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`;
    $('qc').innerHTML = `<div class="qc-body">
      <div class="kv">${(qc.rows || []).map(([k, v]) => row(k, esc(v))).join('')}</div>
      ${r.interval ? `<div class="qc-chart-h"><b>${r.attempts ? 'Agent attempts' : 'Per-sample estimates'}</b><span>kg CH₄/h · band = ${bandName(r.interval).toLowerCase()} · line = estimate</span></div><div id="qc-chart"></div>` : ''}
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

  const bandName = (iv) => (/frame/.test(iv.basis) ? 'Frame-to-frame spread' : /sensitivity/.test(iv.basis) ? 'Assumption sensitivity' : 'P10–P90 interval');

  // ----- agent tools + inputs -----
  // Every rate tool with the agent's decision for this incident. Only the agent's choice has run; the others run on request.
  function renderMethods() {
    const ms = (QD && QD.methods) || [];
    ms.forEach((m) => { METHOD_LABEL[m.id] = m.label; });
    const r = Q && Q.result, ready = Boolean(r && r.rate_kg_ch4_h != null);
    const ran = new Map(runSet().map((x) => [x.m.id, x]));
    const probs = (o) => (o && o.sources ? `<span class="t-why">Leak probability: ${o.sources.slice(0, 3).map((x) => `${esc(x.asset_id)} ${x.leak_probability.toFixed(2)}`).join(' · ')}</span>` : '');
    const out = (m) => {
      const x = ran.get(m.id);
      if (x) return `<span class="t-out${x.prim ? '' : ' mine'}">${x.prim ? 'Agent’s estimate' : 'Your run'}: ${x.v.toFixed(3)} kg/h</span>${probs(m.engineer_run && m.engineer_run.rate_kg_h != null ? null : m.output)}`;
      const e = m.engineer_run;
      return e && e.rate_kg_h == null ? `<span class="t-out mine">Your run: no rate (${esc(why(e.reason || e.status))})</span>` : '';
    };
    const shown = Q && Q.methods ? Q.methods : ms;
    $('methods').innerHTML = shown.map((m) => `<div class="tool r-${esc(m.role)}">
        <div class="t-head"><b>${esc(lbl(m.label))}</b><span class="t-role">${esc(ROLE[m.role] || m.role)}</span></div>
        <span class="t-why">${esc(m.reason)}</span>${ready ? out(m) : ''}
        ${m.connected ? (ready && !ran.has(m.id) ? `<button class="t-run" data-tool="${esc(m.id)}">Run ${esc(lbl(m.label))}</button>` : '')
          : '<span class="t-na">Not deployed</span>'}
      </div>`).join('') || '<div class="note">No tools reported by the backend.</div>';
    $('btn-run').disabled = !ready;
    $('btn-run').textContent = "Re-run the agent's choice";
    $('next-note').textContent = ready ? 'Estimate ready for engineer review.' : Q && Q.progress ? 'The quantify agent is still working.' : 'No rate: the plan proceeds without one.';
    $('btn-plan').setAttribute('aria-disabled', String(!ready && !(Q && Q.result)));
    renderLog();
  }

  // What the agent did on each 10 s step, up to the incident clock: the wait is visible, not a static refusal.
  function renderLog() {
    const att = visibleAttempts();
    $('agent-log').innerHTML = att.length
      ? `<ol class="alog">${att.map((a) => `<li class="${a.status === 'estimated' ? 'ok' : ''}"><span class="at">${hhmmss(a.t)}</span><span>${esc(a.text || a.status)}</span></li>`).join('')}</ol>`
      : `<div class="pending-box">${Q && Q.progress ? esc(Q.progress.text) : 'Starts after Locate.'}</div>`;
    $('log-meta').textContent = att.length ? `${att.length} steps` : '';
    const box = $('agent-log').querySelector('.alog');
    if (box && catching) box.scrollTop = box.scrollHeight;
  }

  function renderInputs() {
    const r = Q && Q.result, so = (S && S.sensor_of) || {};
    const prov = S ? S.provenance : 'pending';
    const li = (id, t, b) => `<li><span class="id">${esc(id)}</span><span class="t" title="${esc(t)}">${esc(t)}</span>${b}</li>`;
    const items = [];
    if (so.methane_ppm) items.push(li(so.methane_ppm, 'Methane concentration', badge(prov)));
    if (so.wind_speed_m_s) items.push(li(so.wind_speed_m_s, 'Wind speed + direction', badge(prov)));
    if (so.obukhov_m) items.push(li('MET-01 sonic', 'Turbulence u* + stability L', badge(prov)));
    if (r && r.primary_tool === 'qogi') items.push(li(r.camera ? r.camera.camera_id : 'Camera', 'Radiometric frames + plume mask', badge(prov)));
    if (so.pressure_bara) items.push(li(so.pressure_bara, 'Pressure (context only)', badge(prov)));
    items.push(li('Geometry', 'Facility positions + heights', badge('synthetic', 'plant knowledge graph')));
    if (r && r.source_asset_id) items.push(li(r.source_asset_id, 'Assumed source', badge('hypothesis', 'Top Locate candidate, unconfirmed')));
    $('inputs').innerHTML = items.join('');
  }

  // Every assumed source location fitted to the same readings: which ones explain them, which are contradicted.
  function renderAttr() {
    const r = Q && Q.result, a = r && r.attribution;
    $('attr-panel').hidden = !(a && (r.candidate_fits || []).length);
    if ($('attr-panel').hidden) return;
    const LBL = { agrees_single: 'Sensors agree with the camera', agrees_ambiguous: 'Sensors agree, but cannot separate candidates', partly_agrees: 'Partly agrees',
      disagrees: 'Sensors disagree with the camera', no_consistent_location: 'No location explains the readings', not_assessed: 'Not assessed' };
    const cams = new Set((a.camera_candidates || []).map((c) => c.candidate_asset_id));
    const fits = r.candidate_fits.filter((f) => f.fit_status !== 'cannot_test' && f.fit_status !== 'not_assessed')
      .sort((x, y) => (x.residual_rms_ppm ?? 9) - (y.residual_rms_ppm ?? 9)).slice(0, 8);
    $('attr-meta').textContent = `${r.candidate_fits.length} locations fitted`;
    $('attr').innerHTML = `<div class="attr"><p><b>${esc(LBL[a.status] || a.status)}</b>${a.best_fit_asset_id ? ` · best fit <b>${esc(a.best_fit_asset_id)}</b>` : ''}. ${esc(a.note || '')}</p>
      <table><tr><th>Assumed source</th><th>Camera candidate</th><th>Fit</th><th>Rate</th><th>Residual</th><th>vs best</th></tr>
      ${fits.map((f) => `<tr class="${f.fit_status === 'best_fit' ? 'best' : f.fit_status === 'poor_fit' ? 'poor' : ''}"><td>${esc(f.candidate_asset_id)}</td><td>${cams.has(f.candidate_asset_id) ? 'yes' : '—'}</td>
        <td>${esc((f.fit_status || '').replace(/_/g, ' '))}</td><td>${f.rate_kg_h != null ? `${f.rate_kg_h.toFixed(3)} kg/h` : '—'}</td>
        <td>${f.residual_rms_ppm != null ? `${f.residual_rms_ppm.toFixed(2)} ppm` : '—'}</td><td>${f.error_ratio_to_best != null ? `${f.error_ratio_to_best}×` : ''}</td></tr>`).join('')}</table>
      <p class="hist-note">Only best and comparable fits carry a rate. A location fitted outside the camera view is listed when it explains the readings too.</p>
      <div class="attr-bars" id="attr-bars"></div></div>`;
    // How much worse each assumed location explains the readings than the best one (1x = best).
    const bars = fits.filter((f) => f.error_ratio_to_best != null || f.fit_status === 'best_fit').slice(0, 6);
    aChart = echarts.init($('attr-bars'), null, { renderer: 'canvas' });
    aChart.setOption({
      animation: false, grid: { left: 70, right: 30, top: 18, bottom: 22 },
      title: { text: 'Fit error vs the best location (lower is better)', textStyle: { fontSize: 11, fontWeight: 500, color: css('--ink-mute') }, left: 0, top: 0 },
      xAxis: { type: 'value', min: 0, axisLabel: { formatter: (v) => `${v}×`, color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5 }, splitLine: { lineStyle: { color: css('--line') } } },
      yAxis: { type: 'category', inverse: true, data: bars.map((f) => f.candidate_asset_id), axisLabel: { color: css('--ink'), fontFamily: MONO, fontSize: 10.5 }, axisTick: { show: false } },
      series: [{ type: 'bar', barWidth: 12, data: bars.map((f) => ({ value: f.fit_status === 'best_fit' ? 1 : f.error_ratio_to_best,
        itemStyle: { color: f.fit_status === 'best_fit' ? css('--good') : f.fit_status === 'poor_fit' ? css('--ink-faint') : css('--n-equip') } })),
        label: { show: true, position: 'right', formatter: (p) => `${p.value}×`, fontFamily: MONO, fontSize: 10, color: css('--ink-mute') } }],
    }, true);
  }

  // Checks behind the number: the conditions the chosen tool needs.
  function renderConditions() {
    const c = Q && Q.conditions;
    $('cond-panel').hidden = !c;
    if (!c) return;
    $('cond-meta').textContent = c.all_ok ? 'All checks pass' : `${c.rows.filter((x) => !x.ok).length} check(s) fail`;
    const r = Q.result;
    $('cond').innerHTML = `<p class="cond-sum">${r && r.rate_kg_ch4_h != null
      ? (c.all_ok ? '<b>Within its conditions.</b> Every condition the chosen tool needs holds for this window.' : '<b>Treat with care.</b> Some conditions fail; the number is shown with them.')
      : '<b>Rate not estimated.</b> The checks below say why; no number is better than a wrong one.'}</p>
      <div class="cond">${c.rows.map((x) => `<div class="${x.ok ? 'ok' : 'no'}"><b>${esc(lbl(x.label.replace(/ agrees$/, '')))}${/ agrees$/.test(x.label) ? ' agrees' : ''}</b><span>${esc(x.detail)}</span></div>`).join('')}</div>
      <p class="hist-note">${esc(c.note)}</p>`;
  }

  // Which tool fits which case, and what the agent decided for this one.
  function renderCatalog() {
    const C = QD && QD.catalog;
    $('meth-panel').hidden = !C;
    if (!C) return;
    const mine = Object.fromEntries(((QD && QD.methods) || []).map((m) => [m.id, m]));
    $('meth-meta').textContent = `${C.methods.length} tools · ${C.methods.filter((m) => m.connected).length} connected`;
    $('meth').innerHTML = `<p class="mhead"><b>${esc(C.headline)}</b></p>
      <table class="tmatrix"><tr><th>Tool</th><th>Built for</th><th>Needs</th><th>This incident</th></tr>
      ${C.methods.map((m) => { const d = mine[m.id] || {}; return `<tr class="r-${esc(d.role || 'pending')}">
        <td><b>${esc(lbl(m.label))}</b><small>${esc(m.family)}</small></td><td>${esc(m.best_when)}</td>
        <td>${m.needs.map(esc).join(' · ')}</td>
        <td><span class="t-role">${esc(ROLE[d.role] || 'Pending')}</span> ${esc(d.reason || '')}</td></tr>`; }).join('')}</table>
      <details class="how"><summary>How each connected tool works</summary>
        ${C.methods.filter((m) => m.connected).map((m) => `<p><b>${esc(lbl(m.label))}.</b> ${esc(m.how)}</p>`).join('')}${HOW_SVG}</details>`;
  }

  // Sensors + wind -> one plume model per assumed location -> best fit gives the rate (static explainer).
  const HOW_SVG = `<svg viewBox="0 0 760 150" role="img" aria-label="Methane sensors and wind feed a plume model per candidate; the best fit gives the rate">
    <g font-family="IBM Plex Sans, sans-serif" font-size="12" fill="currentColor">
      <rect x="6" y="18" width="150" height="46" rx="4" fill="none" stroke="#94A3B8"/><text x="18" y="38">Methane sensors</text><text x="18" y="55" font-size="10.5" fill="#64748B">CH4-01 … CH4-05, ppm</text>
      <rect x="6" y="84" width="150" height="46" rx="4" fill="none" stroke="#94A3B8"/><text x="18" y="104">Wind</text><text x="18" y="121" font-size="10.5" fill="#64748B">speed + direction</text>
      <rect x="216" y="18" width="210" height="112" rx="4" fill="none" stroke="#2E6399"/><text x="230" y="40">Gaussian plume per location</text>
      <text x="230" y="62" font-size="10.5" fill="#64748B">XV-2104 → predicted ppm per kg/h</text><text x="230" y="80" font-size="10.5" fill="#64748B">F-205 → predicted ppm per kg/h</text>
      <text x="230" y="98" font-size="10.5" fill="#64748B">P-202 … (all positioned assets)</text><text x="230" y="118" font-size="10.5" fill="#64748B">steady-plume check, refuses otherwise</text>
      <rect x="486" y="18" width="268" height="112" rx="4" fill="none" stroke="#2F7D4F"/><text x="500" y="40">Fit and compare</text>
      <text x="500" y="62" font-size="10.5" fill="#64748B">least squares: rate per location</text><text x="500" y="80" font-size="10.5" fill="#64748B">best fit vs poor fit (error ratio)</text>
      <text x="500" y="98" font-size="10.5" fill="#64748B">rate ± assumption sensitivity</text><text x="500" y="118" font-size="10.5" fill="#64748B">agrees with the camera?</text>
      <path d="M156 41 L216 60 M156 107 L216 90 M426 74 L486 74" stroke="#64748B" stroke-width="1.4" fill="none" marker-end="url(#ar)"/>
      <defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#64748B"/></marker></defs>
    </g></svg>`;

  function renderHistory() {
    $('hist-panel').hidden = !H;
    if (!H) return;
    const a = H.analysis, lc = a.load_change_alignment || {}, obs = a.observability || [];
    const quiet = (a.quiet_stretches || []).slice().sort((x, y) => y.days - x.days)[0];
    $('hist-meta').textContent = `${a.period_start_utc.slice(0, 10)} → ${a.period_end_utc.slice(0, 10)} · 5-minute averages`;
    $('hist-facts').innerHTML = [
      [a.episode_count, 'methane episodes above background'],
      [`${lc.episodes_starting_after_load_change}/${lc.episodes}`, `start within ${lc.window_minutes} min of a K-201 load change (${Math.round((lc.share_expected_by_chance || 0) * 100)}% expected by chance)`],
      [quiet ? `${quiet.days} d` : '—', quiet ? `longest quiet stretch, ${quiet.start_utc.slice(5, 10)} → ${quiet.end_utc.slice(5, 10)}` : 'no quiet stretch'],
      [obs.length ? `${Math.round(Math.max(...obs.map((o) => o.observable_fraction)) * 100)}%` : '—', 'of the time a 50 g/h leak here would reach a sensor (wind)'],
    ].map(([v, t]) => `<div><b>${esc(v)}</b><span>${esc(t)}</span></div>`).join('');
    $('hist-note').textContent = `Episodes are found from the site's methane sensors; 5-minute fits narrow the area but give no rate.`;
    histChart = histChart || echarts.init($('hist-chart'), null, { renderer: 'canvas' });
    const pts = H.series.points, ss = H.series.sensors;
    const cols = ['--n-well', '--n-equip', '--n-wt', '--n-station', '--violet'];
    histChart.setOption({
      animation: false, grid: { left: 44, right: 14, top: 26, bottom: 30 },
      legend: { top: 0, textStyle: { fontFamily: MONO, fontSize: 10, color: css('--ink-mute') }, itemWidth: 12, itemHeight: 6 },
      tooltip: { trigger: 'axis', textStyle: { fontFamily: MONO, fontSize: 11 } },
      xAxis: { type: 'category', data: pts.map((p) => p.hour_utc.slice(5, 13).replace('T', ' ')), axisLabel: { color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5, interval: 95 }, axisTick: { show: false } },
      yAxis: { type: 'value', name: 'ppm above background', nameTextStyle: { color: css('--ink-dim'), fontSize: 10 }, splitLine: { lineStyle: { color: css('--line') } }, axisLabel: { color: css('--ink-dim'), fontFamily: MONO, fontSize: 9.5 } },
      series: ss.map((sid, i) => ({ name: sid, type: 'line', showSymbol: false, data: pts.map((p) => p[sid]), lineStyle: { width: 1, color: css(cols[i % cols.length]) }, itemStyle: { color: css(cols[i % cols.length]) } })),
    }, true);
  }

  function renderAll() {
    view();
    renderMethods(); renderHero(); renderInputs(); renderTraces(); renderLower(); renderConditions(); renderAttr(); renderCatalog(); renderHistory();
  }

  // The agent froze its estimate later on the incident clock than "now": play the clock forward to that moment while
  // its steps appear, then show the number (Detect does the same between the quiet watch and the event).
  async function catchUp() {
    const target = freezeMs(QD && QD.result), from = nowMs();
    if (catching || target == null || from == null || from >= target) return;
    catching = true;
    const span = target - from, dur = Math.min(6000, Math.max(2500, span / 50)), t0 = performance.now();
    let lastSeen = -1;
    await new Promise((done) => {
      const step = (t) => {
        const k = Math.min(1, (t - t0) / dur);
        shell.setReplayNow(from + span * (1 - (1 - k) ** 2));
        const seen = visibleAttempts().length;
        if (seen !== lastSeen) { lastSeen = seen; view(); renderHero(); renderLog(); }
        if (k < 1) requestAnimationFrame(step); else done();
      };
      requestAnimationFrame(step);
    });
    shell.setReplayNow(target + 500);
    catching = false;
    renderAll();
    shell.refresh();
  }

  async function run(tool, el) {
    const btn = $('btn-run'), msg = $('run-msg');
    const label = tool ? lbl(METHOD_LABEL[tool] || tool) : null;
    btn.disabled = true;
    msg.className = 'run-msg'; msg.textContent = `Running ${label || "the agent's choice"} on the same window…`;
    try {
      const [r] = await Promise.all([fetch(`${API}/incidents/${INCIDENT_ID}/quantify`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method: tool || 'agent', by: (() => { try { return localStorage.getItem('atelic.reviewer') || undefined; } catch (e) { return undefined; } })() }),
      }), wait(1200)]);
      const body = await r.json();
      if (!r.ok) throw new Error((body.detail && body.detail.failure) || body.detail || r.status);
      QD = body;
      const m = (QD.methods || []).find((x) => x.id === tool), e = m && m.engineer_run;
      msg.textContent = !label ? "Re-ran the agent's choice: same inputs, same result"
        : e && e.rate_kg_h != null ? `${label}: ${e.rate_kg_h.toFixed(3)} kg/h, added to the comparison` : `${label} gave no rate${e ? ` (${why(e.reason || e.status)})` : ''}`;
      renderAll();
      shell.refresh();
    } catch (err) {
      msg.className = 'run-msg err'; msg.textContent = `Estimate failed: ${err.message}`;
      btn.disabled = false;
      if (el) { el.disabled = false; el.classList.remove('busy'); }
    }
  }

  const busy = (el, tool) => {
    if (!el || el.disabled) return;
    document.querySelectorAll(`[data-tool="${CSS.escape(tool)}"]`).forEach((b) => { b.disabled = true; b.classList.add('busy'); });
    if (el.classList.contains('crun')) el.innerHTML = `<span>${esc(lbl(METHOD_LABEL[tool] || tool))}</span><b><span class="ag-spin"></span> Running…</b>`;
    else el.innerHTML = '<span class="ag-spin"></span> Running…';
    run(tool, el);
  };
  $('btn-run').addEventListener('click', () => run());
  $('methods').addEventListener('click', (e) => { const b = e.target.closest('.t-run'); if (b) busy(b, b.dataset.tool); });
  $('hero-body').addEventListener('click', (e) => { const b = e.target.closest('.crun'); if (b) busy(b, b.dataset.tool); });
  $('btn-plan').addEventListener('click', (e) => { if ($('btn-plan').getAttribute('aria-disabled') === 'true') e.preventDefault(); });
  window.addEventListener('resize', () => { [traces, qcChart, histChart, aChart].forEach((c) => c && c.resize()); });
  // The QC chart fills its stretched panel, whose height settles after the assumptions render.
  new ResizeObserver(() => { if (qcChart) qcChart.resize(); }).observe($('qc'));

  async function init() {
    const [s, q, d] = await Promise.allSettled([
      get(`/incidents/${INCIDENT_ID}/sensor-series`), get(`/incidents/${INCIDENT_ID}/quantification`), get(`/incidents/${INCIDENT_ID}/detections`),
    ]);
    S = s.status === 'fulfilled' ? s.value : null;
    QD = q.status === 'fulfilled' ? q.value : null;
    const det = d.status === 'fulfilled' ? d.value : null, mon = det && det.monitor;
    alarmMs = det && det.triggered && mon && mon.clip_start_utc && mon.alert_t != null ? Date.parse(mon.clip_start_utc) + mon.alert_t * 1000 : null;
    H = await get(`/incidents/${INCIDENT_ID}/plant-history`).catch(() => null);
    if (typeof echarts === 'undefined') await new Promise((r) => window.addEventListener('load', r, { once: true }));
    // The shell's clock may arrive a moment after the page: wait for it so the traces start on the right time.
    for (let i = 0; i < 20 && nowMs() == null; i++) await wait(100);
    renderAll();
    setInterval(tickTraces, 1000);
    if (pending()) catchUp();
    // While the quantify agent is still working, follow it.
    if (QD && QD.progress) {
      window.AtelicStory.poll(async () => {
        QD = await get(`/incidents/${INCIDENT_ID}/quantification`);
        if (!QD.progress) S = await get(`/incidents/${INCIDENT_ID}/sensor-series`);
        renderAll();
        if (!QD.progress) { shell.refresh(); if (pending()) catchUp(); }
        return QD;
      }, (v) => !!v.progress, 3000);
    }
  }
  init();
})();
