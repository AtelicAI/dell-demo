// Plant knowledge graph. Reads GET /incidents/:id/graph (Neo4j, through the UI API) and draws it as a network graph
// (Cytoscape.js): typed round nodes with icons, labelled relationships, type filters that double as the legend.
// It starts focused and grows on demand, so it stays readable:
//   Focus     the selected node and its neighbours (1 or 2 hops), in rings around it.
//   Incident  the camera, the source candidates and the records the agents cited.
//   Whole     every node of the shown types.
// Double-click a node to add its neighbours; the inspector on the right shows the selected node or relationship, and
// the records below (station history, the selected asset's work orders, the incident evidence) live in tabs.
// URL: ?asset=<id> · ?view=focus|incident|site · ?show=INC-xxx · ?from=<page>.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const EQUIP = new Set(['Vessel', 'Valve', 'Vent', 'Compressor', 'Flange']);
  const TYPE_NAME = { PipeSegment: 'Line segment', EquipmentArea: 'Area', WorkOrder: 'Work order', FieldReport: 'Field report', IncidentRecord: 'Closed incident',
    Sensor: 'Instrument', Camera: 'Camera', Document: 'Document', Commitment: 'Commitment' };
  const METHOD = { manual_illustrative: 'Manually configured', 'manually configured': 'Manually configured' };
  const WO_ST = { closed: ['Closed', ''], open: ['Open', 'warn'], deferred: ['Deferred', 'warn'], closed_with_exception: ['Closed with exception', 'warn'] };
  const CM_ST = { done: ['Done', 'good'], not_done: ['Not done', 'bad'], open: ['Open', 'warn'], deferred: ['Deferred', 'warn'] };
  const LINK = { FOLLOWS_UP: ['Follows up', 'Followed up by'], REVIEWS: ['Reviews', 'Reviewed by'],
    REPEATS_SURVEY_OF: ['Repeats survey of', 'Survey repeated by'], SAME_EQUIPMENT_AS: ['Same equipment as', 'Same equipment as'] };
  const REL = { CONNECTED_TO: 'flows to', HAS_DOCUMENT: 'document', CONTAINS: 'contains', MONITORS: 'monitors', ON_ASSET: 'on asset',
    REFERENCES: 'cites', HAS_WORK_ORDER: 'work order', DOCUMENTS: 'documents', COMMITTED_TO: 'commits to', CAN_OBSERVE: 'can observe',
    PART_OF: 'part of', REVIEWS: 'reviews', CONSIDERED: 'considered', SAME_EQUIPMENT_AS: 'same equipment', REPEATS_SURVEY_OF: 'repeats survey',
    FOLLOWS_UP: 'follows up', FULFILLED_BY: 'fulfilled by', CONCERNS: 'concerns' };
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const LANE_OF = { site: 'history', focus: 'story', incident: 'evidence' };

  // Node groups: one steel/slate family, the icon carries the type. Colour beyond that is only state.
  const ICON = {
    valve: '<path d="M4 7l16 10V7L4 17z"/>',
    compressor: '<circle cx="12" cy="12" r="7"/><path d="M6.5 9.2l11 1.6M6.5 14.8l11-1.6"/>',
    vessel: '<rect x="8" y="3" width="8" height="18" rx="4"/>',
    flange: '<path d="M10 5v14M14 5v14M3 12h7M14 12h7"/>',
    vent: '<path d="M12 21V7M7 11.5l5-6 5 6"/>',
    line: '<path d="M3 12h18M7 9v6M17 9v6"/>',
    sensor: '<circle cx="12" cy="13" r="7"/><path d="M12 13l3.5-3.5M8 6.5l.6 1M16 6.5l-.6 1"/>',
    camera: '<rect x="3" y="7" width="12.5" height="10" rx="2"/><path d="M15.5 11l5-3v8l-5-3"/>',
    wo: '<path d="M14.7 6.3a4 4 0 0 0-5.2 5.2L4 17l3 3 5.5-5.5a4 4 0 0 0 5.2-5.2l-2.6 2.6-2.4-.6-.6-2.4z"/>',
    commit: '<path d="M5 12.5l4.2 4.2L19 7"/>',
    report: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3.5"/>',
    doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    incident: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.2v.3"/>',
    area: '<rect x="4" y="4" width="16" height="16" rx="1.5" stroke-dasharray="3 2.5"/>',
    other: '<circle cx="12" cy="12" r="5"/>',
  };
  const GROUP = {
    equip: { label: 'Equipment', fill: '#2E6399', size: 40, types: ['Vessel', 'Compressor', 'Valve', 'Flange', 'Vent'] },
    line: { label: 'Line segment', fill: '#5B83AD', size: 28, icon: 'line', types: ['PipeSegment'] },
    sensor: { label: 'Instrument', fill: '#7A8FA8', size: 30, icon: 'sensor', types: ['Sensor', 'WeatherStation'] },
    camera: { label: 'Camera', fill: '#24507D', size: 36, icon: 'camera', types: ['Camera'] },
    wo: { label: 'Work order', fill: '#475569', size: 32, icon: 'wo', types: ['WorkOrder'] },
    commit: { label: 'Commitment', fill: '#64748B', size: 24, icon: 'commit', types: ['Commitment'] },
    report: { label: 'Field report', fill: '#8A9BB0', size: 28, icon: 'report', types: ['FieldReport'] },
    doc: { label: 'Document', fill: '#FFFFFF', ink: '#475569', size: 28, icon: 'doc', types: ['Document'] },
    incident: { label: 'Closed incident', fill: '#0F172A', size: 34, icon: 'incident', types: ['IncidentRecord'] },
    area: { label: 'Area', fill: '#E2E8F0', ink: '#475569', size: 44, icon: 'area', types: ['EquipmentArea'] },
  };
  const EQ_ICON = { Vessel: 'vessel', Compressor: 'compressor', Valve: 'valve', Flange: 'flange', Vent: 'vent' };
  const groupOf = (t) => Object.keys(GROUP).find((g) => GROUP[g].types.includes(t)) || 'equip';
  const iconUrl = (name, fill, ink) => `data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 24 24"><circle cx="12" cy="12" r="12" fill="${fill}"/><g transform="translate(5.4 5.4) scale(.55)" fill="none" stroke="${ink || '#FFFFFF'}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ICON[name] || ICON.other}</g></svg>`)}`;
  const nodeIcon = (t) => { const g = GROUP[groupOf(t)]; return iconUrl(g.icon || EQ_ICON[t] || 'other', g.fill, g.ink); };

  let G = null, byId = new Map(), cy = null;
  let mode = 'focus', lane = 'story', depth = 1, selected = null, selEdge = null, hlRecord = null, showInc = null, picker = null;
  let incSets = {}, incList = [], PAST = [], incDay = null;
  const conns = [], flowCol = new Map();
  const shownGroups = new Set(Object.keys(GROUP).filter((g) => g !== 'area' && g !== 'doc'));
  const extra = new Set(), hidden = new Set();
  const params = new URLSearchParams(location.search);

  const typeOf = (id) => (byId.get(id) || {}).asset_type;
  const nameOf = (id) => { const n = byId.get(id); return n && n.display_name !== id ? n.display_name : ''; };
  const outs = (id, t) => G.edges.filter((e) => e.from === id && (!t || e.type === t)).map((e) => e.to);
  const ins = (id, t) => G.edges.filter((e) => e.to === id && (!t || e.type === t)).map((e) => e.from);
  const nbrs = (id) => G.edges.flatMap((e) => (e.from === id ? [e.to] : e.to === id ? [e.from] : []));
  const link = (id, text) => `<button class="linkish" data-sel="${esc(id)}">${esc(text || id)}</button>`;
  const day = (s) => Date.parse(`${String(s).slice(0, 10)}T00:00:00Z`) / 864e5;
  const fmt = (s) => { const [y, m, d] = String(s).slice(0, 10).split('-'); return { dm: `${Number(d)} ${MON[m - 1]}`, y }; };
  const st = (map, k, cls = '') => { const [t, c] = map[k] || [String(k || '').replace(/_/g, ' '), '']; return `<span class="st ${c} ${cls}">${esc(t)}</span>`; };
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const closedByEngine = (l) => (l.stages || []).some((x) => x.stage === 'close' && x.status === 'completed');
  const camAlarm = (cam) => incList.some((l) => l.camera_id === cam && !l.dismissal && !closedByEngine(l));
  const fadeIn = (...els) => els.forEach((el) => { el.classList.remove('fade-in'); void el.offsetWidth; el.classList.add('fade-in'); });
  const curInc = () => incSets[showInc || (incList[0] || {}).incident_id];

  // Flow connections between equipment (a pipe segment between two items is the line), and flow order for sorting.
  function flow() {
    const isEq = (id) => EQUIP.has(typeOf(id)), isPipe = (id) => typeOf(id) === 'PipeSegment';
    G.edges.forEach((e) => {
      if (e.type !== 'CONNECTED_TO' || !isEq(e.from)) return;
      if (isEq(e.to)) { conns.push({ a: e.from, b: e.to, pipe: null }); return; }
      if (!isPipe(e.to)) return;
      const walk = (p, pipes) => {
        const next = G.edges.filter((x) => x.type === 'CONNECTED_TO' && x.from === p);
        if (!next.length) conns.push({ a: e.from, b: null, pipe: pipes[0] });
        next.forEach((x) => {
          if (isEq(x.to)) conns.push({ a: e.from, b: x.to, pipe: pipes[0] });
          else if (isPipe(x.to) && !pipes.includes(x.to)) walk(x.to, [...pipes, x.to]);
        });
      };
      walk(e.to, [e.to]);
    });
    G.nodes.filter((n) => isEq(n.asset_id)).forEach((n) => flowCol.set(n.asset_id, 0));
    for (let k = 0; k < flowCol.size; k++) conns.forEach((c) => { if (c.b && flowCol.get(c.b) < flowCol.get(c.a) + 1) flowCol.set(c.b, flowCol.get(c.a) + 1); });
    conns.forEach((c) => { if (c.pipe && !flowCol.has(c.pipe)) flowCol.set(c.pipe, flowCol.get(c.a) + 0.5); });
  }

  // ===== which nodes are on the canvas =====
  const allowed = (id) => byId.has(id) && !hidden.has(id) && (shownGroups.has(groupOf(typeOf(id))) || id === selected);
  function baseSet() {
    if (mode === 'incident' && curInc()) return new Set(curInc().nodes);
    if (mode === 'site') return new Set(G.nodes.map((n) => n.asset_id));
    const s = new Set(selected ? [selected] : []);
    let ring = [...s];
    for (let k = 0; k < depth; k++) {
      // Hops only walk through shown types, so a hidden documents layer does not pull in everything it links.
      ring = [...new Set(ring.flatMap(nbrs))].filter((x) => !s.has(x) && allowed(x));
      ring.forEach((x) => s.add(x));
    }
    return s;
  }
  const visibleIds = () => new Set([...baseSet(), ...extra].filter(allowed));

  // ===== graph canvas =====
  function styles() {
    const sizes = Object.entries(GROUP).map(([g, v]) => ({ selector: `node.g-${g}`, style: { width: v.size, height: v.size } }));
    return [
      { selector: 'node', style: {
        'background-color': '#FFFFFF', 'background-image': 'data(icon)', 'background-fit': 'cover', 'background-opacity': 0,
        'border-width': 0, label: 'data(label)', 'font-family': 'IBM Plex Mono, monospace', 'font-size': 10.5, 'font-weight': 600, color: '#0F172A',
        'text-valign': 'bottom', 'text-margin-y': 5, 'text-wrap': 'wrap', 'text-max-width': 150,
        'text-background-color': '#FFFFFF', 'text-background-opacity': 0.88, 'text-background-padding': 1.5, 'text-background-shape': 'roundrectangle', 'min-zoomed-font-size': 7,
        'transition-property': 'opacity, underlay-opacity, border-width', 'transition-duration': 180, 'transition-timing-function': 'ease-out' } },
      ...sizes,
      { selector: 'node.g-doc', style: { 'border-width': 1.2, 'border-color': '#94A3B8' } },
      { selector: 'node.g-equip, node.g-camera', style: { 'font-size': 13 } },
      { selector: 'node.g-commit, node.g-line, node.g-report', style: { 'font-size': 9.5, 'font-weight': 500, color: '#475569' } },
      // state (colour only for state)
      { selector: 'node.wo-open', style: { 'border-width': 3, 'border-color': '#B5650B' } },
      { selector: 'node.cm-done', style: { 'border-width': 3, 'border-color': '#2F7D4F' } },
      { selector: 'node.cm-open, node.cm-deferred', style: { 'border-width': 3, 'border-color': '#B5650B' } },
      { selector: 'node.cm-not_done', style: { 'border-width': 3, 'border-color': '#C62828' } },
      { selector: 'node.alarm', style: { 'border-width': 3.5, 'border-color': '#C62828', 'underlay-color': '#C62828', 'underlay-opacity': 0.12, 'underlay-padding': 7, 'underlay-shape': 'ellipse' } },
      { selector: 'node.cand', style: { 'border-width': 3, 'border-color': '#B5650B', 'border-style': 'dashed', color: '#B5650B' } },
      { selector: 'node.src', style: { 'border-width': 3.5, 'border-color': '#B5650B', 'border-style': 'solid', color: '#B5650B', 'underlay-color': '#B5650B', 'underlay-opacity': 0.14, 'underlay-padding': 8, 'underlay-shape': 'ellipse' } },
      { selector: 'node.named', style: { label: 'data(full)' } },
      { selector: 'node.sel', style: { 'font-size': 12, 'text-max-width': 220, 'underlay-color': '#2E6399', 'underlay-opacity': 0.2, 'underlay-padding': 9, 'underlay-shape': 'ellipse', color: '#2E6399' } },
      { selector: 'edge', style: {
        width: 1.3, 'line-color': '#CBD5E1', 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'target-arrow-color': '#CBD5E1', 'arrow-scale': 0.75,
        label: '', 'font-family': 'IBM Plex Mono, monospace', 'font-size': 8.5, color: '#64748B', 'text-rotation': 'autorotate',
        'text-background-color': '#FFFFFF', 'text-background-opacity': 1, 'text-background-padding': 1.5,
        'transition-property': 'opacity, line-color, target-arrow-color, width', 'transition-duration': 180, 'transition-timing-function': 'ease-out' } },
      { selector: 'edge.lab', style: { label: 'data(label)' } },
      { selector: 'edge.peri', style: { width: 1, 'line-color': '#E2E8F0', 'target-arrow-color': '#E2E8F0' } },
      { selector: 'edge.flow', style: { width: 2.2, 'line-color': '#64748B', 'target-arrow-color': '#64748B' } },
      { selector: 'edge.hot', style: { 'line-color': '#2E6399', 'target-arrow-color': '#2E6399', width: 2, label: 'data(label)', color: '#2E6399', 'z-index': 9 } },
      { selector: 'edge:selected', style: { 'line-color': '#2E6399', 'target-arrow-color': '#2E6399', width: 3, label: 'data(label)', color: '#2E6399' } },
      { selector: '.faded', style: { opacity: 0.14 } },
      { selector: '.leaving', style: { events: 'no' } },
    ];
  }

  function nodeData(id) {
    const n = byId.get(id), t = n.asset_type, I = mode === 'incident' ? curInc() : null;
    const cls = [`g-${groupOf(t)}`];
    if (t === 'WorkOrder' && n.work_status !== 'closed') cls.push('wo-open');
    if (t === 'Commitment' && n.work_status) cls.push(`cm-${n.work_status}`);
    if (t === 'Camera' && camAlarm(id)) cls.push('alarm');
    if (I && I.cands.has(id)) cls.push(I.source === id ? 'src' : 'cand');
    if (id === selected) cls.push('sel', 'named');
    const nm = nameOf(id), short = nm.length > 26 ? `${nm.slice(0, 25)}…` : nm;
    const tag = I && I.source === id ? '\nLIKELY SOURCE · HYPOTHESIS' : I && I.cands.has(id) ? '\nCANDIDATE' : '';
    return { group: 'nodes', data: { id, label: `${id}${tag}`, full: `${id}${short ? `\n${short}` : ''}${tag}`, icon: nodeIcon(t) }, classes: cls.join(' ') };
  }

  const FADE = 260, MOVE = 650, EASE = 'ease-in-out-cubic';
  // Fade an element in; the inline opacity is dropped afterwards so hover fading keeps working.
  const fadeTo1 = (el, delay = 0) => el.style('opacity', el.hasClass('leaving') ? el.style('opacity') : 0).delay(delay).animate({ style: { opacity: 1 } }, { duration: FADE, easing: 'ease-out', complete: () => el.removeStyle('opacity') });

  // Changes are applied as a difference: what leaves fades out, what stays keeps its place, what is new fades in
  // next to a node it connects to, then the layout glides everything to its new place.
  function draw(how) {
    const want = visibleIds(), edges = G.edges.map((e, i) => ({ e, i })).filter(({ e }) => want.has(e.from) && want.has(e.to)
      && (e.type !== 'CONTAINS' || shownGroups.has('area')));
    // Labels only where they can be read: the selected node's relationships (focus), or a small graph (incident).
    const labelAll = mode === 'incident' && edges.length <= 45;
    const touches = (e) => e.from === selected || e.to === selected;
    const edgeCls = (e) => `${labelAll || (mode === 'focus' && touches(e)) ? 'lab' : ''}${mode === 'focus' && !touches(e) ? ' peri' : ''}${e.type === 'CONNECTED_TO' ? ' flow' : ''}`;
    const wantE = new Set(edges.map(({ i }) => `e${i}`));
    const anchor = cy.getElementById(selected);
    const around = anchor.nonempty() && !anchor.hasClass('leaving') ? anchor.position() : { x: cy.width() / 2, y: cy.height() / 2 };
    cy.batch(() => {
      cy.elements().filter((el) => (el.isNode() ? !want.has(el.id()) : !wantE.has(el.id())) && !el.hasClass('leaving')).forEach((el) => {
        el.addClass('leaving');
        el.stop(true).animate({ style: { opacity: 0 } }, { duration: FADE * 0.8, easing: 'ease-in', complete: () => el.remove() });
      });
      want.forEach((id) => {
        const d = nodeData(id), cur = cy.getElementById(id);
        if (cur.nonempty()) {
          if (cur.hasClass('leaving')) { cur.stop(true); fadeTo1(cur); }
          cur.data(d.data); cur.classes(d.classes);
          return;
        }
        const near = nbrs(id).map((x) => cy.getElementById(x)).find((x) => x.nonempty() && !x.hasClass('leaving'));
        const p = near ? near.position() : around;
        fadeTo1(cy.add({ ...d, position: { x: p.x + (Math.random() - 0.5) * 60, y: p.y + (Math.random() - 0.5) * 60 } }), 80);
      });
      edges.forEach(({ e, i }) => {
        const id = `e${i}`, cur = cy.getElementById(id);
        if (cur.nonempty()) {
          if (cur.hasClass('leaving')) { cur.stop(true); fadeTo1(cur); }
          cur.classes(edgeCls(e));
          return;
        }
        fadeTo1(cy.add({ group: 'edges', data: { id, source: e.from, target: e.to, label: REL[e.type] || e.type.toLowerCase().replace(/_/g, ' ') }, classes: edgeCls(e) }), 200);
      });
      cy.edges().unselect();
      if (selEdge != null) cy.getElementById(`e${selEdge}`).select();
    });
    layout(how);
  }

  function layout(how) {
    if (how === 'none') return;
    const els = cy.elements().not('.leaving');
    const fresh = !cy.nodes().some((n) => n.scratch('placed'));
    if (how === 'grow') {
      // keep what was there; only the new nodes settle in
      const old = cy.nodes().filter((n) => n.scratch('placed'));
      old.lock();
      els.layout({ name: 'cose', animate: 'end', animationDuration: MOVE, animationEasing: EASE, randomize: false, fit: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 85, gravity: 0.2, numIter: 800, padding: 40 })
        .one('layoutstop', () => {
          old.unlock(); cy.nodes().scratch('placed', true);
          // bring new nodes into view if they settled outside it
          const bb = els.boundingBox(), ext = cy.extent();
          if (bb.x1 < ext.x1 || bb.y1 < ext.y1 || bb.x2 > ext.x2 || bb.y2 > ext.y2) cy.animate({ fit: { eles: els, padding: 36 } }, { duration: 400, easing: EASE });
        }).run();
      return;
    }
    let opts;
    if (mode === 'focus' && selected && cy.getElementById(selected).nonempty()) {
      // rings by hop count around the selected node
      const hop = new Map([[selected, 0]]);
      let ring = [selected];
      for (let k = 1; ring.length; k++) {
        ring = [...new Set(ring.flatMap((id) => cy.getElementById(id).neighborhood('node').map((n) => n.id())))].filter((id) => !hop.has(id));
        ring.forEach((id) => hop.set(id, k));
      }
      const max = Math.max(...hop.values());
      opts = { name: 'concentric', concentric: (n) => max - (hop.get(n.id()) ?? max), levelWidth: () => 1, minNodeSpacing: 34, spacingFactor: 1.05,
        startAngle: (3 / 2) * Math.PI, avoidOverlap: true, animate: true, animationDuration: MOVE, animationEasing: EASE, padding: 36 };
    } else {
      // Incident: a small graph with long chains (sensor → compressor → pipe, work order → commitment); strong repulsion and
      // little gravity spread it across the canvas so it fits at a readable zoom instead of a narrow column.
      const site = mode === 'site';
      opts = { name: 'cose', animate: 'end', animationDuration: MOVE, animationEasing: EASE, randomize: fresh, nodeRepulsion: () => (site ? 60000 : 90000),
        idealEdgeLength: () => (site ? 50 : 60), edgeElasticity: () => 120, gravity: site ? 6 : 0.15, numIter: 2500, nodeOverlap: 24, componentSpacing: 50, padding: 30 };
    }
    els.layout(opts).one('layoutstop', () => cy.nodes().scratch('placed', true)).run();
  }

  // Hover: the node, its relationships and its neighbours stay; the rest fades.
  function hover(n) {
    cy.batch(() => {
      cy.elements().removeClass('faded hot');
      cy.nodes().not('.sel').removeClass('named');
      if (!n) return;
      cy.elements().not(n.closedNeighborhood()).addClass('faded');
      n.connectedEdges().addClass('hot');
      n.closedNeighborhood('node').addClass('named');
    });
  }

  function wireCanvas() {
    cy = window.cytoscape({ container: $('cy'), style: styles(), minZoom: 0.2, maxZoom: 3, boxSelectionEnabled: false, autounselectify: false });
    // Sharp rendering: Cytoscape's layered cache redraws from lower-resolution snapshots after a change
    // (soft on high-DPI screens); draw each element from its own cache at the current zoom instead.
    const lyr = cy.renderer && cy.renderer().data && cy.renderer().data.lyrTxrCache;
    if (lyr) lyr.getLayers = () => null;
    cy.on('mouseover', 'node', (e) => { hover(e.target); $('cy').style.cursor = 'pointer'; });
    cy.on('mouseout', 'node', () => { hover(null); $('cy').style.cursor = ''; });
    cy.on('mouseover', 'edge', (e) => { e.target.addClass('hot'); $('cy').style.cursor = 'pointer'; });
    cy.on('mouseout', 'edge', (e) => { e.target.removeClass('hot'); $('cy').style.cursor = ''; });
    cy.on('tap', 'node', (e) => select(e.target.id(), { keepView: true }));
    cy.on('dbltap', 'node', (e) => expand(e.target.id()));
    cy.on('tap', 'edge', (e) => { selEdge = Number(e.target.id().slice(1)); renderSide(); });
    cy.on('tap', (e) => { if (e.target === cy && selEdge != null) { selEdge = null; cy.edges().unselect(); renderSide(); } });
    $('canvas').querySelector('.zoom').addEventListener('click', (e) => {
      const b = e.target.closest('[data-zoom]');
      if (!b) return;
      if (b.dataset.zoom === 'fit') cy.animate({ fit: { padding: 36 } }, { duration: 250 });
      else cy.animate({ zoom: { level: cy.zoom() * (b.dataset.zoom === 'in' ? 1.25 : 0.8), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } } }, { duration: 150 });
    });
  }

  function expand(id) {
    nbrs(id).filter(allowed).forEach((x) => extra.add(x));
    extra.add(id);
    selected = id; selEdge = null;
    syncUrl(); renderBar(); draw('grow'); renderCtx(); renderLane(); renderSide();
  }

  // ===== toolbar, filters, context =====
  function renderBar() {
    const I = incList.length;
    $('modes').innerHTML = `<button data-mode="focus" aria-pressed="${mode === 'focus'}"${selected ? '' : ' disabled'}>Focus<span class="k">${esc(selected || '')}</span></button>
      <button data-mode="incident" aria-pressed="${mode === 'incident'}"${I ? '' : ' disabled title="No alert raised yet"'}>Incident<span class="k">${esc(incList.length > 1 ? '' : showInc || (I ? incList[0].incident_id : 'none'))}</span></button>
      <button data-mode="site" aria-pressed="${mode === 'site'}">Whole graph</button>`;
    $('depth').hidden = mode !== 'focus';
    $('depth').innerHTML = [1, 2].map((d) => `<button data-depth="${d}" aria-pressed="${depth === d}">${d} hop${d === 1 ? '' : 's'}</button>`).join('');
    const count = (g) => G.nodes.filter((n) => groupOf(n.asset_type) === g).length;
    $('types').innerHTML = Object.entries(GROUP).filter(([g]) => count(g)).map(([g, v]) => `<button type="button" class="chip" data-group="${g}" aria-pressed="${shownGroups.has(g)}" title="${shownGroups.has(g) ? 'Hide' : 'Show'} ${esc(v.label.toLowerCase())} nodes">
      <img alt="" src="${iconUrl(v.icon || 'valve', v.fill, v.ink)}">${esc(v.label)}<span class="k">${count(g)}</span></button>`).join('');
    const keys = [];
    if (G.nodes.some((n) => n.asset_type === 'WorkOrder' && n.work_status !== 'closed')) keys.push(['#B5650B', 'Open work order or commitment']);
    if (G.nodes.some((n) => n.asset_type === 'Commitment' && n.work_status === 'not_done')) keys.push(['#C62828', 'Commitment not done']);
    if (G.nodes.some((n) => n.asset_type === 'Commitment' && n.work_status === 'done')) keys.push(['#2F7D4F', 'Commitment done']);
    if (mode === 'incident') keys.push(['#B5650B', 'Source candidate (dashed) · likely source (solid)']);
    $('state-key').innerHTML = keys.map(([c, l]) => `<span><i style="border-color:${c}"></i>${l}</span>`).join('') + '<span class="hint"><b>Click</b> to inspect · <b>double-click</b> to expand · drag to move · scroll to zoom</span>';
  }

  function renderCtx() {
    const box = $('ctx'), { badge } = window.AtelicShell;
    const n = cy ? cy.nodes().not('.leaving').length : 0, e = cy ? cy.edges().not('.leaving').length : 0, more = extra.size || hidden.size ? `<button class="linkish" data-reset>Reset view</button>` : '';
    const showing = `<span>· showing ${plural(n, 'node')}, ${plural(e, 'relationship')}</span>`;
    if (mode === 'incident' && curInc()) {
      const I = curInc(), l = I.l, closed = l.dismissal || closedByEngine(l);
      box.innerHTML = `<i class="dot ${closed ? 'good' : 'bad'}"></i><b>${esc(l.incident_id)}</b><span>${esc(l.camera_id)} ${l.dismissal ? 'closed as a false alarm' : closed ? 'incident closed' : 'alarm open'} · ${plural(I.cands.size, 'candidate')}${I.source ? ` · likely source <b>${esc(I.source)}</b>` : ''}</span>${I.source ? badge('hypothesis') : ''}${showing}<span class="act">${more}</span>`;
    } else if (mode === 'focus' && selected) {
      box.innerHTML = `<i class="dot acc"></i><b>${esc(selected)}</b><span>${esc(nameOf(selected) || TYPE_NAME[typeOf(selected)] || '')} and everything ${depth === 1 ? 'linked to it' : 'within two hops'}</span>${showing}<span class="act">${more}</span>`;
    } else {
      box.innerHTML = `<i class="dot"></i><span>Whole graph of the shown types</span>${showing}<span class="act">${more}</span>`;
    }
  }

  // ===== records panel =====
  function renderTabs() {
    const t = selected && typeOf(selected), canStory = selected && t !== 'EquipmentArea';
    const inc = (curInc() || {}).l;
    const n = G.nodes.filter((x) => x.asset_type === 'WorkOrder').length;
    const tab = (k, label, sub, dis) => `<button role="tab" data-lane="${k}" aria-selected="${lane === k}"${dis ? ' disabled' : ''}>${label}${sub ? `<span class="k">${esc(sub)}</span>` : ''}</button>`;
    $('tabs').innerHTML = tab('history', 'Station history', String(n)) + tab('story', 'Records', canStory ? selected : '', !canStory)
      + tab('evidence', 'Incident evidence', inc ? inc.incident_id : '', !inc);
  }
  function renderLane() {
    if (lane === 'story' && !(selected && typeOf(selected) !== 'EquipmentArea')) lane = 'history';
    if (lane === 'evidence' && !curInc()) lane = 'history';
    renderTabs();
    const body = $('lane');
    fadeIn(body);
    if (lane === 'evidence') return renderEvidence(body, curInc());
    if (lane === 'story') return renderStory(body);
    body.innerHTML = '<svg id="tl" role="img" aria-label="Maintenance history"></svg><div class="tl-key"><span><i></i>Work order closed</span><span><i class="o"></i>Open, deferred or closed with exception</span><span><i class="a"></i>Follows up · reviews · repeats survey</span><span><i class="d" style="background:var(--bad)"></i>Commitment not done</span><span><i class="d" style="background:var(--warn)"></i>open</span><span><i class="d" style="background:var(--good)"></i>done</span></div>';
    drawTimeline($('tl'));
  }

  function drawTimeline(el) {
    const order = (id) => flowCol.get(id) ?? 99;
    const rows = [...new Set(G.edges.filter((e) => e.type === 'HAS_WORK_ORDER').map((e) => e.from))].sort((a, b) => order(a) - order(b) || a.localeCompare(b));
    const wos = G.nodes.filter((n) => n.asset_type === 'WorkOrder' && n.date);
    if (!rows.length || !wos.length) { el.outerHTML = '<div class="note">No maintenance records in the knowledge graph.</div>'; return; }
    const W = Math.max(600, el.parentElement.clientWidth), LBL = 180, R = 24, TOP = 34, RHt = 46;
    let d0 = Math.min(...wos.map((w) => day(w.date))), d1 = Math.max(...wos.map((w) => day(w.date)));
    if (incDay) d1 = Math.max(d1, incDay);
    d0 -= 8; d1 += 8;
    const X = (d) => LBL + ((d - d0) / (d1 - d0)) * (W - LBL - R), H = TOP + rows.length * RHt + 4, HB = H + 18;
    const at = new Map(), out = [];
    rows.forEach((id, i) => {
      const y = TOP + i * RHt + RHt / 2 + 4, sel = id === selected;
      out.push(`<g class="row${sel ? ' sel' : ''}">${i % 2 === 0 || sel ? `<rect class="row-bg" x="0" y="${y - RHt / 2 - 4}" width="${W}" height="${RHt}"/>` : ''}
        <text class="row-l" x="8" y="${y + 3.5}" data-sel="${esc(id)}">${esc(id)}</text>${nameOf(id) ? `<text class="row-n" x="66" y="${y + 3.5}">${esc(nameOf(id).slice(0, 20))}</text>` : ''}</g>`);
      outs(id, 'HAS_WORK_ORDER').forEach((w) => at.set(w, [X(day(byId.get(w).date)), y]));
    });
    for (let d = new Date(d0 * 864e5); d.getTime() / 864e5 <= d1; d.setUTCMonth(d.getUTCMonth() + 1, 1)) {
      const first = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 864e5;
      if (first < d0) continue;
      const x = X(first);
      out.push(`<line class="grid" x1="${x}" y1="${TOP - 4}" x2="${x}" y2="${H}"/><text class="tick" x="${x + 4}" y="${TOP - 12}">${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}</text>`);
    }
    out.push(`<line class="ax" x1="${LBL}" y1="${TOP - 4}" x2="${W - R}" y2="${TOP - 4}"/>`);
    if (incDay) {
      const x = X(incDay), l = incList[0];
      out.push(`<line class="now" x1="${x}" y1="${TOP - 4}" x2="${x}" y2="${H + 2}"/><text class="now-l" x="${x}" y="${H + 15}" text-anchor="middle">${esc(l ? l.incident_id : 'Alert')} · ${fmt(new Date(incDay * 864e5).toISOString()).dm}</text>`);
    }
    G.edges.filter((e) => LINK[e.type] && at.has(e.from) && at.has(e.to)).forEach((e) => {
      const [x1, y1] = at.get(e.from), [x2, y2] = at.get(e.to), qy = Math.min(y1, y2) - 10 - Math.min(18, Math.abs(x1 - x2) * 0.06) - Math.abs(y1 - y2) * 0.1;
      out.push(`<path class="arc" d="M${x1},${y1 - 5}Q${(x1 + x2) / 2},${qy} ${x2},${y2 - 5}"><title>${esc(e.from)} ${esc(LINK[e.type][0].toLowerCase())} ${esc(e.to)}</title></path>`);
    });
    const placed = {};
    [...at].sort((a, b) => a[1][0] - b[1][0]).forEach(([w, [x, y]]) => {
      const n = byId.get(w), open = n.work_status !== 'closed', lw = w.length * 5.8;
      const taken = placed[y] || (placed[y] = [[], []]);
      const lvl = taken[0].some((p) => Math.abs(p - x) < lw + 4) ? 1 : 0;
      taken[lvl].push(x);
      const cms = outs(w, 'COMMITTED_TO');
      const dots = cms.map((c, k) => `<circle class="c-${esc(byId.get(c).work_status)}" cx="${x + (k - (cms.length - 1) / 2) * 7}" cy="${y + 11}" r="2.6"/>`).join('');
      out.push(`<g class="m${open ? ' open' : ''}" data-rec="${esc(w)}"><title>${esc(w)} · ${esc(n.date)} · ${esc(n.work_type)} · ${esc((WO_ST[n.work_status] || [n.work_status])[0])}\n${esc(n.display_name)}</title>
        <rect x="${x - 5}" y="${y - 5}" width="10" height="10"/>${dots}<text x="${x}" y="${y - 9 - lvl * 11}" text-anchor="middle">${esc(w)}</text></g>`);
    });
    el.setAttribute('viewBox', `0 0 ${W} ${HB}`);
    el.style.height = `${HB}px`;
    el.innerHTML = out.join('');
  }

  // Records of the selected item, oldest first, each work order with its field report and commitments.
  function renderStory(body) {
    const t = typeOf(selected);
    const recs = (t === 'Document' ? ins(selected, 'REFERENCES') : t === 'WorkOrder' ? [selected] : outs(selected, 'HAS_WORK_ORDER'))
      .filter((w) => byId.has(w)).sort((a, b) => day(byId.get(a).date) - day(byId.get(b).date));
    const loose = ins(selected, 'ON_ASSET').filter((f) => typeOf(f) === 'FieldReport' && !outs(f, 'DOCUMENTS').length);
    const head = `<p class="note" style="margin:0 0 12px">${t === 'Document' ? `Work orders that cite <b>${esc(selected)}</b>` : `Maintenance records of <b>${esc(selected)}</b>`}${recs.length ? ` · ${plural(recs.length, 'work order')}, oldest first` : ''}</p>`;
    if (!recs.length && !loose.length) {
      const what = t === 'Sensor' ? `Instruments carry no work orders here. ${esc(selected)} measures ${outs(selected, 'MONITORS').map((x) => link(x)).join(', ') || 'nothing linked'}.`
        : t === 'Camera' ? `Cameras carry no work orders here. ${esc(selected)} can observe ${outs(selected, 'CAN_OBSERVE').map((x) => link(x)).join(', ')}.`
        : `No work orders for ${esc(selected)} in the knowledge graph.`;
      body.innerHTML = `<div class="note">${what}</div>`;
      return;
    }
    body.innerHTML = `${head}<ol class="story">${recs.map(card).join('')}${loose.map((f) => `<li class="rec"><div class="when">${fmt(byId.get(f).date).dm}<span>${fmt(byId.get(f).date).y}</span></div><div class="card">${frBlock(f, true)}</div></li>`).join('')}</ol>`;
  }

  function frBlock(f, open) {
    const n = byId.get(f), on = outs(f, 'ON_ASSET');
    return `<details class="fr"${open ? ' open' : ''}><summary><b>${esc(f)}</b>${esc(n.display_name)}</summary>
      <blockquote>${esc(n.excerpt)}${String(n.excerpt || '').length >= 380 ? '…' : ''}</blockquote>
      <div class="who">${esc(n.author_role || '')}${on.length ? ` · covers ${on.map((x) => link(x)).join(', ')}` : ''}</div></details>`;
  }

  function card(w) {
    const n = byId.get(w), d = fmt(n.date), open = n.work_status !== 'closed';
    const lk = G.edges.filter((e) => LINK[e.type] && (e.from === w || e.to === w))
      .map((e) => (e.from === w ? `${LINK[e.type][0]} <button class="linkish" data-rec="${esc(e.to)}">${esc(e.to)}</button>` : `${LINK[e.type][1]} <button class="linkish" data-rec="${esc(e.from)}">${esc(e.from)}</button>`));
    const docs = outs(w, 'REFERENCES').map((x) => link(x));
    const cms = outs(w, 'COMMITTED_TO').map((c) => {
      const s = byId.get(c).work_status, by = outs(c, 'FULFILLED_BY'), col = (CM_ST[s] || [0, 'ink-dim'])[1];
      return `<li><i style="background:var(--${col})"></i><span>${esc(byId.get(c).display_name)}</span><span class="s" style="color:var(--${col})">${esc((CM_ST[s] || [s])[0])}</span>
        <span class="by">${esc(c)}${by.length ? ` · fulfilled by ${by.map((x) => `<button class="linkish" data-rec="${esc(x)}">${esc(x)}</button>`).join(', ')}` : ''}</span></li>`;
    });
    const asset = ins(w, 'HAS_WORK_ORDER')[0];
    const other = asset && asset !== selected ? ` · on ${link(asset)}` : '';
    return `<li class="rec${open ? ' open' : ''}${hlRecord === w ? ' hl' : ''}" id="rec-${esc(w)}"><div class="when">${d.dm}<span>${d.y}</span></div>
      <div class="card"><div class="card-top"><span class="wid">${esc(w)}</span><span class="wt">${esc(n.work_type)}${other}</span>${st(WO_ST, n.work_status)}</div>
      <h3>${esc(n.display_name)}</h3>
      <dl>${n.finding ? `<dt>Finding</dt><dd>${esc(n.finding)}</dd>` : ''}${n.action ? `<dt>Action</dt><dd>${esc(n.action)}</dd>` : ''}${n.follow_up ? `<dt>Follow-up</dt><dd>${esc(n.follow_up)}</dd>` : ''}</dl>
      ${cms.length ? `<ul class="cm">${cms.join('')}</ul>` : ''}
      ${ins(w, 'DOCUMENTS').map((f) => frBlock(f, false)).join('')}
      ${lk.length || docs.length ? `<div class="links">${lk.map((x) => `<span>${x}</span>`).join('')}${docs.length ? `<span>Cites ${docs.join(', ')}</span>` : ''}</div>` : ''}</div></li>`;
  }

  // What the Locate agent cited for each candidate, from GET /incidents/:id/candidates.
  function renderEvidence(body, I) {
    const c = I.c || { candidates: [] }, { badge } = window.AtelicShell;
    const src = (id) => (!id ? '' : ['WorkOrder', 'Commitment', 'FieldReport'].includes(typeOf(id)) ? `<button class="linkish" data-rec="${esc(id)}">${esc(id)}</button>` : byId.has(id) ? link(id) : esc(id));
    const shown = (p) => (['model_estimated', 'observed', 'experimental_setpoint', 'provider_reported', 'agent_drafted'].includes(p) ? badge(p) : '');
    const row = (k, e, cls) => `<li><span class="ev-k ${cls || ''}">${k}</span><span>${esc(e.label)} ${shown(e.provenance)}${e.source_id ? `<span class="from">from ${src(e.source_id)}</span>` : ''}</span></li>`;
    const cands = [...c.candidates].sort((a, b) => (b.asset_id === I.source) - (a.asset_id === I.source));
    body.innerHTML = `${I.l.dismissal ? `<div class="closed-note">Closed as a false alarm by <b>${esc(I.l.dismissal.by)}</b> (${esc(I.l.dismissal.reason_label || I.l.dismissal.reason)}). The evidence below is what the agents had cited.</div>`
        : closedByEngine(I.l) ? '<div class="closed-note">This incident is closed. The evidence below is what the agents cited before the repair.</div>' : ''}
      <p class="note" style="margin:0 0 12px">${typeof c.investigation_summary === 'string' && c.investigation_summary ? esc(c.investigation_summary) : `What the agents cited for each source candidate of <b>${esc(I.l.incident_id)}</b>, most likely first.`}</p>
      <div class="ev-grid">${cands.map((x) => {
        const s = x.asset_id === I.source;
        const items = [...(x.supporting || []).map((e) => row('For', e, 'for')),
          ...(x.contradicting || []).map((e) => (/^gap/i.test(e.label) ? row('Gap', e, 'gap') : row('Against', e, 'against'))),
          ...(x.missing || []).map((m) => row('Missing', { label: typeof m === 'string' ? m : m.label || JSON.stringify(m) }, 'gap'))];
        return `<div class="ev-cand${s ? ' src' : ''}"><div class="ev-h">${link(x.asset_id)}<span class="note">${esc(nameOf(x.asset_id))}</span>
          ${s ? `<span class="st warn" style="margin-left:0">Likely source</span>${badge('hypothesis')}` : '<span class="st" style="margin-left:0">Candidate</span>'}
          <span class="why">${(x.supporting || []).length} for · ${(x.contradicting || []).length} against</span></div>
          <ul class="ev-list">${items.join('') || '<li><span class="ev-k">—</span><span class="note">No evidence listed.</span></li>'}</ul></div>`;
      }).join('') || '<div class="note">The agents have not listed candidates yet.</div>'}</div>`;
  }

  // ===== inspector: the selected relationship, or the selected node =====
  function renderSide() {
    const { badge } = window.AtelicShell;
    fadeIn($('i-head'), $('i-stats'), $('detail'));
    if (selEdge != null && G.edges[selEdge]) {
      const e = G.edges[selEdge];
      $('i-head').innerHTML = `<div class="i-ty">Relationship<button class="linkish x" data-clear-edge>Close</button></div>
        <div class="i-id" style="font-size:16px">${esc(e.type)}</div><div class="i-nm">${esc(REL[e.type] || '')}</div>
        <div class="conn-path">${link(e.from)}<span class="ar">→</span>${link(e.to)}</div>`;
      $('i-stats').innerHTML = '';
      $('detail').innerHTML = `<h4>From</h4><div class="note">${esc(e.from)} · ${esc(nameOf(e.from) || TYPE_NAME[typeOf(e.from)] || typeOf(e.from))}</div>
        <h4>To</h4><div class="note">${esc(e.to)} · ${esc(nameOf(e.to) || TYPE_NAME[typeOf(e.to)] || typeOf(e.to))}</div>
        <h4>Where it comes from</h4><div class="kv"><div class="k">Source</div><div class="v">${esc(e.source)}</div><div class="k">Method</div><div class="v">${esc(METHOD[e.method] || e.method)}</div>
        ${e.mapping_version ? `<div class="k">Mapping version</div><div class="v">${esc(e.mapping_version)}</div>` : ''}</div>
        <p class="note" style="margin-top:12px">${badge(e.provenance)}</p>`;
      return;
    }
    if (!selected || !byId.has(selected)) return;
    const n = byId.get(selected), t = n.asset_type, I = curInc();
    const chips = [];
    if (I && I.cands.has(selected) && !I.l.dismissal) chips.push(I.source === selected ? `<span class="st warn" style="margin-left:0">Likely source · ${esc(I.l.incident_id)}</span>${badge('hypothesis')}` : `<span class="st" style="margin-left:0">Candidate · ${esc(I.l.incident_id)}</span>`);
    if (t === 'Camera' && camAlarm(selected)) chips.push('<span class="st bad" style="margin-left:0">Alarm</span>');
    if (t === 'WorkOrder') chips.push(st(WO_ST, n.work_status, 'ml0'));
    if (t === 'Commitment') chips.push(st(CM_ST, n.work_status, 'ml0'));
    const area = ins(selected, 'CONTAINS')[0], nb = [...new Set(nbrs(selected).filter((x) => byId.has(x)))];
    const onCanvas = cy && cy.getElementById(selected).nonempty();
    const off = nb.filter((x) => !cy || cy.getElementById(x).empty()).filter(allowed).length;
    $('i-head').innerHTML = `<div class="i-ty"><img alt="" src="${nodeIcon(t)}" width="16" height="16">${esc(TYPE_NAME[t] || t)}${area ? ` · ${esc(area)}` : ''}</div><div class="i-id">${esc(selected)}</div>${nameOf(selected) ? `<div class="i-nm">${esc(nameOf(selected))}</div>` : ''}
      ${chips.length ? `<div class="i-chips">${chips.join('')}</div>` : ''}
      <div class="i-acts">${mode === 'focus' && onCanvas && !extra.size ? '' : '<button class="tool" data-focus>Focus here</button>'}
        <button class="tool" data-expand${off ? '' : ' disabled title="Its neighbours of the shown types are on the graph"'}>Expand${off ? ` +${off}` : ''}</button>
        ${onCanvas && selected !== (I || {}).source ? '<button class="tool" data-hide>Hide</button>' : ''}</div>`;

    const li = (dir, id, xtra) => `<li><span class="dir">${dir}</span><span>${link(id)} <span class="mute">${esc(nameOf(id))}</span>${xtra || ''}</span></li>`;
    const sec = (title, items) => (items.length ? `<h4>${title}<span class="n">${items.length}</span></h4><ul>${items.join('')}</ul>` : '');
    const pipeOf = (c) => (c.pipe && c.pipe !== selected ? ` <span class="mute">via</span> ${link(c.pipe)}` : '');
    const piping = [
      ...conns.filter((c) => c.b === selected).map((c) => li('Upstream', c.a, pipeOf(c))),
      ...conns.filter((c) => c.a === selected).map((c) => (c.b ? li('Downstream', c.b, pipeOf(c)) : `<li><span class="dir">Line out</span><span>${link(c.pipe)} <span class="mute">leaves this unit</span></span></li>`)),
      ...conns.filter((c) => c.pipe === selected).flatMap((c) => [li('From', c.a), ...(c.b ? [li('To', c.b)] : [])]),
    ];
    const inst = ins(selected, 'MONITORS').map((s) => li('Measured by', s, byId.get(s).measures ? ` · ${esc(byId.get(s).measures)}` : ''));
    const monitors = outs(selected, 'MONITORS').map((x) => li('Monitors', x));
    const parts = [...outs(selected, 'PART_OF').map((x) => li('Part of', x)), ...ins(selected, 'PART_OF').map((x) => li('Has part', x))];
    const cam = [...ins(selected, 'CAN_OBSERVE').map((x) => li('Seen by', x)), ...outs(selected, 'CAN_OBSERVE').map((x) => li('Can observe', x))];
    const wos = outs(selected, 'HAS_WORK_ORDER'), notClosed = wos.filter((w) => byId.get(w).work_status !== 'closed');
    const pending = wos.flatMap((w) => outs(w, 'COMMITTED_TO')).filter((c) => byId.get(c).work_status !== 'done');
    const maint = pending.map((c) => {
      const s = byId.get(c).work_status, col = (CM_ST[s] || [0, 'ink-dim'])[1];
      return `<li><span class="dir" style="color:var(--${col})"><i class="cmi" style="background:var(--${col})"></i>${esc((CM_ST[s] || [s])[0])}</span><span>${esc(byId.get(c).display_name)} <span class="mute">· ${link(c)}</span></span></li>`;
    });
    const recLinks = [...(t === 'WorkOrder' ? [...ins(selected, 'HAS_WORK_ORDER').map((x) => li('On asset', x)), ...outs(selected, 'COMMITTED_TO').map((x) => li('Commits to', x)), ...ins(selected, 'DOCUMENTS').map((x) => li('Field report', x))] : []),
      ...(t === 'Commitment' ? [...ins(selected, 'COMMITTED_TO').map((x) => li('From', x)), ...outs(selected, 'FULFILLED_BY').map((x) => li('Fulfilled by', x))] : []),
      ...(t === 'FieldReport' ? [...outs(selected, 'DOCUMENTS').map((x) => li('Documents', x)), ...outs(selected, 'ON_ASSET').map((x) => li('On asset', x))] : []),
      ...(t === 'IncidentRecord' ? [...outs(selected, 'CONCERNS').map((x) => li('Concerns', x)), ...outs(selected, 'CONSIDERED').map((x) => li('Considered', x))] : []),
      ...G.edges.filter((e) => LINK[e.type] && (e.from === selected || e.to === selected)).map((e) => (e.from === selected ? li(LINK[e.type][0], e.to) : li(LINK[e.type][1], e.from)))];
    const past = PAST.filter((h) => h.asset_id === selected).map((h) => `<li><span class="dir">${esc(String(h.opened_utc).slice(0, 10))}</span><span><a class="linkish" href="incidents.html?id=${encodeURIComponent(h.incident_id)}">${esc(h.incident_id)}</a> <span class="mute">· ${esc(h.outcome_label)}</span></span></li>`);
    const docs = [...outs(selected, 'HAS_DOCUMENT'), ...(t === 'WorkOrder' ? outs(selected, 'REFERENCES') : [])].map((d) => li(esc((byId.get(d) || {}).doc_kind || 'Document'), d));
    const usedBy = t === 'Document' ? ins(selected, 'HAS_DOCUMENT').map((x) => li('Used by', x)) : [];
    const contains = outs(selected, 'CONTAINS');
    const nrel = G.edges.filter((e) => e.from === selected || e.to === selected).length;

    $('i-stats').innerHTML = EQUIP.has(t) || t === 'PipeSegment'
      ? `<div class="i-stats"><div><b>${nb.length}</b><span>Linked nodes</span></div><div><b>${wos.length}</b><span>Work orders</span>${notClosed.length ? `<em>${notClosed.length} not closed</em>` : ''}</div>
        <div><b class="${pending.length ? 'warn' : ''}">${pending.length}</b><span>Commitments due</span></div></div>`
      : `<div class="i-stats"><div><b>${nb.length}</b><span>Linked nodes</span></div><div><b>${nrel}</b><span>Relationships</span></div><div><b>${n.date ? esc(fmt(n.date).dm) : '—'}</b><span>${n.date ? esc(fmt(n.date).y) : 'Date'}</span></div></div>`;

    const facts = [['Type', TYPE_NAME[t] || t]];
    if (n.measures) facts.push(['Measures', n.measures]);
    if (n.area) facts.push(['Area', n.area]);
    if (n.doc_kind) facts.push(['Kind', n.doc_kind]);
    if (n.work_type) facts.push(['Work type', n.work_type]);
    if (n.date) facts.push(['Date', String(n.date).slice(0, 10)]);
    if (Array.isArray(n.position_m)) facts.push(['Position (m)', n.position_m.join(' · ')]);
    facts.push(['Source', n.source], ['Mapping version', n.mapping_version]);
    const es = G.edges.filter((e) => e.from === selected || e.to === selected);
    $('detail').innerHTML = `${n.finding ? `<h4>Finding</h4><div class="note" style="color:var(--ink)">${esc(n.finding)}</div>` : ''}${n.excerpt ? `<h4>Excerpt</h4><div class="note" style="color:var(--ink)">${esc(n.excerpt)}</div>` : ''}
      ${sec('Piping', piping)}${sec('Instruments', inst)}${sec('Monitors', monitors)}${sec('Weather station', parts)}${sec('Camera', cam)}${sec('Linked records', recLinks)}
      ${wos.length ? `<h4>Open commitments<span class="n">${pending.length}</span></h4>${maint.length ? `<ul>${maint.join('')}</ul>` : '<div class="note">All commitments are done.</div>'}
        <div class="more">${lane === 'story' ? '<span class="note">Work orders are shown in Records below.</span>' : `<button class="linkish" data-lane="story">Open ${plural(wos.length, 'work order')} in Records ↓</button>`}</div>` : ''}
      ${sec('Earlier incidents', past)}${sec('Documents', docs)}${sec('Used by', usedBy)}
      ${contains.length ? `<h4>Contains<span class="n">${contains.length}</span></h4><div class="note">${contains.map((x) => link(x)).join(', ')}</div>` : ''}
      <h4>Details</h4><div class="kv">${facts.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v == null ? '<span style="color:var(--ink-dim)">Not available</span>' : esc(v)}</div>`).join('')}</div>
      ${es.length ? `<p class="note" style="margin-top:12px">${plural(es.length, 'relationship')} from ${[...new Set(es.map((e) => `${e.source} · ${METHOD[e.method] || e.method}`))].map(esc).join('; ')}. ${badge(n.provenance)}</p>` : ''}`;
  }

  // ===== state =====
  function syncUrl() {
    params.set('view', mode);
    if (selected) params.set('asset', selected); else params.delete('asset');
    if (mode === 'incident' && showInc) params.set('show', showInc); else params.delete('show');
    try { history.replaceState(null, '', `?${params}`); } catch (e) { /* ignore */ }
  }
  function renderAll(how) {
    renderBar(); draw(how); renderCtx(); renderLane(); renderSide();
    if (picker) picker.set(selected || 'none');
  }
  // A record picked from the lists opens on its asset with the card highlighted below.
  const recordOf = (id) => { const t = typeOf(id); return t === 'WorkOrder' ? id : t === 'Commitment' ? ins(id, 'COMMITTED_TO')[0] : t === 'FieldReport' ? outs(id, 'DOCUMENTS')[0] : null; };
  const scrollToRecord = () => requestAnimationFrame(() => { const c = hlRecord && document.getElementById(`rec-${hlRecord}`); if (c) c.scrollIntoView({ behavior: 'smooth', block: 'center' }); });
  function select(id, { keepView, record } = {}) {
    if (!byId.has(id)) return;
    selected = id; selEdge = null; hlRecord = null;
    const wo = record ? recordOf(id) : null;
    if (wo) { selected = ins(wo, 'HAS_WORK_ORDER')[0] || wo; hlRecord = wo; lane = 'story'; }
    syncUrl();
    const onCanvas = cy.getElementById(selected).nonempty();
    if (keepView && onCanvas) {
      // clicking in the graph keeps the layout; only the selection and the panels change
      cy.batch(() => {
        cy.nodes().removeClass('sel named'); cy.getElementById(selected).addClass('sel named'); cy.edges().unselect();
        if (mode === 'focus') { cy.edges().removeClass('lab').addClass('peri'); cy.getElementById(selected).connectedEdges().addClass('lab').removeClass('peri'); }
      });
      const n = cy.getElementById(selected), ext = cy.extent(), p = n.position();
      if (p.x < ext.x1 || p.x > ext.x2 || p.y < ext.y1 || p.y > ext.y2) cy.animate({ center: { eles: n } }, { duration: 400, easing: EASE });
      if (lane === 'history' && mode === 'focus') lane = 'story';
      renderBar(); renderCtx(); renderLane(); renderSide();
      if (picker) picker.set(selected);
    } else {
      mode = 'focus'; extra.clear(); hidden.clear();
      if (lane === 'history') lane = 'story';
      renderAll('full');
    }
    if (hlRecord) scrollToRecord();
  }
  function setMode(m) {
    if (m === 'incident') { showInc = showInc || (incList[0] || {}).incident_id; if (!showInc) return; if (curInc().source) selected = curInc().source; }
    if (m === 'focus' && !selected) return;
    mode = m; lane = LANE_OF[m]; hlRecord = null; selEdge = null; extra.clear(); hidden.clear();
    syncUrl(); renderAll('full');
  }

  document.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-clear-edge]')) { selEdge = null; cy.edges().unselect(); renderSide(); return; }
    if (ev.target.closest('[data-reset]')) { extra.clear(); hidden.clear(); renderAll('full'); return; }
    if (ev.target.closest('[data-focus]')) { mode = 'focus'; extra.clear(); hidden.clear(); lane = 'story'; syncUrl(); renderAll('full'); return; }
    const ex = ev.target.closest('[data-expand]');
    if (ex && !ex.disabled) { expand(selected); return; }
    if (ev.target.closest('[data-hide]')) { hidden.add(selected); extra.delete(selected); draw('none'); renderCtx(); renderSide(); return; }
    const g = ev.target.closest('[data-group]');
    if (g) { const k = g.dataset.group; if (shownGroups.has(k)) shownGroups.delete(k); else shownGroups.add(k); renderBar(); draw(mode === 'focus' ? 'full' : 'grow'); renderCtx(); renderSide(); return; }
    const dp = ev.target.closest('[data-depth]');
    if (dp) { depth = Number(dp.dataset.depth); extra.clear(); renderBar(); draw('full'); renderCtx(); renderSide(); return; }
    if (ev.target.closest('#relayout')) { cy.nodes().scratch('placed', false); layout('full'); return; }
    const m = ev.target.closest('[data-mode]');
    if (m && !m.disabled) { setMode(m.dataset.mode); return; }
    const ln = ev.target.closest('[data-lane]');
    if (ln && !ln.disabled) { lane = ln.dataset.lane; renderLane(); renderSide(); if (ln.closest('#detail')) $('tabs').scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    const r = ev.target.closest('[data-rec]');
    if (r) { select(r.getAttribute('data-rec'), { record: true }); return; }
    const s = ev.target.closest('[data-sel]');
    if (s) select(s.getAttribute('data-sel'), { keepView: true });
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && selEdge != null) { selEdge = null; cy.edges().unselect(); renderSide(); }
  });

  function renderPickers() {
    const order = ['Vessel', 'Compressor', 'Valve', 'Flange', 'Vent', 'PipeSegment', 'Sensor', 'Camera', 'EquipmentArea', 'WorkOrder', 'Commitment', 'FieldReport', 'Document', 'IncidentRecord'];
    const types = [...new Set(G.nodes.map((n) => n.asset_type))].sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
    picker = window.AtelicShell.dropdown($('pick'), {
      label: 'Find in graph', search: 'Find by ID or name…', value: selected || 'none',
      options: [{ value: 'none', label: 'Find…', sub: `${G.nodes.length} nodes` }, ...types.flatMap((t) => G.nodes.filter((n) => n.asset_type === t)
        .map((n) => ({ value: n.asset_id, label: n.asset_id, sub: n.display_name !== n.asset_id ? n.display_name : '', group: TYPE_NAME[t] || t })))],
      onChange: (v) => { if (v !== 'none') select(v); },
    });
    if (incList.length > 1) {
      window.AtelicShell.dropdown($('inc-pick'), {
        label: 'Incident', value: showInc || incList[0].incident_id,
        options: incList.map((l) => ({ value: l.incident_id, label: l.incident_id, color: l.dismissal || closedByEngine(l) ? '--good' : '--warn', sub: `${l.camera_id} · ${incSets[l.incident_id].nodes.size} related` })),
        onChange: (v) => { showInc = v; setMode('incident'); },
      });
    }
  }

  async function init() {
    const { get, INCIDENT_ID, badge, backBar } = window.AtelicShell;
    backBar(params.get('asset') ? ` · <b>${esc(params.get('asset'))}</b>` : '');
    try {
      G = await get(`/incidents/${INCIDENT_ID}/graph`);
    } catch (e) {
      $('cy').outerHTML = '<div class="cy-empty">Knowledge graph data not available.</div>';
      return;
    }
    if (!window.cytoscape) { $('cy').outerHTML = '<div class="cy-empty">The graph viewer could not load.</div>'; return; }
    byId = new Map(G.nodes.map((n) => [n.asset_id, n]));
    flow();
    const [list, status] = await Promise.all([get('/incidents').catch(() => ({ live: [], history: null })), get('/status').catch(() => null)]);
    PAST = list.history ? list.history.incidents : [];
    incList = (list.live || []).filter((l) => l.triggered);
    const start = status && status.replay_start_utc;
    if (incList.length && start) incDay = day(start);
    const cands = await Promise.all(incList.map((l) => get(`/incidents/${l.incident_id}/candidates`).catch(() => null)));
    incList.forEach((l, i) => {
      const c = cands[i] || { candidates: [] };
      const rel = new Set([l.camera_id, ...(c.related_ids || []), ...c.candidates.flatMap((x) => [x.asset_id, ...[...(x.supporting || []), ...(x.contradicting || [])].map((e) => e.source_id)])]);
      // A cited commitment or field report brings in its work order and asset, so the records connect to the plant.
      [...rel].forEach((id) => {
        const wo = byId.has(id) ? recordOf(id) : null;
        if (wo) { rel.add(wo); ins(wo, 'HAS_WORK_ORDER').forEach((a) => rel.add(a)); }
        if (typeOf(id) === 'FieldReport') outs(id, 'ON_ASSET').forEach((a) => rel.add(a));
      });
      // Sensors cited as a family (CH4-01 for "CH4-01–CH4-05") bring the other methane sensors in the fit.
      c.candidates.forEach((x) => [...(x.supporting || []), ...(x.contradicting || [])].forEach((e) => {
        const m = /([A-Z0-9]+-\d+)\s*[–-]\s*([A-Z0-9]+-\d+)/.exec(e.label || '');
        if (m) G.nodes.filter((n) => n.asset_type === 'Sensor' && n.asset_id >= m[1] && n.asset_id <= m[2]).forEach((n) => rel.add(n.asset_id));
      }));
      // what a cited sensor measures, so no sensor floats on its own
      [...rel].filter((id) => typeOf(id) === 'Sensor').forEach((id) => outs(id, 'MONITORS').forEach((x) => rel.add(x)));
      // the line between two candidates, so the flow path reads
      conns.forEach((k) => { if (k.pipe && rel.has(k.a) && k.b && rel.has(k.b)) rel.add(k.pipe); });
      incSets[l.incident_id] = { l, c, camera: l.camera_id, nodes: new Set([...rel].filter((id) => byId.has(id))),
        cands: new Set(c.candidates.map((x) => x.asset_id)), source: c.assessment && c.assessment.result === 'likely_source' ? c.assessment.asset_id : null };
    });
    const store = G.store === 'neo4j' ? 'Neo4j' : G.store;
    $('stats').textContent = `${G.nodes.length} nodes · ${G.edges.length} relationships`;
    $('src').textContent = `Source: engine knowledge graph (${store})${(G.nodes[0] || {}).mapping_version ? ` · mapping v${G.nodes[0].mapping_version}` : ''}`;
    $('store').innerHTML = `Read from the engine's knowledge graph (${esc(store)}). ${badge(G.nodes.some((n) => n.provenance !== 'synthetic') ? 'pending' : 'synthetic')}`;

    // Where to start: ?show / ?view=incident → incident; ?view=site → whole graph; ?asset → focus;
    // nothing asked and this tab's incident is open → incident; otherwise focus on the likely source (or the first equipment).
    const v = params.get('view'), want = params.get('show') || (v === 'incident' ? INCIDENT_ID : null);
    const open = incList.find((l) => l.incident_id === INCIDENT_ID && !l.dismissal && !closedByEngine(l));
    const a = params.get('asset');
    if (a && byId.has(a)) { const wo = recordOf(a); selected = wo ? ins(wo, 'HAS_WORK_ORDER')[0] || a : a; hlRecord = wo; }
    if (want && incSets[want]) { showInc = want; mode = 'incident'; }
    else if (v === 'site') mode = 'site';
    else if (a && byId.has(a)) mode = 'focus';
    else if (open && v !== 'focus') { showInc = open.incident_id; mode = 'incident'; }
    else mode = 'focus';
    if (mode === 'incident' && incSets[showInc].source) selected = selected || incSets[showInc].source;
    if (!selected) selected = (incList[0] && incSets[incList[0].incident_id].source) || [...flowCol.keys()].sort((x, y) => flowCol.get(x) - flowCol.get(y))[0] || G.nodes[0].asset_id;
    lane = hlRecord ? 'story' : LANE_OF[mode];
    $('canvas').style.height = `${Math.max(440, Math.min(640, window.innerHeight - 330))}px`;
    wireCanvas();
    renderPickers();
    syncUrl();
    renderAll('full');
    if (hlRecord) scrollToRecord();
    let w = 0;
    new ResizeObserver(() => {
      const nw = $('lane').clientWidth;
      if (nw !== w && lane === 'history') { w = nw; renderLane(); }
    }).observe(document.querySelector('.ws-main'));
  }

  document.addEventListener('DOMContentLoaded', init);
})();
