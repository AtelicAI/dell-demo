// Screen 7 — Report / Audit. Renders GET /incidents/:id/report; exports go through
// POST /incidents/:id/report/export, built from the same snapshot, so an export matches this page.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const { API, INCIDENT_ID, get } = window.AtelicShell;

  // Simulated / synthetic provenance is tracked in the data (and the JSON export) but not shown.
  // Report rows without a provenance get no badge.
  const badge = (p) => (p ? window.AtelicShell.badge(p) : '');
  const KIND = {
    candidates: 'Locate', candidate_evidence: 'Locate', procedure: 'Procedure', maintenance: 'Maintenance record',
    detection: 'Camera annotation', quantification: 'Estimate', video: 'OGI clip', scenario: 'Scenario', sensor: 'Sensor traces',
    satellite: 'Satellite', field_report: 'Field report', commitment: 'Commitment', history: 'Plant history', operations: 'Operations log',
  };
  const EVENT = {
    alert_triggered: 'Alert triggered', quantification_run: 'Emission estimate run', draft_version: 'Work order version', claim_flagged: 'Claim flagged',
    claim_unflagged: 'Flag cleared', submitted_for_review: 'Submitted for engineer review', review_approve: 'Approved',
    review_request_changes: 'Changes requested', review_reject: 'Rejected', report_exported: 'Report exported',
    agent_detect: 'Camera alert registered', agent_investigation: 'Investigation findings', agent_answer: 'Agent answered',
    question_asked: 'Question asked', report_issued: 'Report issued', repair_recorded: 'Repair recorded',
    agent_verification: 'Verifier result', incident_closed: 'Incident closed', incident_kept_open: 'Kept open', agents_restarted: 'Agents restarted',
  };
  const when = (t) => (t ? `${t.slice(0, 10)} ${t.slice(11, 19)}` : '—');
  const MONO = /^(v\d|INC-|MOV_|[A-Z]{2,4}-\d|\d)/;

  // Report section -> the Glass box step that produced it.
  const SEC_STEP = { camera: 'camera_event', candidates: 'investigation', emissions: 'emission_estimate', action: 'work_order', decision: 'approval', followup: 'verification' };
  let R = null, evNo = {};

  function rows(s) {
    return `<table class="rows">${s.rows.map((r) => {
      const fns = r.cites.filter((c) => evNo[c]).map((c) => `<button class="fn" data-ev="${esc(c)}" aria-label="Source ${evNo[c]}">${evNo[c]}</button>`).join('');
      const val = r.value === null ? 'Not available' : r.value;
      return `<tr class="${r.value === null ? 'gap' : ''}">
        <td class="k">${esc(r.label)}</td>
        <td class="v"><span class="val${r.value !== null && MONO.test(r.value) && r.value.length < 40 ? ' mono' : ''}">${esc(val)}</span>${fns}
          ${r.note ? `<span class="nt">${esc(r.note)}</span>` : ''}</td>
        <td class="p">${badge(r.provenance)}</td></tr>`;
    }).join('')}</table>`;
  }
  function timeline() {
    if (!R.audit.length) return '<p class="tl-empty">No events recorded since the backend started.</p>';
    return `<ol class="tl">${R.audit.map((e) => `<li class="k-${esc(e.event)}"><span class="pt"></span>
      <span class="t">${when(e.at)}</span>
      <span class="e"><b>${esc(EVENT[e.event] || e.event)}</b>${e.version ? ` · v${e.version}` : ''}
        <span class="d">${esc(e.detail)}</span><br><span class="who">${esc(e.actor)}</span></span></li>`).join('')}</ol>`;
  }

  function renderDoc() {
    const doc = $('doc');
    if (!R) {
      $('doc-meta').textContent = '—';
      $('doc-body').innerHTML = '<p class="pending-note">Backend not reachable. No report can be shown.</p>';
      return;
    }
    const final = R.status === 'final';
    doc.classList.toggle('draft', !final);
    $('stamp').hidden = false;
    $('stamp').textContent = R.closure ? 'Closed' : final ? 'Final' : 'Draft';
    $('stamp').className = `stamp${final ? ' ok' : ''}`;
    $('doc-meta').innerHTML = `<span><b>${esc(R.local_incident_id)}</b></span><span>hash ${esc(R.report_hash)}</span><span>generated ${when(R.generated_at)} UTC</span>`;
    $('doc-body').innerHTML = `<p class="pending-note${final ? ' ok' : ''}">${esc(R.status_text)}</p>
      ${R.sections.map((s, i) => `<section class="sec" id="sec-${s.id}" aria-label="${esc(s.title)}">
        <div class="sec-h"><span class="ix">${i + 1}</span><h3>${esc(s.title)}</h3>${SEC_STEP[s.id] ? window.AtelicShell.traceLink(SEC_STEP[s.id], 'Trace') : ''}
          <span class="cnt">${s.id === 'audit' ? `${R.audit.length} event${R.audit.length === 1 ? '' : 's'}` : ((n) => `${n || 'no'} gap${n === 1 ? '' : 's'}`)(s.rows.filter((r) => r.value === null).length)}</span></div>
        ${s.id === 'audit' ? timeline() : rows(s)}</section>`).join('')}
      <div class="doc-foot"><span>${esc(R.footer)}</span><span>${esc(R.report_hash)}</span></div>`;
  }

  function renderSide() {
    if (!R) { $('st-body').innerHTML = '<p class="xp-msg err">Backend not reachable.</p>'; return; }
    const final = R.status === 'final', dec = R.sections.find((s) => s.id === 'decision').rows;
    const who = dec.find((r) => r.label === 'Reviewer'), at = dec.find((r) => r.label === 'Decided at');
    $('st-meta').textContent = R.closure ? 'Closed' : final ? 'Final' : 'Draft';
    $('st-body').innerHTML = `<p class="big">${final && who ? `Approved by ${esc(who.value)} at ${when(at.value)} UTC` : esc(R.status_text)}</p>
      <div class="kv"><span>Report hash</span><span>${esc(R.report_hash)}</span>
        <span>Sources</span><span>${R.evidence.length}</span><span>Audit events</span><span>${R.audit.length}</span>
        <span>Gaps</span><span>${R.gaps.length}</span></div>
      ${final ? '' : '<a class="btn" href="review.html" style="justify-content:center">Open Approve →</a>'}`;
    $('xh').innerHTML = R.exports.slice().reverse().slice(0, 4).map((x) =>
      `<li>${esc(x.format.toUpperCase())} · ${esc(x.status)} · ${esc(x.hash)} · ${when(x.at).slice(11)}</li>`).join('');
    $('gap-meta').textContent = `${R.gaps.length}`;
    $('gaps').innerHTML = R.gaps.length ? R.gaps.map((g) => `<li>${esc(g)}</li>`).join('') : '<li>No gaps recorded.</li>';
  }

  function renderEvidence() {
    if (!R) { $('ev-rows').innerHTML = ''; return; }
    $('ev-meta').textContent = `${R.evidence.length} sources`;
    $('ev-rows').innerHTML = R.evidence.map((e) => `<tr id="ev-${esc(e.id)}">
      <td class="n">[${evNo[e.id]}]</td><td>${esc(e.label)}</td><td>${esc(KIND[e.kind] || e.kind)}</td>
      <td>${badge(e.provenance)}</td><td class="loc">${esc(e.source)}</td>
      <td class="go">${e.screen ? `<a href="${e.screen}">Open →</a>` : ''}</td></tr>`).join('');
  }

  function renderAll() {
    evNo = {};
    if (R) R.evidence.forEach((e, i) => (evNo[e.id] = i + 1));
    renderDoc(); renderSide(); renderEvidence(); renderStory();
  }

  function readName() { try { return localStorage.getItem('atelic.reviewer') || ''; } catch (e) { return ''; } }
  function renderStory() {
    const S = window.AtelicStory, fu = R && R.follow_up;
    $('fu-panel').hidden = !(fu && ['approved', 'reported', 'repair_recorded', 'verification_ready', 'closed', 'follow_up_required'].includes(fu.state));
    if (!$('fu-panel').hidden && !(document.activeElement && /^fu-/.test(document.activeElement.id))) {
      $('fu-body').innerHTML = S.followUp(fu, readName());
      $('fu-meta').textContent = fu.state.replace(/_/g, ' ');
    }
    $('ak-panel').hidden = !R || R.status !== 'final';
    if (R && R.status === 'final') {
      const em = R.sections.find((x) => x.id === 'emissions');
      const rate = em && em.rows[0].value ? parseFloat(em.rows[0].value) : null;
      $('ak-body').innerHTML = S.answerKey(R.answer_key, rate);
    }
  }

  async function act(path, body, msgId) {
    const msg = $(msgId);
    msg.textContent = 'Recording…';
    const r = await fetch(`${API}/incidents/${INCIDENT_ID}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) { msg.textContent = typeof data.detail === 'string' ? data.detail : 'Failed'; return; }
    try { localStorage.setItem('atelic.reviewer', body.by); } catch (e) { /* ignore */ }
    R = data;
    document.activeElement.blur();
    renderAll();
    window.AtelicShell.refresh();
    startPoll();
  }
  document.addEventListener('click', (e) => {
    if (e.target.id === 'fu-repair') act('/repair', { by: $('fu-by').value.trim(), work_performed: $('fu-text').value.trim() }, 'fu-msg');
    const c = e.target.closest('[data-close]');
    if (c) act('/close', { by: $('fu-cby').value.trim(), decision: c.dataset.close, rationale: $('fu-why').value.trim() }, 'fu-cmsg');
  });
  // Live while the verifier works.
  const waiting = (r) => r && r.follow_up && (r.follow_up.state === 'repair_recorded' || (r.follow_up.agent && r.follow_up.agent.status === 'running'));
  let stop = null;
  function startPoll() {
    if (stop) stop();
    stop = window.AtelicStory.poll(async () => { R = await get(`/incidents/${INCIDENT_ID}/report`); renderAll(); return R; }, waiting, 4000);
  }

  async function load() {
    try { R = await get(`/incidents/${INCIDENT_ID}/report`); } catch (e) { console.error(e); R = null; }
    renderAll();
  }

  // ----- exports: the backend builds the file and records the export in the audit log -----
  async function exportAs(fmt) {
    const msg = $('xp-msg');
    msg.classList.remove('err'); msg.textContent = 'Preparing…';
    try {
      const r = await fetch(`${API}/incidents/${INCIDENT_ID}/report/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ format: fmt }),
      });
      if (!r.ok) throw new Error(`Export failed (${r.status})`);
      if (fmt === 'pdf') {
        await load();
        msg.textContent = 'Choose "Save as PDF" in the print dialog.';
        window.print();
      } else {
        const blob = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${INCIDENT_ID}_report_${R.report_hash}.${fmt}`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        await load();
        msg.textContent = `${fmt === 'md' ? 'Markdown' : 'JSON'} exported (${R.report_hash}).`;
      }
      window.AtelicShell.refresh();
    } catch (e) {
      msg.classList.add('err'); msg.textContent = e.message;
    }
  }

  function showEvidence(id) {
    document.querySelectorAll('.evt tr.hl').forEach((t) => t.classList.remove('hl'));
    const tr = id && document.getElementById(`ev-${id}`);
    if (tr) { tr.classList.add('hl'); tr.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    else $('evidence').scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  document.addEventListener('click', (e) => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.dataset.export) exportAs(t.dataset.export);
    else if (t.id === 'btn-evidence') showEvidence(null);
    else if (t.matches('.fn')) showEvidence(t.dataset.ev);
  });

  load().then(startPoll);
})();
