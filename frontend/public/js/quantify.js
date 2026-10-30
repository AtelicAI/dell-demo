// Screen 4 — Quantify / Emissions. Reads GET /incidents/:id/sensor-series and /quantification;
// "Run estimate" calls POST /incidents/:id/quantify. No number is shown without a backend result.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const { API, INCIDENT_ID, get } = window.AtelicShell;

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
  const ROLE = { primary: 'Selected', cross_check: 'Cross-check', waiting: 'Waiting', not_needed: 'Not needed here', blocked: 'Cannot run', recommended: 'Would fit', pending: 'Pending' };
  const AXIS_H = 28;
  const MONO = 'IBM Plex Mono, monospace';
  const METHOD_LABEL = {};
  const hhmm = (t) => (t ? t.slice(11, 16) : '—');
  const hhmmss = (t) => (t ? t.slice(11, 19) : '—');

  let S = null, Q = null, traces = null, qcChart = null, H = null, histChart = null, aChart = null;

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
        `<div class="hero-empty"><span class="ag-spin"></span> ${esc(Q.progress.text)}. Every 10 s of replay the agent reads the case (wind, stability, sensors, camera), picks the tool built for it and tries again until that tool has a steady window. No number is shown before that.</div>`;
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
    title.textContent = 'Emission-rate estimate';
    $('res-badges').innerHTML = badge(r.provenance, `Produced by ${r.model_version}`);
    const qc = r.qc || {}, cam = r.primary_tool === 'qogi';
    const cks = r.cross_checks || [], ok = cks.filter((c) => c.agrees);
    const ccLine = cks.length ? `<div class="sub cc ${ok.length === cks.length ? 'ok' : 'no'}">${ok.length} of ${cks.length} sensor-model checks agree with ${cam ? 'the camera' : 'the primary tool'}
      (${Math.min(...cks.map((c) => c.rate_kg_h)).toFixed(2)}–${Math.max(...cks.map((c) => c.rate_kg_h)).toFixed(2)} kg/h)${ok.length < cks.length ? `; ${cks.filter((c) => !c.agrees).map((c) => esc(c.label)).join(', ')} disagree` : ''}</div>` : '';
    body.innerHTML = rateBlock(`<div class="num">≈ ${r.rate_kg_ch4_h.toFixed(2)}</div>`,
      `<div class="unit">kg CH₄/h · ${esc(METHOD_LABEL[r.primary_tool] || r.primary_tool)}</div><div class="sub">${r.attempts ? `Frozen at replay ${r.as_of_seconds} s after ${r.attempts.length} agent attempts · ${qc.samples_used} ${cam ? 'camera frames' : 'readings'}` : `Median of ${qc.samples_used} per-sample estimates`}</div>${ccLine}`) + gauge(r);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const b = body.querySelector('.band');
      if (b) b.classList.remove('pre');
    }));

    const w = r.data_window || {}, iv = r.interval;
    const f = (k, v, sub) => `<div class="f"><span class="lbl">${k}</span><div class="v">${v}${sub ? `<small>${sub}</small>` : ''}</div></div>`;
    const case_ = r.case || {};
    fields.innerHTML =
      f('Uncertainty interval', iv ? `${iv.low.toFixed(2)} – ${iv.high.toFixed(2)}` : 'Not computed', iv ? `kg/h · ${esc(iv.basis)}` : '') +
      f('Tool chosen by the agent', esc(METHOD_LABEL[r.primary_tool] || r.primary_tool), `Source: ${esc(r.source_asset_id)} (hypothesis)`) +
      f('Data window', cam && r.camera ? `clip ${r.camera.window_clip_seconds.join('–')} s` : `${hhmmss(w.start_utc)}–${hhmmss(w.end_utc)}`,
        cam ? (r.camera && r.camera.latest_window_held ? 'Latest 90 s camera window the plume was tracked' : '90 s of camera frames') : 'UTC · 60 s of sensor readings') +
      f('Model version', esc(r.model_version), esc(r.model_note || '')) +
      f('Case read by the agent', case_.wind !== 'available' ? 'Weather mast down' : `${esc((case_.stability || '').replace('_', ' '))} air · ${case_.methane_sensors_reporting} sensors`,
        case_.wind !== 'available' ? 'Camera sees the plume' : `${case_.camera_candidates} camera candidates · wind ${case_.wind_speed_m_s} m/s${cam ? ' · camera sees the plume' : ''}`) +
      f('Assumptions', `<a href="#assumptions-panel">${(r.assumptions || []).length} listed</a>`, 'Geometry is draft');
    fields.hidden = false;
  }

  const bandName = (iv) => (/frame/.test(iv.basis) ? 'Frame-to-frame spread' : /sensitivity/.test(iv.basis) ? 'Assumption sensitivity' : 'P10–P90 interval');

  // Each connected tool's number: the engineer's run if there is one, else the agent's.
  const toolRate = (m) => (m.engineer_run && m.engineer_run.rate_kg_h != null ? m.engineer_run.rate_kg_h : m.output && m.output.rate_kg_h != null ? m.output.rate_kg_h : null);

  function gauge(r) {
    const rates = (r.samples || []).filter((s) => s.used).map((s) => s.rate_kg_ch4_h);
    const ref = Q.release_check;
    const tools = ((Q && Q.methods) || []).filter((m) => m.connected);
    const top = Math.max(...rates, ...tools.map(toolRate).filter((v) => v != null), r.interval ? r.interval.high : 0, ref ? ref.rate_kg_ch4_h : 0) * 1.1;
    const step = top > 8 ? 2 : top > 4 ? 1 : 0.5;
    const max = Math.ceil(top / step) * step;
    const pct = (v) => `${(Math.min(v, max) / max) * 100}%`;
    const ticks = [];
    for (let v = 0; v <= max + 1e-9; v += step) ticks.push(`<div class="g-tick" style="left:${pct(v)}"><span>${+v.toFixed(1)}</span></div>`);
    const dots = rates.map((v, i) => `<div class="g-dot" style="left:${pct(v)};top:${14 + (i % 4) * 6}px" title="${v.toFixed(2)} kg/h"></div>`).join('');
    const iv = r.interval;
    const band = iv ? `<div class="band pre" style="left:${pct(iv.low)};width:calc(${pct(iv.high)} - ${pct(iv.low)})"></div>` : '';
    const refEl = ref ? `<div class="ref" style="left:${pct(ref.rate_kg_ch4_h)}" title="Reference release rate"></div>` : '';
    // One row per tool on the same scale; a row without a number runs that tool when clicked.
    const rows = tools.map((m) => {
      const v = toolRate(m), primary = m.id === r.primary_tool, mine = m.engineer_run && m.engineer_run.rate_kg_h != null;
      const why = m.engineer_run && m.engineer_run.rate_kg_h == null ? (m.engineer_run.reason || '').replace(/_/g, ' ') : m.output && m.output.rate_kg_h == null && m.output.reason ? m.output.reason.replace(/_/g, ' ') : '';
      return `<button class="trow${primary ? ' prim' : ''}${v == null ? ' empty' : ''}" data-tool="${esc(m.id)}" title="${v == null ? 'Run this tool on the frozen window' : 'Run again'}">
        <span class="tn">${esc(m.label.replace(/ \(.*\)$/, ''))}</span>
        <span class="tt">${v != null ? `<i class="tdot${mine ? ' mine' : ''}" style="left:${pct(v)}"></i>` : `<em>${why ? esc(why) : 'Not run · click to run'}</em>`}${ref ? `<i class="tref" style="left:${pct(ref.rate_kg_ch4_h)}"></i>` : ''}</span>
        <span class="tv">${v != null ? v.toFixed(3) : '—'}</span></button>`;
    }).join('');
    const axis = `<div class="taxis"><span></span><span class="tt">${ticks.map((t) => t.replace('g-tick', 'g-tick ta')).join('')}</span><span class="tv">kg/h</span></div>`;
    return `<div class="tstrip" aria-label="Rate from each tool, kg/h">${rows}${axis}</div><div class="gauge" aria-label="Per-sample estimates on a kg/h scale">
      <div class="g-track"><div class="g-axis"></div>${ticks.join('')}${band}${dots}${refEl}
        <div class="med" style="left:${pct(r.rate_kg_ch4_h)}" title="Median ${r.rate_kg_ch4_h} kg/h"></div></div>
      <div class="g-legend">
        <span><i class="lm"></i>Median</span>
        ${iv ? `<span><i class="lb"></i>${bandName(iv)}</span>` : ''}
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
    $('series-meta').textContent = `${hhmm(S.site.replay_start_utc)}–${hhmm(S.site.replay_end_utc)} UTC · ${S.interval_label || '30 s samples'}`;
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
      <div class="kv">${(qc.rows || []).map(([k, v]) => row(k, esc(v))).join('')}</div>
      ${r.interval ? `<div class="qc-chart-h"><b>${r.attempts ? 'Agent attempts' : 'Per-sample estimates'}</b><span>kg CH₄/h · band = ${bandName(r.interval).toLowerCase()} · line = frozen estimate</span></div><div id="qc-chart"></div>` : ''}
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

  // ----- agent tools + inputs -----
  // Every rate tool with the agent's decision for this incident. Connected tools can be run on the frozen window.
  function renderMethods() {
    const ms = (Q && Q.methods) || [];
    ms.forEach((m) => { METHOD_LABEL[m.id] = m.label; });
    const frozen = Q && Q.result && Q.result.tools && Q.result.tools.length;
    const probs = (o) => (o && o.sources ? `<span class="t-why">Leak probability: ${o.sources.slice(0, 3).map((x) => `${esc(x.asset_id)} ${x.leak_probability.toFixed(2)}`).join(' · ')}</span>` : '');
    const out = (m) => (m.output && m.output.rate_kg_h != null ? `<span class="t-out">Agent run: ${m.output.rate_kg_h.toFixed(3)} kg/h</span>${probs(m.output)}` : probs(m.output));
    const mine = (m) => {
      const e = m.engineer_run;
      if (!e) return '';
      return `<span class="t-out mine">Your run: ${e.rate_kg_h != null ? `${e.rate_kg_h.toFixed(3)} kg/h${e.ratio_to_frozen ? ` (${e.ratio_to_frozen.toFixed(2)}× the frozen rate)` : ''}` : esc((e.reason || e.status).replace(/_/g, ' '))}</span>`;
    };
    $('methods').innerHTML = ms.map((m) => `<div class="tool r-${esc(m.role)}">
        <div class="t-head"><b>${esc(m.label)}</b><span class="t-role">${esc(ROLE[m.role] || m.role)}</span></div>
        <span class="t-why">${esc(m.reason)}</span>${out(m)}${mine(m)}
        ${m.connected ? (frozen ? `<button class="t-run" data-tool="${esc(m.id)}">Run ${esc(m.label.split(' (')[0])}</button>` : '')
          : '<span class="t-na">Not deployed in this demo</span>'}
      </div>`).join('') || '<div class="note">No tools reported by the backend.</div>';
    const ran = Q && Q.result && Q.result.tools && Q.result.tools.length;
    $('btn-run').disabled = !ran;
    $('btn-run').textContent = "Re-run the agent's choice";
    const ready = Q && Q.result && Q.result.rate_kg_ch4_h != null;
    $('next-note').textContent = ready ? 'Estimate ready for engineer review.' : Q && Q.progress ? 'The quantify agent is still working.' : 'No rate: the plan proceeds without one.';
    $('btn-plan').setAttribute('aria-disabled', String(!ready && !(Q && Q.result)));
    renderLog();
  }

  // What the agent did on each 10 s step of the replay clock: so the wait is visible, not a static refusal.
  function renderLog() {
    const r = Q && Q.result, att = (r && r.attempts) || [];
    $('agent-log').innerHTML = att.length
      ? `<ol class="alog">${att.map((a) => `<li class="${a.status === 'estimated' ? 'ok' : ''}"><span class="at">${hhmmss(a.t)}</span><span>${esc(a.text || a.status)}</span></li>`).join('')}</ol>`
      : `<div class="pending-box">${Q && Q.progress ? esc(Q.progress.text) : 'Starts after Locate.'}</div>`;
    $('log-meta').textContent = att.length ? `${att.length} steps` : '';
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
    items.push(li('Geometry', 'Facility positions + heights', badge('synthetic', 'engine facility model (Neo4j)')));
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
      <div class="cond">${c.rows.map((x) => `<div class="${x.ok ? 'ok' : 'no'}"><b>${esc(x.label)}</b><span>${esc(x.detail)}</span></div>`).join('')}</div>
      <p class="hist-note">${esc(c.note)}</p>`;
  }

  // Which tool fits which case, and what the agent decided for this one.
  function renderCatalog() {
    const C = Q && Q.catalog;
    $('meth-panel').hidden = !C;
    if (!C) return;
    const mine = Object.fromEntries(((Q && Q.methods) || []).map((m) => [m.id, m]));
    $('meth-meta').textContent = `${C.methods.length} tools · ${C.methods.filter((m) => m.connected).length} run in this demo`;
    $('meth').innerHTML = `<p class="mhead"><b>${esc(C.headline)}</b></p>
      <table class="tmatrix"><tr><th>Tool</th><th>Built for</th><th>Needs</th><th>This incident</th></tr>
      ${C.methods.map((m) => { const d = mine[m.id] || {}; return `<tr class="r-${esc(d.role || 'pending')}">
        <td><b>${esc(m.label)}</b><small>${esc(m.family)}</small></td><td>${esc(m.best_when)}</td>
        <td>${m.needs.map(esc).join(' · ')}</td>
        <td><span class="t-role">${esc(ROLE[d.role] || 'Pending')}</span> ${esc(d.reason || '')}</td></tr>`; }).join('')}</table>
      <details class="how"><summary>How each connected tool works</summary>
        ${C.methods.filter((m) => m.connected).map((m) => `<p><b>${esc(m.label)}.</b> ${esc(m.how)}</p>`).join('')}${HOW_SVG}</details>`;
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
    $('hist-note').textContent = `${H.note || ''} Episodes are found from public sensor data only; 5-minute fits narrow the area but give no rate.`;
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
    renderMethods(); renderHero(); renderInputs(); renderTraces(); renderLower(); renderConditions(); renderAttr(); renderCatalog(); renderHistory();
  }

  async function run(tool) {
    const btn = $('btn-run'), msg = $('run-msg');
    btn.disabled = true;
    msg.className = 'run-msg'; msg.textContent = `Running ${tool ? METHOD_LABEL[tool] || tool : "the agent's choice"} on the frozen window…`;
    try {
      const r = await fetch(`${API}/incidents/${INCIDENT_ID}/quantify`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method: tool || 'agent' }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error((body.detail && body.detail.failure) || body.detail || r.status);
      Q = body;
      const label = tool ? METHOD_LABEL[tool] || tool : null;
      msg.textContent = label ? `Ran ${label} on the frozen window; the frozen rate is unchanged` : 'Re-ran the same tools at the frozen clock: same inputs, same result';
      renderAll();
      window.AtelicShell.refresh();
    } catch (e) {
      msg.className = 'run-msg err'; msg.textContent = `Estimate failed: ${e.message}`;
      btn.disabled = false;
    }
  }

  $('btn-run').addEventListener('click', () => run());
  // Any connected tool can be run on the frozen window; the result shows next to the agent's.
  $('methods').addEventListener('click', (e) => { const b = e.target.closest('.t-run'); if (b) { b.disabled = true; run(b.dataset.tool); } });
  $('hero-body').addEventListener('click', (e) => {
    const b = e.target.closest('.trow');
    if (!b || b.disabled) return;
    b.disabled = true; b.classList.add('busy');
    b.querySelector('.tt').innerHTML = '<em><span class="ag-spin"></span> Running…</em>';
    b.querySelector('.tv').textContent = '…';
    run(b.dataset.tool);
  });
  $('btn-plan').addEventListener('click', (e) => { if ($('btn-plan').getAttribute('aria-disabled') === 'true') e.preventDefault(); });
  window.addEventListener('resize', () => { [traces, qcChart, histChart, aChart].forEach((c) => c && c.resize()); });
  // The QC chart fills its stretched panel, whose height settles after the assumptions render.
  new ResizeObserver(() => { if (qcChart) qcChart.resize(); }).observe($('qc'));

  async function init() {
    const [s, q] = await Promise.allSettled([
      get(`/incidents/${INCIDENT_ID}/sensor-series`), get(`/incidents/${INCIDENT_ID}/quantification`),
    ]);
    S = s.status === 'fulfilled' ? s.value : null;
    Q = q.status === 'fulfilled' ? q.value : null;
    H = await get(`/incidents/${INCIDENT_ID}/plant-history`).catch(() => null);
    if (typeof echarts === 'undefined') await new Promise((r) => window.addEventListener('load', r, { once: true }));
    renderAll();
    // While the quantify agent is still stepping the replay clock, follow it.
    if (Q && Q.progress) {
      window.AtelicStory.poll(async () => {
        Q = await get(`/incidents/${INCIDENT_ID}/quantification`);
        if (!Q.progress) S = await get(`/incidents/${INCIDENT_ID}/sensor-series`);
        renderAll();
        if (!Q.progress) window.AtelicShell.refresh();
        return Q;
      }, (v) => !!v.progress, 3000);
    }
  }
  init();
})();
