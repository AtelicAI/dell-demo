// Plant knowledge graph — site-level explorer. Reads GET /incidents/:id/graph (the same skid graph Locate uses).
// ?asset=<id>&from=<page> preselects an asset and shows a back link (opened from Locate).
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  const { badge } = window.AtelicShell;
  const METHOD = { manual_illustrative: 'Manually configured', 'manually configured': 'Manually configured' };
  const TYPE_COLOR = {
    Vessel: '--n-station', Valve: '--n-equip', Vent: '--n-well', PipeSegment: '--ink-dim',
    Sensor: '--n-tag', Camera: '--n-wt', EquipmentArea: '--ink-mute', WorkOrder: '--n-sap',
  };
  const TYPE_NAME = { PipeSegment: 'Pipe', EquipmentArea: 'Area', WorkOrder: 'Work order' };
  const EDGE_COLOR = { CONNECTED_TO: '--ink-mute', MONITORS: '--ink-mute', CAN_OBSERVE: '--ink-dim', CONTAINS: '--ink-faint', HAS_WORK_ORDER: '--n-sap' };
  // Relationship filters. "Area" links every asset to the skid, so it starts hidden.
  const RELS = [
    ['CONNECTED_TO', 'Piping'], ['MONITORS', 'Sensors'], ['CAN_OBSERVE', 'Camera'], ['HAS_WORK_ORDER', 'Work orders'], ['CONTAINS', 'Area'],
  ];
  const on = { CONNECTED_TO: true, MONITORS: true, CAN_OBSERVE: true, HAS_WORK_ORDER: true, CONTAINS: false };
  const EQUIP = new Set(['Vessel', 'Valve', 'PipeSegment', 'Vent']);

  let G = null, byId = new Map(), at = null, topRow = 0, selected = null, edgeSel = null, chart = null;
  const params = new URLSearchParams(location.search);

  const typeOf = (id) => (byId.get(id) || {}).asset_type;
  const name = (id) => (byId.get(id) || {}).display_name || '';
  const assetLink = (id) => `<button class="linkish" data-sel="${esc(id)}">${esc(id)}</button>`;

  // ----- fixed reading layout -----
  // Three bands: sensors, camera and area on top; equipment in the middle with gas flowing left → right
  // (column = longest flow distance from an inlet); work orders along the bottom under their asset.
  const ROW = 1, BAND = 1.3;
  function place() {
    const pos = new Map();
    const pipes = G.edges.filter((e) => e.type === 'CONNECTED_TO');
    const eq = G.nodes.filter((n) => EQUIP.has(n.asset_type)).map((n) => n.asset_id);
    const col = new Map(eq.filter((id) => !pipes.some((e) => e.to === id)).map((id) => [id, 0]));
    for (let k = 0; k < eq.length; k++) {
      pipes.forEach((e) => { if (col.has(e.from) && (col.get(e.to) ?? -1) < col.get(e.from) + 1) col.set(e.to, col.get(e.from) + 1); });
    }
    eq.forEach((id) => { if (!col.has(id)) col.set(id, 0); });
    // Rows: each column near the mean height of its upstream nodes, at least one row apart.
    const cols = [...new Set(col.values())].sort((a, b) => a - b);
    cols.forEach((c) => {
      const ids = eq.filter((id) => col.get(id) === c);
      const bary = (id) => {
        const ys = pipes.filter((e) => e.to === id && pos.has(e.from)).map((e) => pos.get(e.from)[1]);
        return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0;
      };
      const b = ids.map((id) => [id, bary(id)]).sort((x, y) => x[1] - y[1]);
      const ys = [];
      b.forEach(([, v], i) => ys.push(i ? Math.max(v, ys[i - 1] + ROW) : v));
      const shift = b.reduce((s, [, v]) => s + v, 0) / b.length - ys.reduce((s, v) => s + v, 0) / ys.length;
      b.forEach(([id], i) => pos.set(id, [c, ys[i] + shift]));
    });
    const xs = [...pos.values()].map((p) => p[0]), yv = [...pos.values()].map((p) => p[1]);
    const top = (yv.length ? Math.min(...yv) : 0) - BAND, bottom = (yv.length ? Math.max(...yv) : 0) + BAND;
    const left = xs.length ? Math.min(...xs) : 0;
    const mean = (v, d) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : d);
    // Slide right until no node in the same band is closer than 0.6 columns.
    const free = (x, y) => {
      while ([...pos.values()].some((p) => p[1] === y && Math.abs(p[0] - x) < 0.6)) x += 0.6;
      return [x, y];
    };
    const linked = (id, type) => G.edges.filter((e) => e.type === type && (e.from === id || e.to === id))
      .map((e) => (e.from === id ? e.to : e.from)).filter((o) => pos.has(o)).map((o) => pos.get(o)[0]);
    const order = ['EquipmentArea', 'Sensor', 'Camera', 'WorkOrder'];
    G.nodes.map((n) => n.asset_id).filter((id) => !pos.has(id))
      .sort((a, b) => order.indexOf(typeOf(a)) - order.indexOf(typeOf(b))).forEach((id) => {
        const t = typeOf(id);
        if (t === 'EquipmentArea') pos.set(id, free(left, top));
        else if (t === 'Sensor') pos.set(id, free(mean(linked(id, 'MONITORS'), left), top));
        else if (t === 'Camera') pos.set(id, free(mean(linked(id, 'CAN_OBSERVE'), left), top));
        else if (t === 'WorkOrder') pos.set(id, free(mean(linked(id, 'HAS_WORK_ORDER'), left), bottom));
        else pos.set(id, free(left, bottom));
      });
    return { pos, top };
  }

  // ----- what is shown -----
  const shownEdges = () => G.edges.map((e, i) => ({ e, i })).filter(({ e }) => on[e.type]);
  function shownNodes() {
    const s = new Set(G.nodes.filter((n) => EQUIP.has(n.asset_type) || n.asset_type === 'EquipmentArea').map((n) => n.asset_id));
    shownEdges().forEach(({ e }) => { s.add(e.from); s.add(e.to); });
    return s;
  }

  function select(id) {
    if (!id || !byId.has(id)) return;
    selected = id;
    edgeSel = null;
    $('pick').value = id;
    try { history.replaceState(null, '', `?${new URLSearchParams({ ...Object.fromEntries(params), asset: id })}`); } catch (e) { /* ignore */ }
    render();
  }

  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-sel]');
    if (b) select(b.dataset.sel);
  });

  function render() { renderGraph(); renderAsset(); renderEdgeInfo(); }

  // ----- graph (ECharts, layout in real pixels) -----
  function renderGraph() {
    if (!G || !window.echarts) return;
    const el = $('kg');
    if (!chart) {
      el.innerHTML = '';
      chart = echarts.init(el, null, { renderer: 'canvas' });
      chart.on('click', (p) => {
        if (p.dataType === 'node' && !p.data._ghost) select(p.data.id);
        if (p.dataType === 'edge') { edgeSel = p.data._i; renderEdgeInfo(); renderGraph(); }
      });
    }
    const vis = shownNodes(), edges = shownEdges().filter(({ e }) => vis.has(e.from) && vis.has(e.to));
    const near = new Set(selected ? [selected] : []);
    edges.forEach(({ e }) => { if (e.from === selected) near.add(e.to); if (e.to === selected) near.add(e.from); });
    const dim = (id) => selected && !near.has(id);

    // Same pixel fit as Locate: two invisible corner nodes pin ECharts' fit to scale 1, so circles stay round.
    const BW = el.clientWidth, BH = el.clientHeight, PX = 70, PT = 54, PB = 52;
    const pts = [...vis].map((id) => at.get(id));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const kx = x1 > x0 ? Math.min(190, (BW - 2 * PX) / (x1 - x0)) : 0;
    const ky = y1 > y0 ? Math.min(120, (BH - PT - PB) / (y1 - y0)) : 0;
    const px = (p) => [BW / 2 + (p[0] - (x0 + x1) / 2) * kx, PT + (BH - PT - PB) / 2 + (p[1] - (y0 + y1) / 2) * ky];
    const W = x1 > x0 ? kx - 12 : 132, wide = W >= 96;
    const mono = 'IBM Plex Mono, monospace';
    const fit = (s, w) => (s.length * 6 > w ? `${s.slice(0, Math.max(4, Math.floor(w / 6) - 1))}…` : s);

    const P = new Map([...vis].map((id) => [id, px(at.get(id))]));
    const size = (id) => (id === selected ? 34 : ['WorkOrder', 'Sensor', 'Camera'].includes(typeOf(id)) ? 18 : 26);
    const above = (id) => at.get(id)[1] === topRow;
    const nameOf = (n) => (n.asset_type === 'WorkOrder' ? `${n.work_type} · ${n.date}` : n.display_name);
    // Label box (id line + name line) above or below the node, for the edge routing below.
    const labelBox = (id) => {
      const [x, y] = P.get(id), r = size(id) / 2, w = Math.min(W, Math.max(id.length * 7, wide ? nameOf(byId.get(id)).length * 5.6 : 0)) + 6;
      return above(id) ? [x - w / 2, y - r - 34, x + w / 2, y - r - 4] : [x - w / 2, y + r + 4, x + w / 2, y + r + 34];
    };
    // A straight link that would cross another node or its label bends around it (first clear curveness).
    const blocked = (a, b, c) => {
      const [x1, y1] = P.get(a), [x2, y2] = P.get(b);
      const cx = (x1 + x2) / 2 - (y1 - y2) * c, cy = (y1 + y2) / 2 - (x2 - x1) * c;
      let hits = 0;
      for (let t = 0.06; t < 0.95; t += 0.04) {
        const qx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2, qy = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2;
        vis.forEach((o) => {
          const [ox, oy] = P.get(o);
          if (o !== a && o !== b && Math.hypot(qx - ox, qy - oy) < size(o) / 2 + 7) hits++;
          if (o === a || o === b) return;
          const [l, tp, rt, bt] = labelBox(o);
          if (qx > l && qx < rt && qy > tp && qy < bt) hits++;
        });
      }
      return hits;
    };
    const route = (a, b) => {
      let best = [0, Infinity];
      for (const c of [0, 0.18, -0.18, 0.3, -0.3, 0.45, -0.45]) {
        const h = blocked(a, b, c);
        if (h < best[1]) best = [c, h];
        if (!h) break;
      }
      return best[0];
    };

    const data = [...vis].map((id) => {
      const n = byId.get(id), isSel = id === selected, c = css(TYPE_COLOR[n.asset_type] || '--ink-dim');
      const nm = nameOf(n);
      const [x, y] = P.get(id);
      return {
        id, name: id, x, y,
        symbol: 'circle', symbolSize: size(id),
        itemStyle: {
          color: c, opacity: dim(id) ? 0.3 : 1,
          borderColor: isSel ? css('--accent') : '#fff', borderWidth: isSel ? 4 : 2,
          shadowBlur: isSel ? 16 : 0, shadowColor: css('--accent-glow'),
        },
        label: {
          show: true, position: above(id) ? 'top' : 'bottom', distance: 5, align: 'center', opacity: dim(id) ? 0.4 : 1,
          backgroundColor: css('--panel'), padding: [1, 3], borderRadius: 3,
          formatter: `{id|${id}}${wide ? `\n{nm|${fit(nm, W - 14)}}` : ''}`,
          rich: {
            id: { fontFamily: mono, fontSize: 11, fontWeight: 700, color: isSel ? css('--accent') : css('--ink') },
            nm: { fontSize: 9.5, color: css('--ink-mute'), padding: [1, 0, 0, 0] },
          },
        },
      };
    });
    const links = edges.map(({ e, i }) => {
      const mine = selected && (e.from === selected || e.to === selected);
      return {
        source: e.from, target: e.to, _i: i, _t: e.type,
        // Arrow = direction of flow / measurement / view; work orders and area links are plain.
        symbol: ['none', ['CONNECTED_TO', 'MONITORS', 'CAN_OBSERVE'].includes(e.type) ? 'arrow' : 'none'],
        lineStyle: {
          color: i === edgeSel ? css('--accent') : css(EDGE_COLOR[e.type]),
          width: i === edgeSel ? 2.6 : mine ? 2 : e.type === 'CONNECTED_TO' ? 1.6 : 1.1,
          type: e.type === 'MONITORS' ? 'dashed' : e.type === 'CAN_OBSERVE' ? 'dotted' : 'solid',
          opacity: selected && !mine ? 0.22 : 1, curveness: route(e.from, e.to),
        },
      };
    });
    chart.setOption({
      animationDurationUpdate: 350,
      tooltip: {
        backgroundColor: 'rgba(255,255,255,.98)', borderColor: css('--line-strong'),
        textStyle: { color: css('--ink'), fontFamily: mono, fontSize: 11.5 },
        extraCssText: 'box-shadow:0 10px 24px rgba(15,23,42,.1);border-radius:4px;padding:7px 10px;max-width:280px;white-space:normal;',
        formatter: (p) => {
          if (p.dataType === 'node' && p.data._ghost) return '';
          if (p.dataType === 'node') { const n = byId.get(p.data.id); return `<b>${esc(n.asset_id)}</b> · ${esc(TYPE_NAME[n.asset_type] || n.asset_type)}<br>${esc(n.display_name)}`; }
          if (p.dataType === 'edge') { const e = G.edges[p.data._i]; return `<b>${e.type}</b><br>${esc(e.from)} → ${esc(e.to)}<br>Click for source`; }
          return '';
        },
      },
      series: [{
        type: 'graph', layout: 'none', roam: true, nodeScaleRatio: 0,
        left: 0, right: 0, top: 0, bottom: 0,
        data: [...data, ...[[0, 0], [BW, BH]].map(([x, y], i) => ({ id: `_ghost${i}`, name: '', x, y, _ghost: true, symbolSize: 0, label: { show: false }, tooltip: { show: false } }))],
        links,
        edgeSymbolSize: 7,
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

  // ----- selected asset -----
  const src = (e) => `<span class="src">${esc(e.source)} · ${esc(METHOD[e.method] || e.method)} ${badge(e.provenance)}</span>`;
  const group = (title, items, empty) => `<h4>${title}<span class="n">${items.length || ''}</span></h4>${items.length ? `<ul>${items.join('')}</ul>` : `<div class="empty">${empty}</div>`}`;

  function renderAsset() {
    if (!selected) return;
    const n = byId.get(selected), es = G.edges.filter((e) => e.from === selected || e.to === selected);
    const other = (e) => (e.from === selected ? e.to : e.from);
    const row = (dir, e, extra) => `<li><span class="dir">${dir}</span>${assetLink(other(e))} ${esc(name(other(e)))}${extra || ''}${src(e)}</li>`;
    const of = (t) => es.filter((e) => e.type === t);

    $('asset-head').innerHTML = `<div class="a-id">${esc(n.asset_id)}</div><div class="a-nm">${esc(n.display_name)}</div>
      <div class="a-ty"><i style="background:var(${TYPE_COLOR[n.asset_type] || '--ink-dim'})"></i>${esc(TYPE_NAME[n.asset_type] || n.asset_type)}</div>`;

    const facts = [['Asset ID', n.asset_id], ['Type', TYPE_NAME[n.asset_type] || n.asset_type]];
    if (n.measures) facts.push(['Measures', n.measures]);
    if (n.date) facts.push(['Date', n.date], ['Work type', n.work_type], ['Status', n.work_status]);
    facts.push(['Source', n.source], ['Mapping version', n.mapping_version]);

    $('detail').innerHTML = `
      ${group('Piping', of('CONNECTED_TO').map((e) => row(e.to === selected ? 'Upstream' : 'Downstream', e)), 'No piping connection.')}
      ${group('Sensors', of('MONITORS').map((e) => row(e.from === selected ? 'Monitors' : 'Measured by', e, byId.get(e.from).measures ? ` · ${esc(byId.get(e.from).measures)}` : '')), 'No sensor linked.')}
      ${group('Camera', of('CAN_OBSERVE').map((e) => row(e.from === selected ? 'Can observe' : 'Seen by', e)), 'No camera mapping covers this asset.')}
      ${group('Maintenance', of('HAS_WORK_ORDER').map((e) => {
        const w = byId.get(e.to);
        return e.from === selected
          ? `<li><span class="dir">${esc(w.date)}</span>${assetLink(w.asset_id)} · ${esc(w.work_type)} · ${esc(w.work_status)}<br><span style="color:var(--ink-mute)">${esc(w.display_name)}</span>${src(e)}</li>`
          : row('Record for', e);
      }), 'No maintenance records.')}
      ${group('Area', of('CONTAINS').map((e) => row(e.from === selected ? 'Contains' : 'Part of', e)), 'Not linked to an area.')}
      <h4>Asset details</h4><div class="kv">${facts.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v == null ? '<span style="color:var(--ink-dim)">Not available</span>' : esc(v)}</div>`).join('')}</div>`;
  }

  // ----- wiring -----
  function renderTools() {
    const count = (t) => G.edges.filter((e) => e.type === t).length;
    $('rels').innerHTML = RELS.map(([t, label]) => `<button class="tg" data-rel="${t}" aria-pressed="${on[t]}">${label} <span class="n">${count(t)}</span></button>`).join('');
    $('rels').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-rel]');
      if (!b) return;
      on[b.dataset.rel] = !on[b.dataset.rel];
      b.setAttribute('aria-pressed', on[b.dataset.rel]);
      edgeSel = null;
      renderGraph(); renderEdgeInfo();
    });
    const types = [...new Set(G.nodes.map((n) => n.asset_type))];
    $('pick').innerHTML = `<option value="" disabled>Find asset…</option>${types.map((t) => `<optgroup label="${esc(TYPE_NAME[t] || t)}">${G.nodes.filter((n) => n.asset_type === t)
      .map((n) => `<option value="${esc(n.asset_id)}">${esc(n.asset_id)} · ${esc(n.display_name.slice(0, 40))}</option>`).join('')}</optgroup>`).join('')}`;
    $('pick').addEventListener('change', (ev) => select(ev.target.value));
  }

  async function init() {
    window.AtelicShell.backBar(params.get('asset') ? ` · <b>${esc(params.get('asset'))}</b>` : '');
    const { get, INCIDENT_ID } = window.AtelicShell;
    try {
      G = await get(`/incidents/${INCIDENT_ID}/graph`);
    } catch (e) {
      $('kg').innerHTML = '<div class="view-empty">Knowledge graph data not available.</div>';
      $('kg-badge').innerHTML = badge('pending');
      return;
    }
    byId = new Map(G.nodes.map((n) => [n.asset_id, n]));
    ({ pos: at, top: topRow } = place());
    $('kg-badge').innerHTML = badge(G.nodes.some((n) => n.provenance !== 'synthetic') ? 'pending' : 'synthetic');
    $('store').textContent = `${G.nodes.length} assets · ${G.edges.length} relationships · ${G.store === 'json' ? 'read from JSON files (Neo4j not connected yet)' : G.store} · ${(G.nodes[0] || {}).mapping_version || ''}`;
    renderTools();
    const start = params.get('asset');
    select(byId.has(start) ? start : (G.nodes.find((n) => n.asset_type === 'Vessel') || G.nodes[0]).asset_id);
    new ResizeObserver(() => { if (chart) { chart.resize(); renderGraph(); } }).observe($('kg'));
  }

  document.addEventListener('DOMContentLoaded', init);
})();
