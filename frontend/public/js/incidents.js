// Incidents — every camera alert at the site. Reads GET /incidents: live incidents (state from this
// backend), closed incidents remembered in the knowledge graph, and the station's work-order history from the graph. ?id=<incident> preselects one.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const { badge } = window.AtelicShell;

  const { incHref, whereNow, INCIDENT_ID } = window.AtelicShell;
  // The incident this tab is working on (the one the incident screens show).
  const activeTag = (id) => (id === INCIDENT_ID ? '<span class="cur-tag" title="The incident screens in this tab show this incident">Active</span>' : '');
  const STAGE = { detect: ['Detect', 'detection'], locate: ['Locate', 'locate'], quantify: ['Quantify', 'quantify'],
    plan: ['Plan', 'plan'], approve: ['Approve', 'review'], report: ['Report', 'report'] };
  const STATUS = { not_started: 'Not started', in_progress: 'In progress', ready_for_review: 'Ready for review', completed: 'Completed', blocked: 'Blocked' };
  const STATUS_KIND = { in_progress: 'good', ready_for_review: 'warn', completed: 'good', blocked: 'bad' };
  const OUTCOME_KIND = { repaired: 'good', no_leak_found: '', false_alarm: '' };
  const DECISION = { approve: 'Approved', request_changes: 'Changes requested', reject: 'Rejected', dismissed: 'Dismissed' };

  // Commitment status as recorded in the graph; open/not done are the ones still waiting.
  const COMMIT = { done: ['Done', 'good'], open: ['Open', 'warn'], not_done: ['Not done', 'bad'], deferred: ['Deferred', 'warn'] };
  let open = [], closed = [], past = [], selected = null;
  const params = new URLSearchParams(location.search);
  const when = (iso) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC` : '—');
  const chip = (text, kind, live) => `<span class="chip ${kind || ''}${live ? ' live' : ''}"><i></i>${esc(text)}</span>`;
  const incLink = (l, page) => incHref(page, l.incident_id);

  // Where an open incident stands (same rule as the top-bar switcher and the context strip).
  function current(l) {
    const w = whereNow(l);
    return { text: w.text, kind: { open: STATUS_KIND[w.status] || 'warn', bad: 'bad', closed: 'good' }[w.kind] || '', page: w.page, stage: w.stage };
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
          <td class="id">${esc(l.incident_id)}${activeTag(l.incident_id)}</td>
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
          <td class="id">${esc(h.incident_id)}${activeTag(h.incident_id)}</td>
          <td class="mono">${esc(h.camera_id)}</td>
          <td>${h.asset_id ? `<span class="mono">${esc(h.asset_id)}</span>` : '<span class="dim">None</span>'}</td>
          <td>${chip(h.outcome_label, OUTCOME_KIND[h.outcome])}</td>
          <td class="mute">${esc(h.decision.reviewer)}</td>
          <td class="mono mute">${esc((h.closed_utc || '').slice(0, 10))}</td>
        </tr>`).join('')}</tbody></table>`
      : '<div class="empty-row">No closed incidents on file.</div>';
  }

  function renderPast() {
    $('past-meta').textContent = `${past.length} work orders`;
    $('past').innerHTML = past.length ? `<table class="inc"><thead><tr><th>Work order</th><th>Date</th><th>Asset</th><th>What happened</th><th>Follow-ups</th></tr></thead><tbody>
      ${past.map((w) => {
        const waiting = w.commitments.filter((c) => c.status !== 'done').length;
        return `<tr ${rowAttrs(w.id)}>
          <td class="id">${esc(w.id)}</td>
          <td class="mono mute">${esc(w.date)}</td>
          <td>${w.asset_id ? `<span class="mono">${esc(w.asset_id)}</span>` : '<span class="dim">Site</span>'}</td>
          <td class="wrap">${esc(w.issue)}<small class="wt">${esc(w.work_type)}</small></td>
          <td>${w.commitments.length ? chip(waiting ? `${waiting} open` : 'All done', waiting ? 'warn' : 'good') : '<span class="dim">None</span>'}</td>
        </tr>`;
      }).join('')}</tbody></table>`
      : '<div class="empty-row">No station history in the knowledge graph.</div>';
  }

  function renderPastDetail(w) {
    const fr = w.field_report;
    return `
      <div class="d-head"><div class="d-id">${esc(w.id)}</div><div class="d-sub">${chip(w.work_type, '')} ${chip(w.status === 'closed' ? 'Closed' : w.status, w.status === 'closed' ? 'good' : 'warn')}</div></div>
      <div class="d-body">
        <p><b>${esc(w.issue)}</b></p>
        ${kv([
          ['Date', `<span class="mono">${esc(w.date)}${w.time_utc ? ` ${esc(w.time_utc)} UTC` : ''}</span>`],
          ['Asset', w.asset_id ? `<a href="graph.html?asset=${encodeURIComponent(w.asset_id)}&from=incidents">${esc(w.asset_id)}</a>` : 'Site'],
          ...(w.finding ? [['Finding', esc(w.finding)]] : []),
          ...(w.action ? [['Action', esc(w.action)]] : []),
          ...(w.document_id ? [['Reference', `<span class="mono">${esc(w.document_id)}</span>`]] : []),
        ])}
        ${w.commitments.length ? `<h4>Commitments</h4><ul class="cm">${w.commitments.map((c) => {
          const [t, k] = COMMIT[c.status] || [c.status, ''];
          return `<li>${chip(t, k)}<span><span class="mono">${esc(c.id)}</span> ${esc(c.text)}${c.fulfilled_by ? ` · done in <span class="mono">${esc(c.fulfilled_by)}</span>` : ''}</span></li>`;
        }).join('')}</ul>` : ''}
        ${w.related.length ? `<h4>Related work orders</h4>${kv(w.related.map((r) => [esc(r.type), `<a href="#" data-goto="${esc(r.id)}">${esc(r.id)}</a>`]))}` : ''}
        ${fr ? `<h4>Field report · ${esc(fr.id)}</h4><p>${esc(fr.title)}<br><span style="color:var(--ink-mute)">${esc(fr.author_role)}</span></p><blockquote>${esc(fr.body)}</blockquote>` : '<h4>Field report</h4><p class="dim">No field report linked.</p>'}
      </div>
      <div class="d-foot">Station record from the knowledge graph. Read-only. The agents read these records when they investigate.</div>`;
  }

  const kv = (rows) => `<div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('')}</div>`;

  function renderDetail() {
    const box = $('detail');
    const l = open.find((x) => x.incident_id === selected), h = closed.find((x) => x.incident_id === selected);
    if (l) {
      const c = current(l), rv = l.review;
      box.innerHTML = `
        <div class="d-head"><div class="d-id">${esc(l.incident_id)}${activeTag(l.incident_id)}</div><div class="d-sub">${chip(c.text, c.kind, c.kind === 'warn')}</div></div>
        <div class="d-body">
          ${kv([
            ['Camera', `<span class="mono">${esc(l.camera_id)}</span>`],
            ['Opened', `<span class="mono">${when(l.triggered_at)}</span>`],
            ['Likely source', l.asset_id ? `<a href="graph.html?asset=${encodeURIComponent(l.asset_id)}&from=incidents">${esc(l.asset_id)}</a> ${badge('hypothesis')}` : 'Not located yet'],
            ['Emission rate', l.rate_kg_ch4_h != null ? `${l.rate_kg_ch4_h} kg CH₄/h ${badge('model_estimated')}` : 'Not estimated'],
            ['Review', rv ? `${DECISION[rv.decision]} by ${esc(rv.reviewer)}${rv.reason ? `<br><span style="color:var(--ink-mute)">“${esc(rv.reason)}”</span>` : ''}` : 'Not decided'],
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
        <div class="d-head"><div class="d-id">${esc(h.incident_id)}${activeTag(h.incident_id)}</div><div class="d-sub">${chip(h.outcome_label, OUTCOME_KIND[h.outcome])}</div></div>
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
            ...(h.comment ? [['What was seen', esc(h.comment)]] : []),
            ['Work order', h.work_order ? `<a href="graph.html?asset=${encodeURIComponent(h.work_order)}&from=incidents">${esc(h.work_order)}</a>` : 'None'],
            ['Emission rate', h.rate != null ? `${h.rate} kg CH₄/h ${badge('model_estimated')}` : 'Not recorded'],
          ])}
          <h4>Timeline</h4>
          <ul class="tl">${(h.timeline || []).map((t) => `<li>${esc(t.event)}<small>${when(t.at_utc)}</small></li>`).join('')}</ul>
        </div>
        ${h.live
          ? `<div class="d-acts"><a class="btn" href="${incHref('report', h.incident_id)}">Closure record →</a><button class="btn" type="button" id="btn-reopen">Reopen</button></div>
             <div class="d-foot">Closed in this run. Reopen it if it was closed by mistake.</div>`
          : '<div class="d-foot">Closed record. Read-only.</div>'}`;
      const ro = $('btn-reopen');
      if (ro) ro.addEventListener('click', () => window.AtelicShell.reopenDialog(h.incident_id, () => { location.href = `incidents.html?id=${encodeURIComponent(h.incident_id)}`; }));
      return;
    }
    const w = past.find((x) => x.id === selected);
    if (w) { box.innerHTML = renderPastDetail(w); return; }
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
    const g = e.target.closest('[data-goto]');
    if (g) { e.preventDefault(); select(g.dataset.goto); const row = document.querySelector(`tr[data-id="${g.dataset.goto}"]`); if (row) row.scrollIntoView({ block: 'nearest' }); return; }
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
      open = d.live.filter((l) => l.triggered && !l.dismissal);
      // A live incident closed as a false alarm in this run sits with the closed records (it can be reopened).
      const closedLive = d.live.filter((l) => l.triggered && l.dismissal).map((l) => ({
        incident_id: l.incident_id, camera_id: l.camera_id, asset_id: null, live: true,
        opened_utc: l.triggered_at, closed_utc: l.dismissal.at, outcome: 'false_alarm', outcome_label: 'False alarm',
        summary: `${l.dismissal.reason_label}. “${l.dismissal.comment}”`,
        decision: { decision: 'dismissed', reviewer: l.dismissal.by, role: null, reason: l.dismissal.reason_label },
        comment: l.dismissal.comment, work_order: null, rate: l.rate_kg_ch4_h,
        timeline: [{ at_utc: l.triggered_at, event: `${l.camera_id} alert raised` },
          { at_utc: l.dismissal.at, event: `Closed as a false alarm by ${l.dismissal.by}` }],
      }));
      closed = [...closedLive, ...(d.history ? d.history.incidents : [])].sort((a, b) => b.opened_utc.localeCompare(a.opened_utc));
      past = (d.history && d.history.past_events) || [];
    } catch (e) {
      console.error(e);
      $('open').innerHTML = '<div class="empty-row">Incidents are unavailable: backend is not reachable.</div>';
      $('closed').innerHTML = '';
      return;
    }
    $('counts').textContent = `${open.length} open · ${closed.length} closed`;
    const want = params.get('id');
    selected = [...open, ...closed].some((x) => x.incident_id === want) || past.some((x) => x.id === want) ? want : (open.find((x) => x.incident_id === INCIDENT_ID) || open[0] || closed[0] || {}).incident_id || null;
    renderOpen();
    renderClosed();
    renderPast();
    renderDetail();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
