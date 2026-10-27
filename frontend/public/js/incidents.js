// Incidents — every camera alert at the site. Reads GET /incidents: live incidents (state from this
// backend) and the closed history (incidents/history.json, read-only records). ?id=<incident> preselects one.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const { badge } = window.AtelicShell;

  const STAGE = { detect: ['Detect', 'detection.html'], locate: ['Locate', 'locate.html'], quantify: ['Quantify', 'quantify.html'],
    plan: ['Plan', 'plan.html'], approve: ['Approve', 'review.html'], report: ['Report', 'report.html'] };
  const STATUS = { not_started: 'Not started', in_progress: 'In progress', ready_for_review: 'Ready for review', completed: 'Completed', blocked: 'Blocked' };
  const STATUS_KIND = { in_progress: 'good', ready_for_review: 'warn', completed: 'good', blocked: 'bad' };
  const OUTCOME_KIND = { repaired: 'good', no_leak_found: '', false_alarm: '' };
  const DECISION = { approve: 'Approved', request_changes: 'Changes requested', reject: 'Rejected', dismissed: 'Dismissed' };

  let open = [], closed = [], selected = null;
  const params = new URLSearchParams(location.search);
  const when = (iso) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC` : '—');
  const chip = (text, kind, live) => `<span class="chip ${kind || ''}${live ? ' live' : ''}"><i></i>${esc(text)}</span>`;
  const incLink = (l, page) => `${page}?inc=${encodeURIComponent(l.incident_id)}`;

  // Where an open incident stands: a blocked stage, else the furthest stage that has started.
  // (Detect and Locate stay "ready for review" until a named reviewer signs them off, so the
  // first unfinished stage would understate progress.)
  function current(l) {
    if (l.stages.every((s) => s.status === 'completed')) return { text: 'Report filed', kind: 'good', page: 'report.html' };
    const st = l.stages.find((s) => s.status === 'blocked')
      || l.stages.slice().reverse().find((s) => s.status !== 'not_started') || l.stages[0];
    return { text: `${STAGE[st.stage][0]} · ${STATUS[st.status]}`, kind: STATUS_KIND[st.status] || '', page: STAGE[st.stage][1], stage: st.stage };
  }

  function rowAttrs(id) {
    return `tabindex="0" data-id="${esc(id)}" aria-selected="${id === selected}"`;
  }

  function renderOpen() {
    $('open-meta').textContent = `${open.length} open`;
    $('open').innerHTML = open.length ? `<table class="inc"><thead><tr><th>Incident</th><th>Camera</th><th>Likely source</th><th>Emission rate</th><th>Stage</th><th>Opened</th></tr></thead><tbody>
      ${open.map((l) => {
        const c = current(l);
        return `<tr ${rowAttrs(l.incident_id)}>
          <td class="id">${esc(l.incident_id)}</td>
          <td class="mono">${esc(l.camera_id)}</td>
          <td>${l.asset_id ? `<span class="mono">${esc(l.asset_id)}</span>${badge('hypothesis')}` : '<span class="dim">Not located yet</span>'}</td>
          <td>${l.rate_kg_ch4_h != null ? `<span class="mono">${l.rate_kg_ch4_h} kg/h</span>${badge('model_estimated')}` : '<span class="dim">Not estimated</span>'}</td>
          <td>${chip(c.text, c.kind, c.kind === 'warn')}</td>
          <td class="mono mute">${when(l.triggered_at)}</td>
        </tr>`;
      }).join('')}</tbody></table>`
      : '<div class="empty-row">No open incidents. All cameras are watching.</div>';
  }

  function renderClosed() {
    $('closed-meta').textContent = `${closed.length} closed`;
    $('closed').innerHTML = closed.length ? `<table class="inc"><thead><tr><th>Incident</th><th>Camera</th><th>Asset</th><th>Outcome</th><th>Decided by</th><th>Closed</th></tr></thead><tbody>
      ${closed.map((h) => `<tr ${rowAttrs(h.incident_id)}>
          <td class="id">${esc(h.incident_id)}</td>
          <td class="mono">${esc(h.camera_id)}</td>
          <td>${h.asset_id ? `<span class="mono">${esc(h.asset_id)}</span>` : '<span class="dim">None</span>'}</td>
          <td>${chip(h.outcome_label, OUTCOME_KIND[h.outcome])}</td>
          <td class="mute">${esc(h.decision.reviewer)}</td>
          <td class="mono mute">${esc((h.closed_utc || '').slice(0, 10))}</td>
        </tr>`).join('')}</tbody></table>`
      : '<div class="empty-row">No closed incidents on file.</div>';
  }

  const kv = (rows) => `<div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('')}</div>`;

  function renderDetail() {
    const box = $('detail');
    const l = open.find((x) => x.incident_id === selected), h = closed.find((x) => x.incident_id === selected);
    if (l) {
      const c = current(l), rv = l.review;
      box.innerHTML = `
        <div class="d-head"><div class="d-id">${esc(l.incident_id)}</div><div class="d-sub">${chip(c.text, c.kind, c.kind === 'warn')}</div></div>
        <div class="d-body">
          ${kv([
            ['Camera', `<span class="mono">${esc(l.camera_id)}</span>`],
            ['Opened', `<span class="mono">${when(l.triggered_at)}</span>`],
            ['Likely source', l.asset_id ? `<a href="graph.html?asset=${encodeURIComponent(l.asset_id)}&from=incidents">${esc(l.asset_id)}</a> ${badge('hypothesis')}` : 'Not located yet'],
            ['Emission rate', l.rate_kg_ch4_h != null ? `${l.rate_kg_ch4_h} kg CH₄/h ${badge('model_estimated')}` : 'Not estimated'],
            ['Review', rv ? `${DECISION[rv.decision]} by ${esc(rv.reviewer)}` : 'Not decided'],
          ])}
          <h4>Stages</h4>
          ${kv(l.stages.map((s) => [`<a href="${incLink(l, STAGE[s.stage][1])}" style="font-family:var(--mono);color:var(--accent);text-decoration:none">${STAGE[s.stage][0]}</a>`,
            chip(STATUS[s.status], STATUS_KIND[s.status])]))}
          <p><a class="btn primary" href="${incLink(l, c.page)}">${c.stage ? `Continue at ${STAGE[c.stage][0]}` : 'Open report'} →</a></p>
        </div>`;
      return;
    }
    if (h) {
      const d = h.decision;
      box.innerHTML = `
        <div class="d-head"><div class="d-id">${esc(h.incident_id)}</div><div class="d-sub">${chip(h.outcome_label, OUTCOME_KIND[h.outcome])}</div></div>
        <div class="d-body">
          <p>${esc(h.summary)}</p>
          <h4>Record</h4>
          ${kv([
            ['Camera', `<span class="mono">${esc(h.camera_id)}</span>`],
            ['Asset', h.asset_id ? `<a href="graph.html?asset=${encodeURIComponent(h.asset_id)}&from=incidents">${esc(h.asset_id)}</a>` : 'None (no release found)'],
            ['Opened', `<span class="mono">${when(h.opened_utc)}</span>`],
            ['Closed', `<span class="mono">${when(h.closed_utc)}</span>`],
            ['Decision', `${DECISION[d.decision] || esc(d.decision)} by ${esc(d.reviewer)}${d.role ? `, ${esc(d.role)}` : ''}`],
            ...(d.reason ? [['Reason', esc(d.reason)]] : []),
            ['Work order', h.work_order ? `<a href="graph.html?asset=${encodeURIComponent(h.work_order)}&from=incidents">${esc(h.work_order)}</a>` : 'None'],
            ['Emission rate', 'Not recorded'],
          ])}
          <h4>Timeline</h4>
          <ul class="tl">${(h.timeline || []).map((t) => `<li>${esc(t.event)}<small>${when(t.at_utc)}</small></li>`).join('')}</ul>
        </div>
        <div class="d-foot">Closed record. Read-only.</div>`;
      return;
    }
    box.innerHTML = '<div class="d-empty">Select an incident to see what happened.</div>';
  }

  function select(id) {
    selected = id;
    params.set('id', id);
    try { history.replaceState(null, '', `?${params}`); } catch (e) { /* ignore */ }
    document.querySelectorAll('tr[data-id]').forEach((r) => r.setAttribute('aria-selected', String(r.dataset.id === id)));
    renderDetail();
  }

  document.addEventListener('click', (e) => {
    const r = e.target.closest('tr[data-id]');
    if (r && !e.target.closest('a')) select(r.dataset.id);
  });
  document.addEventListener('keydown', (e) => {
    const r = e.target.closest && e.target.closest('tr[data-id]');
    if (r && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(r.dataset.id); }
  });

  async function init() {
    try {
      const d = await window.AtelicShell.get('/incidents');
      // An incident is open once its camera raised the alert; a camera with no alert is just watching.
      open = d.live.filter((l) => l.triggered);
      closed = d.history ? d.history.incidents.slice().sort((a, b) => b.opened_utc.localeCompare(a.opened_utc)) : [];
    } catch (e) {
      console.error(e);
      $('open').innerHTML = '<div class="empty-row">Incidents are unavailable: backend is not reachable.</div>';
      $('closed').innerHTML = '';
      return;
    }
    $('counts').textContent = `${open.length} open · ${closed.length} closed`;
    const want = params.get('id');
    selected = [...open, ...closed].some((x) => x.incident_id === want) ? want : (open[0] || closed[0] || {}).incident_id || null;
    renderOpen();
    renderClosed();
    renderDetail();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
