// Screen 3 — Locate / Source investigation. Reads GET /incidents/:id/graph, /candidates, /detections.
// One shared selection (asset_id) drives the camera ROI, the 2D layout, the graph and the candidate panel.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  const BADGE = {
    simulated: 'Simulated', synthetic: 'Simulated', manual_annotation: 'Manual annotation',
    experimental_setpoint: 'Experimental setpoint', hypothesis: 'Hypothesis', pending: 'Pending',
  };
  const badge = (p, title) => `<span class="badge ${p}"${title ? ` title="${esc(title)}"` : ''}>${BADGE[p] || 'Pending'}</span>`;
  const METHOD = { manual_illustrative: 'Manually configured (illustrative)', 'manually configured': 'Manually configured' };
  const TYPE_COLOR = {
    Vessel: '--n-station', Valve: '--n-equip', Vent: '--n-well', PipeSegment: '--ink-dim',
    Sensor: '--n-tag', Camera: '--n-wt', EquipmentArea: '--ink-mute', WorkOrder: '--n-sap',
  };
  const EDGE_COLOR = {
    CONNECTED_TO: '--ink-mute', MONITORS: '--pink', CAN_OBSERVE: '--good', CONTAINS: '--ink-faint', HAS_WORK_ORDER: '--n-sap',
  };
  const MISSING_CAM = 'Camera coverage has not been calibrated. Equipment attribution is illustrative.';
  const MAP_DISC = 'Equipment relationships and camera coverage are manually configured.';
  const NO_REL = 'No verified relationship is available for this asset.';

  let G = null, C = null, D = null, byId = new Map(), rank = new Map();
  let selected = null, edgeSel = null, dtab = 'evidence', chart = null;
  const expand = { up: false, down: false, sensors: false };

  // ----- graph helpers -----
  const edgesOf = (id) => G.edges.filter((e) => e.from === id || e.to === id);
  const roiOf = (id) => (G.camera_rois.regions || []).find((r) => r.asset_id === id);
  const hasPoly = (r) => r && (r.polygon_normalized || []).length > 2;
  const name = (id) => (byId.get(id) || {}).display_name || '';
  const assetLink = (id) => `<button class="linkish" data-sel="${esc(id)}">${esc(id)}</button>`;

  function walk(id, dir) {
    // Transitive CONNECTED_TO in one flow direction.
    const out = new Set(), stack = [id];
    while (stack.length) {
      const cur = stack.pop();
      G.edges.forEach((e) => {
        if (e.type !== 'CONNECTED_TO') return;
        const next = dir === 'up' ? (e.to === cur && e.from) : (e.from === cur && e.to);
        if (next && !out.has(next)) { out.add(next); stack.push(next); }
      });
    }
    return out;
  }

  const showEdge = (e) => e.type !== 'CONTAINS' || e.from === selected;

  function graphSet() {
    const s = new Set([selected]);
    // The area node links to every asset; only show those links when the area itself is selected.
    edgesOf(selected).filter(showEdge).forEach((e) => { s.add(e.from); s.add(e.to); });
    if (expand.up) walk(selected, 'up').forEach((x) => s.add(x));
    if (expand.down) walk(selected, 'down').forEach((x) => s.add(x));
    if (expand.sensors) G.edges.forEach((e) => { if (e.type === 'MONITORS' && s.has(e.to)) s.add(e.from); });
    return s;
  }

  // ----- selection -----
  function select(id) {
    if (!id || !byId.has(id)) return;
    selected = id;
    edgeSel = null;
    renderLayout();
    renderGraph();
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

    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Representative skid equipment layout">
      <defs><marker id="flow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1 1 L9 5 L1 9 z" fill="${css('--ink-mute')}"/></marker></defs>
      ${links}${nodes}</svg>`;
    box.querySelectorAll('[data-edge]').forEach((p) => p.addEventListener('click', () => { edgeSel = +p.dataset.edge; renderEdgeInfo(); renderLayout(); }));
    box.querySelectorAll('.nd').forEach((g) => g.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(g.dataset.sel); }
    }));
  }

  // ----- knowledge graph (ECharts, fixed reading layout) -----
  // Not a force layout: gas flows left → right through the selected asset, sensors sit above what
  // they monitor, work orders below their asset, the camera along the bottom.
  const EQUIP = new Set(['Vessel', 'Valve', 'PipeSegment', 'Vent']);
  const EDGE_WORDS = { CONNECTED_TO: 'flows to', MONITORS: 'monitors', CAN_OBSERVE: 'can observe', HAS_WORK_ORDER: 'work order', CONTAINS: 'contains' };
  const typeOf = (id) => (byId.get(id) || {}).asset_type;

  function placeGraph(vis) {
    const at = new Map();
    const eq = [...vis].filter((id) => EQUIP.has(typeOf(id)));
    const pipes = G.edges.filter((e) => e.type === 'CONNECTED_TO' && vis.has(e.from) && vis.has(e.to));
    // Columns by flow distance from an anchor; each unconnected group gets its own band below.
    const col = new Map();
    const touch = (id) => edgesOf(selected).some((e) => e.from === id || e.to === id);
    const order = [EQUIP.has(typeOf(selected)) ? selected : eq.find(touch), ...eq].filter(Boolean);
    let band = 0;
    const bandOf = new Map();
    order.forEach((start) => {
      if (col.has(start)) return;
      col.set(start, 0); bandOf.set(start, band);
      const q = [start];
      while (q.length) {
        const cur = q.shift();
        pipes.forEach((e) => {
          const [nb, d] = e.from === cur ? [e.to, 1] : e.to === cur ? [e.from, -1] : [];
          if (nb && !col.has(nb)) { col.set(nb, col.get(cur) + d); bandOf.set(nb, band); q.push(nb); }
        });
      }
      band++;
    });
    // Stack nodes that share a column and band, centred on the band line.
    const groups = new Map();
    eq.forEach((id) => { const k = `${bandOf.get(id)}|${col.get(id)}`; groups.set(k, [...(groups.get(k) || []), id]); });
    let y0 = 0;
    for (let b = 0; b < band; b++) {
      const keys = [...groups.keys()].filter((k) => +k.split('|')[0] === b);
      const tall = Math.max(1, ...keys.map((k) => groups.get(k).length));
      keys.forEach((k) => groups.get(k).forEach((id, i) => at.set(id, [col.get(id), y0 + (i - (groups.get(k).length - 1) / 2) * 1.1])));
      y0 += tall * 1.1 + 1.6;
    }
    const xs = [...at.values()].map((p) => p[0]), ys = [...at.values()].map((p) => p[1]);
    const top = ys.length ? Math.min(...ys) : 0, bottom = ys.length ? Math.max(...ys) : 0;
    const cx = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 0;
    if (!at.size && vis.has(selected)) at.set(selected, [0, 0]);

    // Satellites: sensors above, work orders below, camera at the bottom, area on top.
    const used = new Set([...at.values()].map((p) => p.join()));
    const free = (x, y, dy) => { while (used.has(`${x},${y}`)) x += 0.55; used.add(`${x},${y}`); return [x, y]; };
    [...vis].filter((id) => !at.has(id)).forEach((id) => {
      const t = typeOf(id), e = G.edges.find((x) => (x.from === id && at.has(x.to)) || (x.to === id && at.has(x.from)));
      const host = e ? at.get(e.from === id ? e.to : e.from) : [cx, bottom];
      if (t === 'Sensor') at.set(id, free(host[0], Math.min(host[1], top) - 1.4));
      else if (t === 'WorkOrder') at.set(id, free(host[0], Math.max(host[1], bottom) + 1.7));
      else if (t === 'Camera') at.set(id, free(cx, bottom + 2.1));
      else if (t === 'EquipmentArea') at.set(id, free(cx, top - 2.1));
      else at.set(id, free(host[0] + 0.5, host[1] + 1.1));
    });
    return at;
  }

  function renderGraph() {
    if (!G || $('view-graph').hidden || !window.echarts) return;
    const el = $('graph');
    if (!selected) { el.innerHTML = '<div class="view-empty">Select an asset to see its connections.</div>'; return; }
    if (!chart) {
      el.innerHTML = '';
      chart = echarts.init(el, null, { renderer: 'canvas' });
      chart.on('click', (p) => {
        if (p.dataType === 'node' && !p.data._ghost) select(p.data.id);
        if (p.dataType === 'edge') { edgeSel = p.data._i; renderEdgeInfo(); renderGraph(); }
      });
    }
    const vis = graphSet(), at = placeGraph(vis);
    // Positions in real pixels. ECharts would otherwise stretch x and y separately to fit the box,
    // which squashes the circles; the two invisible corner nodes below pin its fit to scale 1.
    const BW = el.clientWidth, BH = el.clientHeight, PX = 56, PT = 44, PB = 42;
    const xs = [...at.values()].map((p) => p[0]), ys = [...at.values()].map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const kx = x1 > x0 ? Math.min(170, (BW - 2 * PX) / (x1 - x0)) : 0;
    const ky = y1 > y0 ? Math.min(62, (BH - PT - PB) / (y1 - y0)) : 0;
    const px = (p) => [BW / 2 + (p[0] - (x0 + x1) / 2) * kx, PT + (BH - PT - PB) / 2 + (p[1] - (y0 + y1) / 2) * ky];
    // Narrow columns drop the name line (it stays in the tooltip).
    const W = x1 > x0 ? kx - 12 : 132;
    const wide = W >= 96;
    const mono = 'JetBrains Mono, monospace';
    const data = [...vis].map((id) => {
      const n = byId.get(id), isSel = id === selected, r = rank.get(id), c = css(TYPE_COLOR[n.asset_type] || '--ink-dim');
      const small = n.asset_type === 'WorkOrder' || n.asset_type === 'Sensor' || n.asset_type === 'Camera';
      const nm = n.asset_type === 'WorkOrder' ? `${n.work_type} · ${n.date}` : n.display_name;
      const fit = (s, w) => (s.length * 6 > w ? `${s.slice(0, Math.max(4, Math.floor(w / 6) - 1))}…` : s);
      return {
        id, name: id, x: px(at.get(id))[0], y: px(at.get(id))[1],
        // Circles coloured by type (kg-demo look); candidates get a dashed amber ring, selection a cyan ring.
        symbol: 'circle', symbolSize: isSel ? 34 : small ? 18 : 26,
        itemStyle: {
          color: c,
          borderColor: isSel ? css('--accent') : r ? css('--warn') : '#fff',
          borderWidth: isSel ? 4 : r ? 2.5 : 2,
          borderType: r && !isSel ? 'dashed' : 'solid',
          shadowBlur: isSel ? 16 : 0, shadowColor: css('--accent-glow'),
        },
        label: {
          // Sensors sit above their asset, so their label goes on top to stay clear of the link.
          show: true, position: n.asset_type === 'Sensor' ? 'top' : 'bottom', distance: 5, align: 'center',
          backgroundColor: css('--panel'), padding: [1, 3], borderRadius: 3,
          formatter: `{id|${id}}${r && wide ? ` {rk|#${r}}` : ''}${wide ? `\n{nm|${fit(nm, W - 14)}}` : ''}`,
          rich: {
            id: { fontFamily: mono, fontSize: 11, fontWeight: 700, color: isSel ? css('--accent') : css('--ink') },
            rk: { fontFamily: mono, fontSize: 9.5, fontWeight: 700, color: css('--warn') },
            nm: { fontSize: 9.5, color: css('--ink-mute'), padding: [1, 0, 0, 0] },
          },
        },
      };
    });
    const links = G.edges.map((e, i) => ({ e, i })).filter(({ e }) => showEdge(e) && vis.has(e.from) && vis.has(e.to)).map(({ e, i }) => ({
      source: e.from, target: e.to, _i: i, _t: e.type,
      // Words only on horizontal flow links with room; short vertical links rely on line style + legend.
      label: { show: wide && e.type === 'CONNECTED_TO' },
      lineStyle: {
        color: i === edgeSel ? css('--accent') : css(EDGE_COLOR[e.type]), width: i === edgeSel ? 2.6 : e.type === 'CONNECTED_TO' ? 1.8 : 1.2,
        type: e.type === 'MONITORS' ? 'dashed' : e.type === 'CAN_OBSERVE' ? 'dotted' : 'solid', curveness: 0,
      },
    }));
    chart.setOption({
      animationDurationUpdate: 450,
      tooltip: {
        backgroundColor: 'rgba(255,255,255,.98)', borderColor: css('--line-strong'),
        textStyle: { color: css('--ink'), fontFamily: mono, fontSize: 11.5 },
        extraCssText: 'box-shadow:0 10px 24px rgba(15,23,42,.1);border-radius:6px;padding:7px 10px;max-width:280px;white-space:normal;',
        formatter: (p) => {
          if (p.dataType === 'node' && p.data._ghost) return '';
          if (p.dataType === 'node') { const n = byId.get(p.data.id); return `<b>${esc(n.asset_id)}</b> · ${esc(n.asset_type)}<br>${esc(n.display_name)}`; }
          if (p.dataType === 'edge') { const e = G.edges[p.data._i]; return `<b>${e.type}</b><br>${esc(e.from)} → ${esc(e.to)}<br>Click for source`; }
          return '';
        },
      },
      series: [{
        // nodeScaleRatio 0: node size ignores the fit/zoom scale, so circles stay round.
        type: 'graph', layout: 'none', roam: true, nodeScaleRatio: 0,
        // Node centres are fitted to this box, so pad by half a card to keep cards fully visible.
        left: 0, right: 0, top: 0, bottom: 0,
        data: [...data, ...[[0, 0], [BW, BH]].map(([x, y], i) => ({ id: `_ghost${i}`, name: '', x, y, _ghost: true, symbolSize: 0, label: { show: false }, tooltip: { show: false } }))],
        links,
        edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 8,
        edgeLabel: {
          show: true, formatter: (p) => EDGE_WORDS[p.data._t] || p.data._t,
          fontFamily: mono, fontSize: 9.5, color: css('--ink-mute'),
          backgroundColor: css('--panel'), padding: [1, 4], borderRadius: 3,
        },
        emphasis: { focus: 'adjacency', lineStyle: { width: 2.4 } },
      }],
    }, true);
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
  let video = null, frameTime = null;

  function renderCam() {
    if (!G) return;
    const regions = G.camera_rois.regions || [];
    const drawn = regions.filter(hasPoly);
    $('vis-list').innerHTML = regions.length ? regions.map((r) => `
      <button class="vchip${r.asset_id === selected ? ' sel' : ''}" data-sel="${esc(r.asset_id)}" title="${esc(name(r.asset_id))} · ${hasPoly(r) ? 'ROI drawn' : 'ROI not drawn'}">
        <i class="d${hasPoly(r) ? ' on' : ''}"></i>${esc(r.asset_id)}</button>`).join('') : `<div class="note">${NO_REL}</div>`;
    const sel = roiOf(selected);
    $('vis-sel').innerHTML = !selected ? ''
      : !sel ? `<span class="t"><b>${esc(selected)}</b> is not in the CAM-01 mapping</span>`
      : `<span class="t" title="${esc(name(selected))}"><b>${esc(selected)}</b> · ${esc(name(selected))}</span>
         <span class="roi-tag${hasPoly(sel) ? ' on' : ''}">${hasPoly(sel) ? 'ROI' : 'No ROI'}</span>`;
    const cav = $('cam-caveat');
    cav.hidden = drawn.length === regions.length && regions.length > 0;
    cav.textContent = MISSING_CAM;

    const svg = $('cam-svg');
    if (!svg) return;
    const box = D && D.box;
    svg.innerHTML = `${box ? `<rect class="bbox" x="${box[0]}" y="${box[1]}" width="${box[2]}" height="${box[3]}"/>` : ''}
      ${drawn.map((r) => `<polygon class="roi${r.asset_id === selected ? ' sel' : ''}" points="${r.polygon_normalized.map((p) => p.join(',')).join(' ')}"/>`).join('')}`;
  }

  function initCam(d) {
    const meta = d.metadata, ann = d.annotations;
    // Same provenance rule as Screen 2: only a verified clip carries its stated provenance.
    const prov = meta.status === 'verified' && BADGE[meta.provenance] ? meta.provenance : meta.status === 'sample' ? 'simulated' : 'pending';
    $('cam-clip').textContent = meta.ogi_clip_id ? `METEC · ${meta.ogi_clip_id}` : 'METEC · clip not set';
    if (!d.clip_available) {
      $('cam-badge').innerHTML = badge('pending');
      $('cam-frame').innerHTML = '<div class="cam-empty">Approved METEC clip not loaded</div>';
      return Promise.reject(new Error('Clip not loaded'));
    }
    // First annotated frame of this exact clip (dataset identity).
    const ranges = ann.ogi_clip_id && ann.ogi_clip_id === meta.ogi_clip_id
      ? [...(ann.ranges || []).map((r) => ({ ...r, frame: r.start_frame })), ...(ann.frames || [])].filter((r) => r.bbox_normalized)
      : [];
    const first = ranges.sort((a, b) => a.frame - b.frame)[0];
    D = { box: first ? first.bbox_normalized : null };
    $('cam-badge').innerHTML = badge(prov, 'Clip') + (first ? badge('manual_annotation', 'Plume region') : '');

    const times = d.frame_times && d.frame_times.frame_start_s;
    const f = first ? first.frame : 0;
    frameTime = times && times[f] != null ? (times[f] + (times[f + 1] ?? times[f] + 0.05)) / 2 : (meta.frame_rate ? (f + 0.5) / meta.frame_rate : 0);
    $('cam-frame').innerHTML = `
      <video id="cam-video" preload="auto" playsinline muted src="${window.AtelicShell.API}/ogi/clip"></video>
      <svg id="cam-svg" viewBox="0 0 1 1" preserveAspectRatio="none"></svg>
      <span class="br tl"></span><span class="br tr"></span><span class="br bl"></span><span class="br bR"></span>
      <div class="cam-chip top"><span>CAM-01 · STILL · FRAME ${f}</span></div>
      <div class="cam-chip bot">${first ? '<span><i></i>Plume region · manual</span>' : ''}</div>`;
    const m = Math.floor(frameTime / 60), s = (frameTime % 60).toFixed(2).padStart(5, '0');
    $('cam-time').textContent = `· ${String(m).padStart(2, '0')}:${s}`;
    video = $('cam-video');
    renderCam();
    // Resolves when the still frame is really on screen (seeked), so the loader reports the truth.
    return new Promise((ok, fail) => {
      video.addEventListener('loadedmetadata', () => {
        if (video.videoWidth && video.videoHeight) $('cam-frame').style.setProperty('--ar', video.videoWidth / video.videoHeight);
        video.currentTime = frameTime;
      }, { once: true });
      video.addEventListener('seeked', () => ok(`${meta.ogi_clip_id} · frame ${f}`), { once: true });
      video.addEventListener('error', () => { $('cam-frame').innerHTML = '<div class="cam-empty">Clip could not be played</div>'; fail(new Error('Clip could not be played')); }, { once: true });
      setTimeout(() => fail(new Error('Frame still loading')), 6000);
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
      <div class="by"><span class="v-nm">${esc(name(likely) || '')}</span>${C.status === 'sample' ? ' · <b>Sample values</b> · mock ranking, no agent has run yet' : ` · ${esc(C.method || '')}`}</div>${bad}`
      : `<div class="v-k">Source</div><div class="v-id">Unresolved</div>
      <div class="by">${C.status === 'sample' ? '<b>Sample values</b> · mock ranking, no agent has run yet' : esc(C.method || '')}</div>${bad}`;
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
  const ev = (e) => `${esc(e.label)}<span class="src">${assetLink(e.source_id)} <span class="pdot" title="${BADGE[e.provenance] || ''}"></span></span>`;
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
    return `<h4>Related documents</h4>${list(recs, 'No maintenance records for this asset.')}`;
  }

  function mappingTab() {
    const regions = G.camera_rois.regions || [];
    return `<h4>Mapping assumptions</h4><div class="caveat">${MAP_DISC}</div>
      ${regions.some((r) => !hasPoly(r)) ? `<div class="caveat" style="margin-top:8px">${MISSING_CAM}</div>` : ''}
      <h4>Versions</h4>${kv([
        ['Graph store', G.store === 'json' ? 'JSON files (Neo4j not connected yet)' : G.store],
        ['Skid mapping', (G.nodes[0] || {}).mapping_version],
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

  function showView(tab) {
    const graph = tab.id === 'tab-graph';
    $('tab-layout').setAttribute('aria-selected', !graph);
    $('tab-graph').setAttribute('aria-selected', graph);
    $('tab-layout').tabIndex = graph ? -1 : 0;
    $('tab-graph').tabIndex = graph ? 0 : -1;
    $('view-layout').hidden = graph;
    $('view-graph').hidden = !graph;
    if (graph) { renderGraph(); if (chart) chart.resize(); } else renderLayout();
  }

  function wire() {
    ['tab-layout', 'tab-graph'].forEach((id) => $(id).addEventListener('click', () => showView($(id))));
    tabKeys(document.querySelector('.panel-head .tabs'), showView);
    const dt = (t) => { dtab = t.dataset.t; renderDetail(); };
    document.querySelectorAll('.dtabs .tab').forEach((t) => t.addEventListener('click', () => dt(t)));
    tabKeys(document.querySelector('.dtabs'), dt);
    document.querySelectorAll('.tools .tg').forEach((b) => b.addEventListener('click', () => {
      expand[b.dataset.x] = !expand[b.dataset.x];
      b.setAttribute('aria-pressed', expand[b.dataset.x]);
      renderGraph();
    }));
    new ResizeObserver(() => renderLayout()).observe($('layout'));
    new ResizeObserver(() => chart && chart.resize()).observe($('graph'));
  }

  async function init() {
    wire();
    const { get, INCIDENT_ID } = window.AtelicShell;
    const graphP = step('graph', get(`/incidents/${INCIDENT_ID}/graph`),
      (v) => `${v.nodes.length} assets · ${v.edges.length} relationships · ${v.store === 'json' ? 'JSON' : v.store}`);
    const candP = step('cands', get(`/incidents/${INCIDENT_ID}/candidates`),
      (v) => `${(v.candidates || []).length} candidates${v.status === 'sample' ? ' · sample' : ''}`);
    const [g, c, d] = await Promise.allSettled([graphP, candP, get(`/incidents/${INCIDENT_ID}/detections`)]);

    if (g.status === 'fulfilled') {
      G = g.value;
      byId = new Map(G.nodes.map((n) => [n.asset_id, n]));
      $('graph-badge').innerHTML = badge(G.nodes.some((n) => n.provenance !== 'synthetic') ? 'pending' : 'synthetic', 'Representative skid data');
    } else {
      const msg = '<div class="view-empty">Knowledge graph data not available.</div>';
      $('layout').innerHTML = msg;
      $('graph').innerHTML = msg;
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

    const start = assessment() || (C && C.candidates && C.candidates[0] && C.candidates[0].asset_id);
    if (G && start && byId.has(start)) select(start);
    else { renderLayout(); renderCands(); renderDetail(); renderCam(); }

    await Promise.allSettled([step('cam', camP, (v) => v)]);
    // Short hold so the finished list is readable, then reveal the panels.
    setTimeout(() => {
      $('loc-grid').classList.add('ready');
      $('loader').classList.add('done');
      renderLayout();
      if (chart) chart.resize();
    }, 450);
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
