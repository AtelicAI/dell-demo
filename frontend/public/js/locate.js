// Screen 3 — Locate / Source investigation. Reads GET /incidents/:id/graph, /candidates, /detections.
// One shared selection (asset_id) drives the camera ROI, the 2D layout and the candidate panel.
// The full knowledge graph is its own page (graph.html); the link there carries the selected asset.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  const { BADGE, badge } = window.AtelicShell;
  // Provenances a verified camera clip may carry (same rule as Screen 2).
  const CLIP_PROV = new Set(['experimental_setpoint', 'manual_annotation', 'model_estimated', 'simulated', 'synthetic', 'hypothesis', 'pending']);
  // Simulated / synthetic provenance is tracked in the data but not shown.
  const METHOD = { manual_illustrative: 'Manually configured', 'manually configured': 'Manually configured' };
  const MISSING_CAM = 'Camera coverage has not been calibrated.';
  const MAP_DISC = 'Equipment relationships and camera coverage are manually configured.';
  const NO_REL = 'No verified relationship is available for this asset.';

  let PAST = [];
  let G = null, C = null, D = null, byId = new Map(), rank = new Map();
  let selected = null, edgeSel = null, dtab = 'evidence';

  // ----- graph helpers -----
  const edgesOf = (id) => G.edges.filter((e) => e.from === id || e.to === id);
  const roiOf = (id) => (G.camera_rois.regions || []).find((r) => r.asset_id === id);
  const hasPoly = (r) => r && (r.polygon_normalized || []).length > 2;
  const name = (id) => (byId.get(id) || {}).display_name || '';
  const assetLink = (id) => `<button class="linkish" data-sel="${esc(id)}">${esc(id)}</button>`;

  // ----- selection -----
  function select(id) {
    if (!id || !byId.has(id)) return;
    selected = id;
    edgeSel = null;
    $('kg-link').href = `graph.html?${new URLSearchParams({ asset: id, from: 'locate' })}`;
    // Remembered per incident in this tab, so coming back (from the graph, Plan, …) keeps the same asset.
    try { sessionStorage.setItem(`atelic.locate.${window.AtelicShell.INCIDENT_ID}`, id); } catch (e) { /* ignore */ }
    renderLayout();
    renderCands();
    renderDetail();
    renderCam();
    renderEdgeInfo();
  }

  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-sel]');
    if (b) select(b.dataset.sel);
  });

  // ----- 2D equipment layout (SVG in pixel space, so text never stretches) -----
  function shape(t) {
    switch (t) {
      case 'Vessel': return '<rect class="sh" x="-24" y="-14" width="48" height="28" rx="13"/>';
      case 'Valve': return '<path class="sh" d="M-11 -7 L11 7 L11 -7 L-11 7 Z"/>';
      case 'PipeSegment': return '<rect class="sh" x="-17" y="-6" width="34" height="12" rx="6"/>';
      case 'Vent': return '<rect class="sh" x="-6" y="-17" width="12" height="34" rx="3"/>';
      case 'Compressor': return '<circle class="sh" r="16"/><path class="sh" d="M-9 -9 L9 -4 L9 4 L-9 9 Z"/>';
      case 'Flange': return '<rect class="sh" x="-4" y="-13" width="8" height="26" rx="1"/><rect class="sh" x="-12" y="-4" width="24" height="8" rx="2"/>';
      case 'Sensor': return '<circle class="sh" r="9"/>';
      case 'Camera': return '<rect class="sh" x="-12" y="-8" width="20" height="16" rx="3"/><path class="sh" d="M8 -4 L14 -7 L14 7 L8 4 Z"/>';
      default: return '<rect class="sh" x="-10" y="-10" width="20" height="20" rx="4"/>';
    }
  }

  function renderLayout() {
    const box = $('layout');
    if (!G) return;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H) return;
    const pos = G.layout.positions;
    const P = (id) => [36 + pos[id].x / 100 * (W - 72), 30 + pos[id].y / 100 * (H - 66)];
    const shown = G.edges.map((e, i) => ({ ...e, i })).filter((e) =>
      ['CONNECTED_TO', 'MONITORS', 'CAN_OBSERVE'].includes(e.type) && pos[e.from] && pos[e.to]);

    const links = shown.map((e) => {
      const [x1, y1] = P(e.from), [x2, y2] = P(e.to);
      const cls = { CONNECTED_TO: '', MONITORS: 'mon', CAN_OBSERVE: 'obs' }[e.type];
      const hl = (e.from === selected || e.to === selected) && e.type !== 'CAN_OBSERVE' || e.i === edgeSel ? ' hl' : '';
      const d = `M${x1} ${y1} L${(x1 + x2) / 2} ${(y1 + y2) / 2} L${x2} ${y2}`;
      return `<path class="lk ${cls}${hl}" d="${d}"${e.type === 'CONNECTED_TO' ? ' marker-mid="url(#flow)"' : ''}/>
        <path d="${d}" stroke="transparent" stroke-width="12" fill="none" style="cursor:pointer" data-edge="${e.i}"><title>${e.type}: ${e.from} → ${e.to}</title></path>`;
    }).join('');

    const nodes = G.nodes.filter((n) => pos[n.asset_id]).map((n) => {
      const [x, y] = P(n.asset_id), r = rank.get(n.asset_id);
      const cand = r ? `<circle class="cring" r="25"/><text class="rank" x="19" y="-19">#${r}</text>` : '';
      const ly = n.asset_type === 'Vent' ? 30 : 26;
      return `<g class="nd t-${n.asset_type}${n.asset_id === selected ? ' sel' : ''}" transform="translate(${x} ${y})" data-sel="${esc(n.asset_id)}" tabindex="0" role="button" aria-label="${esc(n.asset_id)} ${esc(n.display_name)}">
        <title>${esc(n.asset_id)} | ${esc(n.display_name)}</title>
        <circle class="ring" r="29"/>${cand}${shape(n.asset_type)}
        <text class="tx" y="${ly}" text-anchor="middle">${esc(n.asset_id)}</text>
      </g>`;
    }).join('');

    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Equipment layout">
      <defs><marker id="flow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1 1 L9 5 L1 9 z" fill="${css('--ink-mute')}"/></marker></defs>
      ${links}${nodes}</svg>`;
    box.querySelectorAll('[data-edge]').forEach((p) => p.addEventListener('click', () => { edgeSel = +p.dataset.edge; renderEdgeInfo(); renderLayout(); }));
    box.querySelectorAll('.nd').forEach((g) => g.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(g.dataset.sel); }
    }));
  }

  function renderEdgeInfo() {
    const box = $('edge-info');
    const e = edgeSel != null && G ? G.edges[edgeSel] : null;
    if (!e) { box.innerHTML = '<span class="note">Click a connection to see where it comes from.</span>'; return; }
    box.innerHTML = `<span class="lbl">Source of relationship</span><b>${e.type}</b>
      <span>${assetLink(e.from)} → ${assetLink(e.to)}</span>
      <span>· ${esc(e.source)} · ${esc(METHOD[e.method] || e.method)}${e.mapping_version ? ` · ${esc(e.mapping_version)}` : ''}</span>
      ${badge(e.provenance)}`;
  }

  // ----- camera frame + ROIs -----
  let video = null, frameTime = null, CAM = 'Camera';

  function renderCam() {
    if (!G) return;
    const regions = G.camera_rois.regions || [];
    const drawn = regions.filter(hasPoly);
    $('vis-list').innerHTML = regions.length ? regions.map((r) => `
      <button class="vchip${r.asset_id === selected ? ' sel' : ''}" data-sel="${esc(r.asset_id)}" title="${esc(name(r.asset_id))} · ${hasPoly(r) ? 'ROI drawn' : 'ROI not drawn'}">
        <i class="d${hasPoly(r) ? ' on' : ''}"></i>${esc(r.asset_id)}</button>`).join('') : `<div class="note">${NO_REL}</div>`;
    const sel = roiOf(selected);
    $('vis-sel').innerHTML = !selected ? ''
      : !sel ? `<span class="t"><b>${esc(selected)}</b> is not in the ${esc(CAM)} mapping</span>`
      : `<span class="t" title="${esc(name(selected))}"><b>${esc(selected)}</b> · ${esc(name(selected))}</span>
         <span class="roi-tag${hasPoly(sel) ? ' on' : ''}">${hasPoly(sel) ? 'ROI' : 'No ROI'}</span>`;
    const cav = $('cam-caveat');
    cav.hidden = drawn.length === regions.length && regions.length > 0;
    cav.textContent = MISSING_CAM;

    const eq = (G.camera_equipment || {}).equipment || [];
    $('eq-list').innerHTML = eq.length ? eq.map((e) => `<span class="eqchip${e.kind === 'release_point' ? ' lp' : ''}" title="OWLv2 saw “${esc(e.detector_prompt.replace(/^an? /, ''))}” (score ${e.detector_score}), checked by eye">${esc(e.label)}${e.stands_in_for ? ` <em>→ ${esc(e.stands_in_for)} in the plant model</em>` : ''}</span>`).join('') : '<span class="note">None recognised</span>';
    $('eq-row').hidden = !eq.length;

    const svg = $('cam-svg');
    if (!svg) return;
    // The plume itself is shown by the heat map canvas; the SVG carries equipment ROIs and the recognised equipment.
    svg.innerHTML = `${drawn.map((r) => `<polygon class="roi${r.asset_id === selected ? ' sel' : ''}" points="${r.polygon_normalized.map((p) => p.join(',')).join(' ')}"/>`).join('')}
      ${eq.map((e) => { const [x0, y0, x1, y1] = e.box_normalized; return `<rect class="eqb${e.kind === 'release_point' ? ' lp' : ''}" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/>`; }).join('')}`;
  }

  // The camera view, large: what the camera recognises in its own footage, labelled, over the live replay.
  function bigCam() {
    const eq = (G && G.camera_equipment) || {};
    if (!video || !(eq.equipment || []).length) return;
    const W = 320, H = 240;
    const boxes = eq.equipment.map((e) => {
      const [x0, y0, x1, y1] = e.box_normalized.map((v, i) => v * (i % 2 ? H : W));
      return `<g class="${e.kind === 'release_point' ? 'lp' : ''}"><rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/><text x="${x0 + 1}" y="${Math.max(7, y0 - 2.5)}">${esc(e.label)}</text></g>`;
    }).join('');
    window.AtelicShell.modal({ title: `${CAM} · what the camera recognises`, okLabel: null, cancelLabel: 'Close', html: `<div class="eq-view">
      <div class="eq-big"><video muted autoplay loop playsinline src="${esc(video.currentSrc || video.src)}"></video>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${boxes}</svg></div>
      <p>${esc(eq.scene || '')}${eq.imaging_distance_m ? `, filmed from ${eq.imaging_distance_m} m` : ''}. Boxes are ${esc((eq.model || '').split('/').pop())} open-vocabulary detections on this camera's footage, each checked by eye. The release pipe is where METEC metered the gas out; in the plant model it stands in for XV-2104.</p></div>` });
    const v = document.querySelector('.eq-big video');
    if (v) v.addEventListener('loadedmetadata', () => { v.currentTime = video.currentTime || 0; }, { once: true });
  }

  function initCam(d) {
    const meta = d.metadata, ann = d.annotations;
    CAM = d.camera_id || CAM;
    $('cam-name').textContent = `${CAM} replay`;
    // Same provenance rule as Screen 2: only a verified clip carries its stated provenance.
    const prov = meta.status === 'verified' && CLIP_PROV.has(meta.provenance) ? meta.provenance : meta.status === 'sample' ? 'simulated' : 'pending';
    $('cam-clip').textContent = window.AtelicShell.FACILITY.short;
    if (!d.clip_available) {
      $('cam-badge').innerHTML = badge('pending');
      $('cam-frame').innerHTML = '<div class="cam-empty">Camera clip not loaded</div>';
      return Promise.reject(new Error('Clip not loaded'));
    }
    // First annotated frame of this exact clip (dataset identity).
    const ranges = ann.ogi_clip_id && ann.ogi_clip_id === meta.ogi_clip_id
      ? [...(ann.ranges || []).map((r) => ({ ...r, frame: r.start_frame })), ...(ann.frames || [])].filter((r) => r.bbox_normalized)
      : [];
    const first = ranges.sort((a, b) => a.frame - b.frame)[0];
    D = { box: first ? first.bbox_normalized : null };
    // Plume already colourised in the clip -> no browser heat map on top of it.
    const heat = meta.plume_rendering !== 'burned_in';
    const annProv = ann.provenance === 'model_estimated' ? 'model_estimated' : 'manual_annotation';
    $('cam-badge').innerHTML = badge(prov, 'Clip') + (first && annProv !== prov ? badge(annProv, 'Plume region') : '') + (first && heat ? badge('model_estimated', 'Heat map') : '');

    const times = d.frame_times && d.frame_times.frame_start_s;
    const f = first ? first.frame : 0;
    frameTime = times && times[f] != null ? (times[f] + (times[f + 1] ?? times[f] + 0.05)) / 2 : (meta.frame_rate ? (f + 0.5) / meta.frame_rate : 0);
    $('cam-frame').innerHTML = `
      <video id="cam-video" crossorigin="anonymous" preload="auto" playsinline muted src="${window.AtelicShell.API}/incidents/${window.AtelicShell.INCIDENT_ID}/clip"></video>
      <canvas class="heat" id="cam-heat" aria-hidden="true"></canvas>
      <svg id="cam-svg" viewBox="0 0 1 1" preserveAspectRatio="none"></svg>
      <span class="br tl"></span><span class="br tr"></span><span class="br bl"></span><span class="br bR"></span>
      <div class="cam-chip top"><span>${esc(CAM)} · REPLAY</span></div>
      <div class="cam-chip bot">${first ? '<span><i></i>Plume region · manual</span>' : ''}</div>`;
    const m = Math.floor(frameTime / 60), s = (frameTime % 60).toFixed(2).padStart(5, '0');
    $('cam-time').textContent = `· from ${String(m).padStart(2, '0')}:${s}, looped`;
    video = $('cam-video');
    $('cam-frame').title = 'Open the camera view with the equipment it recognises';
    $('cam-frame').onclick = bigCam;
    renderCam();
    // Replays from the first annotated frame on a loop, with the same heat map as the Camera screen.
    // The heat map is drawn only on frames that an annotation range covers.
    const inRange = (fr) => ranges.some((r) => fr >= r.frame && fr <= (r.end_frame ?? r.frame));
    const frameOf = (t) => {
      if (!times) return meta.frame_rate ? Math.floor(t * meta.frame_rate) : -1;
      let lo = 0, hi = times.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t + 1e-4) lo = mid; else hi = mid - 1; }
      return lo;
    };
    if (heat) {
      const step = window.PlumeHeat.create(video, $('cam-heat'));
      window.PlumeHeat.eachFrame(video, (t) => step(inRange(frameOf(t)) ? D.box : null));
    }
    video.addEventListener('ended', () => { video.currentTime = frameTime; video.play().catch(() => {}); });
    video.addEventListener('seeked', () => video.play().catch(() => {}), { once: true });
    // Resolves when the first annotated frame is really on screen (seeked), so the loader reports the truth.
    return new Promise((ok, fail) => {
      video.addEventListener('loadedmetadata', () => {
        if (video.videoWidth && video.videoHeight) $('cam-frame').style.setProperty('--ar', video.videoWidth / video.videoHeight);
        video.currentTime = frameTime;
      }, { once: true });
      video.addEventListener('seeked', () => ok(`${CAM} · frame ${f}`), { once: true });
      video.addEventListener('error', () => { $('cam-frame').innerHTML = '<div class="cam-empty">Clip could not be played</div>'; fail(new Error('Clip could not be played')); }, { once: true });
      setTimeout(() => fail(new Error('Frame not loaded yet')), 6000);
    });
  }

  // ----- candidates + detail -----
  function assessment() {
    // "Likely source" only when the data names one candidate and every cited ID is valid.
    const a = C && C.assessment;
    if (!a || a.result !== 'likely_source' || !rank.has(a.asset_id) || (C.invalid_ids || []).length) return null;
    return a.asset_id;
  }

  function renderAssess() {
    const prov = C.status === 'sample' ? 'simulated' : C.status === 'verified' ? 'synthetic' : 'pending';
    $('cand-badge').innerHTML = badge(prov) + badge('hypothesis');
    const likely = assessment();
    const bad = (C.invalid_ids || []).length
      ? `<div class="caveat" style="margin-top:8px">Unknown IDs in candidate data: ${C.invalid_ids.map(esc).join(', ')}</div>` : '';
    $('assess').className = `assess${likely ? '' : ' unresolved'}`;
    // Same claim as before ("Likely source: X (unconfirmed)"), laid out as a verdict card.
    $('assess').setAttribute('aria-label', likely ? `Likely source: ${likely} (unconfirmed)` : 'Source unresolved');
    $('assess').innerHTML = likely ? `
      <div class="v-k">Likely source</div>
      <div class="v-row"><span class="v-id">${esc(likely)}</span><span class="pill"><i></i>Unconfirmed · needs field check</span></div>
      <div class="by"><span class="v-nm">${esc(name(likely) || '')}</span>${C.status === 'sample' || !C.method ? '' : ` · ${esc(C.method)}`}</div>${bad}`
      : `<div class="v-k">Source</div><div class="v-id">Unresolved</div>
      <div class="by">${C.status === 'sample' ? '' : esc(C.method || '')}</div>${bad}`;
  }

  function renderCands() {
    if (!C) return;
    const list = C.candidates || [];
    // Meter = count of evidence items in the data (one block each). Not a score or a confidence.
    const meter = (c) => {
      const f = (c.supporting || []).length, a = (c.contradicting || []).length;
      return `<span class="meter" title="${f} supporting · ${a} against (evidence items, not a score)">${'<i class="f"></i>'.repeat(f)}${'<i class="a"></i>'.repeat(a)}</span>
        <span class="ct">${f} for · ${a} against</span>`;
    };
    $('cands').innerHTML = list.length ? `<span class="lbl">Ranked candidates</span>${list.map((c, i) => `
      <button class="cand${c.asset_id === selected ? ' sel' : ''}" data-sel="${esc(c.asset_id)}">
        <span class="rk">${i + 1}</span>
        <span class="nm"><b>${esc(c.asset_id)}</b><span>${esc(name(c.asset_id) || 'Unknown asset')}</span></span>
        <span class="ev">${meter(c)}</span>
      </button>`).join('')}` : '<div class="note" style="padding:6px">No candidates.</div>';
  }

  const li = (mk, cls, html) => `<li><span class="mk ${cls}">${mk}</span><span class="tx">${html}</span></li>`;
  const ev = (e) => `${esc(e.label)}<span class="src">${assetLink(e.source_id)} <span class="pdot"${e.provenance === 'synthetic' || e.provenance === 'simulated' ? '' : ` title="${BADGE[e.provenance] || ''}"`}></span></span>`;
  const list = (items, empty) => items.length ? `<ul>${items.join('')}</ul>` : `<div class="empty">${empty}</div>`;

  function evidenceTab(id) {
    const c = (C && C.candidates || []).find((x) => x.asset_id === id);
    const otherIds = (C && C.candidates || []).filter((x) => x.asset_id !== id);
    const others = otherIds.length
      ? `<div class="chips">${otherIds.map((x) => `<span class="chip">${assetLink(x.asset_id)} ${esc(name(x.asset_id))}</span>`).join('')}</div>`
      : '<div class="empty">None</div>';
    if (!c) {
      return `<h4>${esc(id)}</h4><div class="empty">${C ? 'This asset is not a source candidate.' : 'Candidate data not available.'}</div>
        <h4>Other possible sources</h4>${others}`;
    }
    const group = (cls, mk, title, items, empty) => `<div class="eg ${cls}"><h4><span class="mk">${mk}</span>${title}<span class="n">${items.length}</span></h4>${items.length ? `<ul>${items.join('')}</ul>` : `<div class="empty">${empty}</div>`}</div>`;
    const item = (html) => `<li>${html}</li>`;
    return `${(c.rationale || []).length ? `<p class="why">${c.rationale.map(esc).join(' ')}</p>` : ''}
      ${group('for', '✓', 'Why this component?', (c.supporting || []).map((e) => item(ev(e))), 'No supporting evidence.')}
      ${group('against', '✕', 'Evidence against', (c.contradicting || []).map((e) => item(ev(e))), 'None recorded.')}
      ${group('miss', '?', 'Missing information', (c.missing || []).map((m) => item(esc(m))), 'None recorded.')}
      <h4 class="oth">Other possible sources</h4>${others}`;
  }

  function kv(rows) {
    return `<div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v == null ? '<span style="color:var(--ink-dim)">Not available</span>' : `<span class="pdot"></span>${esc(v)}`}</div>`).join('')}</div>`;
  }

  function connectionsTab(id) {
    const n = byId.get(id), es = edgesOf(id);
    const pipe = es.filter((e) => e.type === 'CONNECTED_TO').map((e) => e.to === id
      ? li('↑', 'miss', `Upstream: ${assetLink(e.from)} ${esc(name(e.from))}`)
      : li('↓', 'miss', `Downstream: ${assetLink(e.to)} ${esc(name(e.to))}`));
    const cams = es.filter((e) => e.type === 'CAN_OBSERVE').map((e) =>
      li('◎', 'miss', `${assetLink(e.from)} can observe ${assetLink(e.to)} · ${esc(METHOD[e.method] || e.method)} · ${hasPoly(roiOf(e.to)) ? 'ROI drawn' : 'ROI not drawn'}`));
    const camNote = cams.length && es.some((e) => e.type === 'CAN_OBSERVE' && !hasPoly(roiOf(e.to))) ? `<div class="empty">${MISSING_CAM}</div>` : '';
    const meas = es.filter((e) => e.type === 'MONITORS').map((e) => {
      const s = byId.get(e.from);
      return li('~', 'miss', e.from === id
        ? `Monitors ${assetLink(e.to)} · ${esc(s.measures || '')}`
        : `${assetLink(e.from)} ${esc(s.display_name)} · ${esc(s.measures || '')}`);
    });
    const rel = es.map((e) => li('·', 'miss', `<b class="id">${e.type}</b> ${esc(e.from)} → ${esc(e.to)}<span class="src">${esc(e.source)} · ${esc(METHOD[e.method] || e.method)}</span>`));
    return `<h4>Asset details</h4>${kv([['Asset ID', n.asset_id], ['Name', n.display_name], ['Type', n.asset_type], ['Source', n.source], ['Mapping version', n.mapping_version]])}
      <h4>Connected equipment</h4>${list(pipe, NO_REL)}
      <h4>Camera visibility</h4>${list(cams, 'No camera mapping covers this asset.')}${camNote}
      <h4>Measurements</h4>${list(meas, 'No sensor is linked to this asset.')}${meas.length ? '<div class="empty">Readings are reviewed in the Quantify stage.</div>' : ''}
      <h4>Source of relationship</h4>${list(rel, NO_REL)}`;
  }

  function maintenanceTab(id) {
    const n = byId.get(id);
    const ids = n.asset_type === 'WorkOrder' ? [id] : edgesOf(id).filter((e) => e.type === 'HAS_WORK_ORDER').map((e) => e.to);
    const recs = ids.map((w) => byId.get(w)).map((w) => li('▤', 'miss',
      `<b class="id">${esc(w.asset_id)}</b> · ${esc(w.date)} · ${esc(w.work_type)} · ${esc(w.work_status)}<span class="src" style="color:var(--ink-mute)">${esc(w.display_name)}</span><span class="src">${esc(w.source)} <span class="pdot"></span></span>`));
    // Closed incidents on this asset (GET /incidents history), newest first.
    const past = PAST.filter((h) => h.asset_id === id).map((h) => li('◷', 'miss',
      `<a class="id" href="incidents.html?id=${encodeURIComponent(h.incident_id)}">${esc(h.incident_id)}</a> · ${esc(h.opened_utc.slice(0, 10))} · ${esc(h.outcome_label)}<span class="src" style="color:var(--ink-mute)">${esc(h.summary)}</span>`));
    // Field reports and work-order commitments from the knowledge graph: what the crew saw and what was promised.
    const reports = G.edges.filter((e) => e.type === 'ON_ASSET' && e.to === id && (byId.get(e.from) || {}).asset_type === 'FieldReport')
      .map((e) => byId.get(e.from)).sort((a, b) => String(a.date).localeCompare(String(b.date))).map((r) => li('✎', 'miss',
        `<b class="id">${esc(r.asset_id)}</b> · ${esc(r.date || '')} · ${esc(r.author_role || '')}<span class="src" style="color:var(--ink-mute)">${esc(r.display_name)}</span>${r.excerpt ? `<span class="src">${esc(r.excerpt.slice(0, 220))}…</span>` : ''}`));
    const promises = G.edges.filter((e) => e.type === 'COMMITTED_TO' && ids.includes(e.from)).map((e) => ({ wo: e.from, c: byId.get(e.to) }))
      .filter((x) => x.c).map(({ wo, c }) => li(c.work_status === 'done' ? '✓' : '!', c.work_status === 'done' ? 'for' : 'against',
        `<b class="id">${esc(c.asset_id)}</b> · from ${esc(wo)} · <b>${esc((c.work_status || '').replace(/_/g, ' '))}</b><span class="src" style="color:var(--ink-mute)">${esc(c.display_name)}</span>`));
    return `<h4>Related documents</h4>${list(recs, 'No maintenance records for this asset.')}
      <h4>Commitments from those work orders</h4>${list(promises, 'No follow-up commitments recorded.')}
      <h4>Field reports</h4>${list(reports, 'No field reports for this asset.')}
      <h4>Previous incidents</h4>${list(past, 'No earlier incidents on this asset.')}`;
  }

  function mappingTab() {
    const regions = G.camera_rois.regions || [];
    return `<h4>Mapping assumptions</h4><div class="caveat">${MAP_DISC}</div>
      ${regions.some((r) => !hasPoly(r)) ? `<div class="caveat" style="margin-top:8px">${MISSING_CAM}</div>` : ''}
      <h4>Versions</h4>${kv([
        ['Graph store', G.store === 'json' ? 'JSON files (Neo4j not connected yet)' : G.store],
        ['Site mapping', (G.nodes[0] || {}).mapping_version],
        ['2D layout', G.layout.mapping_version],
        ['Camera mapping', G.camera_rois.mapping_version],
        ['Mapping method', METHOD[G.camera_rois.mapping_method] || G.camera_rois.mapping_method],
        ['ROI polygons', `${regions.filter(hasPoly).length} of ${regions.length} drawn`],
        ['Candidate ranking', C ? C.method : null],
      ])}`;
  }

  function renderDetail() {
    document.querySelectorAll('.dtabs .tab').forEach((t) => {
      const on = t.dataset.t === dtab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
    });
    const el = $('detail');
    if (!G) { el.innerHTML = '<div class="empty">Knowledge graph data not available.</div>'; return; }
    if (dtab === 'mapping') { el.innerHTML = mappingTab(); return; }
    if (!selected) { el.innerHTML = '<div class="empty">Select an asset.</div>'; return; }
    el.innerHTML = { evidence: evidenceTab, connections: connectionsTab, maintenance: maintenanceTab }[dtab](selected);
  }

  // ----- tabs -----
  function tabKeys(group, activate) {
    group.addEventListener('keydown', (ev) => {
      if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
      const tabs = [...group.querySelectorAll('[role=tab]')];
      const i = tabs.indexOf(document.activeElement);
      if (i < 0) return;
      const next = tabs[(i + (ev.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      activate(next);
    });
  }

  function wire() {
    const dt = (t) => { dtab = t.dataset.t; renderDetail(); };
    document.querySelectorAll('.dtabs .tab').forEach((t) => t.addEventListener('click', () => dt(t)));
    tabKeys(document.querySelector('.dtabs'), dt);
    new ResizeObserver(() => renderLayout()).observe($('layout'));
  }

  async function init() {
    wire();
    const { get, INCIDENT_ID } = window.AtelicShell;
    // Closed incidents for the Maintenance tab; loaded alongside, not waited for.
    get('/incidents').then((d) => { PAST = d.history ? d.history.incidents : []; if (G) renderDetail(); }, () => {});
    const graphP = step('graph', get(`/incidents/${INCIDENT_ID}/graph`),
      (v) => `${v.nodes.length} assets · ${v.edges.length} relationships · ${v.store === 'json' ? 'JSON' : v.store}`);
    const candP = step('cands', get(`/incidents/${INCIDENT_ID}/candidates`),
      (v) => `${(v.candidates || []).length} candidates`);
    const [g, c, d] = await Promise.allSettled([graphP, candP, get(`/incidents/${INCIDENT_ID}/detections`)]);

    if (g.status === 'fulfilled') {
      G = g.value;
      byId = new Map(G.nodes.map((n) => [n.asset_id, n]));
      $('graph-badge').innerHTML = badge(G.nodes.some((n) => n.provenance !== 'synthetic') ? 'pending' : 'synthetic', 'Site data');
    } else {
      const msg = '<div class="view-empty">Knowledge graph data not available.</div>';
      $('layout').innerHTML = msg;
      $('vis-list').innerHTML = `<div class="note">${NO_REL}</div>`;
      $('graph-badge').innerHTML = badge('pending');
    }

    if (c.status === 'fulfilled') {
      C = c.value;
      (C.candidates || []).forEach((x, i) => rank.set(x.asset_id, i + 1));
      renderAssess();
    } else {
      $('cand-badge').innerHTML = badge('pending');
      $('assess').className = 'assess unresolved';
      $('assess').innerHTML = '<div class="big"><span class="sig"></span>Source unresolved</div><div class="by">Candidate data not available.</div>';
    }

    let camP;
    if (d.status === 'fulfilled') camP = initCam(d.value);
    else {
      $('cam-badge').innerHTML = badge('pending');
      $('cam-frame').innerHTML = '<div class="cam-empty">Camera data not available</div>';
      camP = Promise.reject(new Error('Camera data not available'));
    }

    let kept = new URLSearchParams(location.search).get('asset');
    try { kept = kept || sessionStorage.getItem(`atelic.locate.${INCIDENT_ID}`); } catch (e) { /* ignore */ }
    const start = (kept && byId.has(kept) && kept) || assessment() || (C && C.candidates && C.candidates[0] && C.candidates[0].asset_id);
    if (G && start && byId.has(start)) select(start);
    else { renderLayout(); renderCands(); renderDetail(); renderCam(); }

    await Promise.allSettled([step('cam', camP, (v) => v)]);
    // Reveal the panels as soon as the data is in (no extra hold).
    $('loc-grid').classList.add('ready');
    $('loader').classList.add('done');
    renderLayout();
  }

  // Loader row: spinner until the real request settles, then what it returned (or why it failed).
  function step(key, promise, summary) {
    const row = document.querySelector(`#loader li[data-k="${key}"]`);
    return promise.then((v) => {
      row.classList.add('ok');
      row.querySelector('.rs').textContent = summary(v);
      return v;
    }, (e) => {
      row.classList.add('fail');
      row.querySelector('.rs').textContent = /^\/.*→/.test(e.message) ? 'Not available' : e.message;
      throw e;
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
