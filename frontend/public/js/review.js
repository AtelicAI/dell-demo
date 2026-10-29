// Screen 6 — Approve / Engineer review. Reads GET /incidents/:id/work-order and records a named
// reviewer's decision with POST /incidents/:id/review. Approval records a decision; it operates nothing.
// Layout is decision first: brief, points needing judgement, the work order with folded step lists.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const { API, INCIDENT_ID, get } = window.AtelicShell;

  // Provenance goes in each line's tooltip; simulated / synthetic is tracked in the data but not shown.
  const PROV = { model_estimated: 'Model estimated', manual_annotation: 'Manual annotation', hypothesis: 'Hypothesis', gap: 'Missing data' };
  const DECISION = {
    approve: { label: 'Approved', chip: 'ok' },
    request_changes: { label: 'Changes requested', chip: 'chg' },
    reject: { label: 'Rejected', chip: 'bad' },
  };
  const STEPS = [['inspection', 'Recommended inspection', 'step'], ['isolation', 'Isolation prerequisites', 'check'], ['safety', 'Safety considerations', 'item']];
  const when = (t) => (t ? `${t.slice(11, 16)} UTC` : '—');
  const day = (t) => (t ? `${t.slice(0, 10)} ${t.slice(11, 16)} UTC` : '—');
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const SHIELD = '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M8 1.5 2.5 3.5v4c0 3.3 2.3 5.8 5.5 7 3.2-1.2 5.5-3.7 5.5-7v-4z"/><path d="M5.5 8h5"/></svg>';

  let W = null, evById = {}, justDecided = false;

  function readName() { try { return localStorage.getItem('atelic.reviewer') || ''; } catch (e) { return ''; } }
  function saveName(v) { try { localStorage.setItem('atelic.reviewer', v); } catch (e) { /* storage blocked: name still sent */ } }

  // One claim as a plain reading line. Hover shows its provenance and sources.
  function line(c) {
    const f = W.flags[c.id];
    const srcs = [...new Set(c.cites.map((id) => evById[id] && evById[id].group).filter(Boolean))];
    const tip = [PROV[c.provenance], srcs.length ? `Sources: ${srcs.join('; ')}` : 'No source cited'].filter(Boolean).join(' · ');
    return `<li class="${f ? 'flagged' : c.provenance}" title="${esc(tip)}"><span class="mk"></span><span class="tx">${esc(c.text)}</span>
      ${f ? `<span class="note">Flagged on Plan${f.note ? `: ${esc(f.note)}` : ''}</span>` : ''}</li>`;
  }

  // ----- 1 · decision brief -----
  function renderBrief() {
    const d = W && W.draft;
    if (!W) { $('brief-head').innerHTML = '<div class="eyebrow">Backend not reachable.</div>'; $('tiles').innerHTML = ''; return; }
    const sub = W.submitted, rv = W.review;
    if (!d) {
      $('brief-head').innerHTML = '<div class="eyebrow">Nothing to review yet</div><h3>No work order has been drafted.</h3>';
    } else {
      const eyebrow = rv ? `Decided · <b>v${rv.version}</b> · ${esc(DECISION[rv.decision].label.toLowerCase())} by ${esc(rv.reviewer)}`
        : sub ? `For your decision · <b>v${sub.version}</b> submitted ${when(sub.at)}`
        : `Draft <b>v${d.version}</b> · not submitted for review yet`;
      const a = d.fields.asset;
      $('brief-head').innerHTML = `<div class="eyebrow">${eyebrow}</div>
        <h3>${a ? esc(d.assessment).replace(esc(a), `<em>${esc(a)}</em>`) : esc(d.assessment)}</h3>`;
    }
    const inp = Object.fromEntries((W.inputs || []).map((x) => [x.id, x]));
    const tile = (label, v, sub2, href, cls) => `<${href ? `a href="${href}"` : 'div'} class="tile">
      <span class="lbl">${label}</span><span class="v${cls ? ' ' + cls : ''}" title="${esc(v)}">${esc(v)}</span><span class="d">${esc(sub2)}</span></${href ? 'a' : 'div'}>`;
    const flags = d ? Object.keys(W.flags).length : 0;
    $('tiles').innerHTML = [
      tile('Likely source', (inp.source && inp.source.value) || '—', 'Unconfirmed · needs field check', 'locate.html'),
      tile('Emission rate', (inp.rate && inp.rate.value) || '—', (inp.rate && inp.rate.detail) || '', 'quantify.html'),
      tile('Priority', (d && d.fields.priority) || 'Not set', d && d.fields.priority ? 'Set by the engineer on Plan' : 'Set on Plan before submitting', null, d && d.fields.priority ? '' : 'warn'),
      tile('Flagged claims', String(flags), flags ? 'Marked unsupported on Plan' : 'None marked on Plan', null, flags ? 'bad' : ''),
    ].join('');
    $('observed').hidden = !d;
    if (d) {
      const cs = d.sections.observed || [];
      $('sec-observed').innerHTML = cs.map(line).join('');
      $('n-observed').textContent = cs.length;
    }
  }

  // ----- 2 · source uncertainty: flagged first -----
  function renderAttn() {
    const d = W && W.draft;
    $('attn').hidden = !d;
    if (!d) return;
    const cs = (d.sections.uncertain || []).slice().sort((a, b) => (W.flags[b.id] ? 1 : 0) - (W.flags[a.id] ? 1 : 0));
    $('sec-uncertain').innerHTML = cs.map(line).join('');
    $('n-uncertain').textContent = `${plural(cs.length, 'point')} to weigh`;
  }

  // ----- 3 · proposed work order -----
  function renderDoc() {
    const d = W && W.draft, chip = $('wo-chip');
    chip.hidden = !d;
    if (!d) {
      $('doc-meta').textContent = '';
      $('doc-body').innerHTML = W ? '<div class="empty">No draft has been prepared yet. <a href="plan.html">Open Plan →</a></div>'
        : '<div class="empty">Backend not reachable. No draft can be shown.</div>';
      return;
    }
    const f = d.fields, rv = W.review;
    const [txt, cls] = rv ? [DECISION[rv.decision].label, DECISION[rv.decision].chip] : W.submitted ? ['Awaiting review', 'sub'] : ['Draft', ''];
    chip.textContent = txt;
    chip.className = `chip${cls ? ' ' + cls : ''}`;
    $('doc-meta').textContent = `v${d.version} · ${d.hash}`;
    const permits = f.required_permits && f.required_permits.length ? f.required_permits.map(esc).join('<br>') : 'None listed';
    const acc = STEPS.map(([key, title, unit]) => {
      const cs = d.sections[key] || [];
      const fl = cs.filter((c) => W.flags[c.id]).length;
      return `<details${fl ? ' open' : ''}><summary>${title}${fl ? `<span class="fl">${fl} flagged</span>` : ''}<span class="n">${plural(cs.length, unit)}</span></summary>
        <ol class="plain">${cs.map(line).join('')}</ol></details>`;
    }).join('');
    const weak = STEPS.flatMap(([k]) => d.sections[k] || []).filter((c) => c.provenance === 'hypothesis' || c.provenance === 'gap');
    $('doc-body').innerHTML = `
      <dl class="fields">
        <dt>Asset</dt><dd class="mono">${esc(f.asset || '—')}</dd>
        <dt>Problem</dt><dd>${esc(f.problem || '—')}</dd>
        <dt>Proposed inspection</dt><dd>${esc(f.proposed_inspection || '—')}</dd>
        <dt>Required permits</dt><dd>${permits}</dd>
      </dl>
      <div class="steps-acc">${acc}</div>
      <div class="limits-line" aria-label="Procedure limitations"><span class="t">Procedure limitations</span>
        <span>No equipment action will be executed</span><span class="sep">·</span>
        <span>${esc(W.generator.label)}${W.generator.kind === 'llm' ? ', advisory draft' : ', no language model'}</span>
        ${weak.length ? `<span class="sep">·</span><button type="button" id="btn-weak" aria-expanded="false">${plural(weak.length, 'line')} not backed by a procedure</button>` : ''}
      </div>
      <div class="limits-list" id="weak-list" hidden><ul class="plain">${weak.map(line).join('')}</ul></div>`;
  }

  // ----- 4 · reviewer decision -----
  function renderRail() {
    const body = $('rd-body');
    const safety = `<div class="safety">${SHIELD}<span>Approval records a decision only. No physical control commands are enabled.</span></div>`;
    if (!W) { $('rd-meta').textContent = '—'; body.innerHTML = '<p class="rd-note err">Backend not reachable.</p>'; return; }
    const d = W.draft, rv = W.review, sub = W.submitted;
    $('rd-meta').textContent = rv ? DECISION[rv.decision].label : sub ? 'Awaiting you' : 'Nothing submitted';
    const history = W.reviews.length ? `<details class="hist"><summary>History · ${plural(W.reviews.length, 'decision')}</summary><ul>${W.reviews.slice().reverse().map((r) =>
      `<li><b>${esc(DECISION[r.decision].label)}</b> v${r.version} by ${esc(r.reviewer)} · ${day(r.at)}${r.reason ? ` · ${esc(r.reason)}` : ''}</li>`).join('')}</ul></details>` : '';

    if (rv) {
      const ok = rv.decision === 'approve';
      body.innerHTML = `
        <div class="sig ${ok ? '' : rv.decision === 'reject' ? 'rejected' : 'changes'}${justDecided ? ' just' : ''}">
          <span class="lbl">${ok ? 'Approved by' : 'Reviewed by'}</span>
          <div class="who">${esc(rv.reviewer)}</div>
          <div class="when">${day(rv.at)} · v${rv.version}</div>
          <span class="seal chip ${DECISION[rv.decision].chip}">${esc(DECISION[rv.decision].label)}</span>
          ${ok ? '' : `<div class="why">${esc(rv.reason)}</div>`}
        </div>
        <p class="rd-note">${ok ? 'Recorded in the audit log. The draft is now locked. The name is as typed at review; it is not tied to the sign-in.'
          : 'Returned to Plan. A rejected or returned draft cannot close the incident until it is edited and submitted again.'}</p>
        <a class="btn" href="${ok ? 'report.html' : 'plan.html'}">${ok ? 'Open Report →' : 'Open Plan →'}</a>
        ${history}${safety}`;
      justDecided = false;
      return;
    }
    if (!sub) {
      body.innerHTML = `
        <div class="ask"><p class="big">Nothing to decide yet</p>
          <p class="sm">${d ? `Draft v${d.version} has not been submitted. It is submitted from Plan.` : 'No draft has been prepared yet.'}</p></div>
        <a class="btn" href="plan.html">Open Plan →</a>
        ${history}${safety}`;
      return;
    }
    const flags = sub.open_flags.length;
    body.innerHTML = `
      <div class="ask"><p class="big">Approve work order v${sub.version}?</p>
        <p class="sm">Submitted ${when(sub.at)} by ${esc(sub.by)}${flags ? ` · <b>${plural(flags, 'flagged claim')}</b>` : ' · no flagged claims'}</p></div>
      ${W.stale ? '<p class="rd-note err">The emission estimate changed after this draft was made. It cannot be approved; request changes instead.</p>' : ''}
      <div class="field"><label for="rv-name">Your name</label>
        <input id="rv-name" autocomplete="name" maxlength="80" value="${esc(readName())}" placeholder="Full name">
        <div class="hint">Recorded with the decision. Not tied to the sign-in.</div></div>
      <div class="acts">
        <button class="btn go" data-decide="approve"${W.stale ? ' disabled title="Evidence changed after drafting"' : ''}>Approve draft</button>
        <button class="btn" data-decide="request_changes">Request changes</button>
        <button class="btn no" data-decide="reject">Reject draft</button>
      </div>
      <p class="rd-note" id="rd-msg" role="status" aria-live="polite" hidden></p>
      ${history}${safety}`;
    syncButtons();
  }
  function syncButtons() {
    const named = !!($('rv-name') && $('rv-name').value.trim());
    document.querySelectorAll('[data-decide]').forEach((b) => {
      b.disabled = !named || (b.dataset.decide === 'approve' && W.stale);
    });
  }

  function renderAll() {
    evById = {};
    if (W && W.draft) W.draft.evidence.forEach((e) => (evById[e.id] = e));
    renderBrief(); renderAttn(); renderDoc(); renderRail(); renderStory();
  }

  function renderStory() {
    const S = window.AtelicStory;
    $('inv-panel').hidden = !(W && (W.investigation || (W.questions || []).length || W.agent));
    if (!W) return;
    $('agent-banner').innerHTML = S.agentBanner(W.agent);
    $('investigation').innerHTML = S.investigation(W.investigation);
    const notes = (W.field_notes || []).length, round = W.investigation && W.investigation.round;
    $('inv-meta').textContent = W.investigation ? [`${W.investigation.findings.length} findings`, `${(W.questions || []).length} questions`,
      notes ? `${notes} field note${notes === 1 ? '' : 's'}` : '', round > 1 ? `round ${round}` : ''].filter(Boolean).join(' · ') : '';
    // Keep anything typed but not sent across re-renders.
    const typed = ['qa-text', 'fn-text', 'rs-focus'].map((id) => [id, $(id) ? $(id).value : '']);
    $('qa').innerHTML = S.questions(W.questions, W.can_ask) + S.research(W);
    typed.forEach(([id, v]) => { if ($(id)) $(id).value = v; });
  }

  async function decide(decision, reason) {
    const sub = W.submitted, reviewer = $('rv-name').value.trim();
    saveName(reviewer);
    const msg = $('rd-msg');
    msg.hidden = false; msg.classList.remove('err'); msg.textContent = 'Recording decision…';
    try {
      const r = await fetch(`${API}/incidents/${INCIDENT_ID}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, reviewer, reason, version: sub.version, hash: sub.hash }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : r.status);
      W = data;
      justDecided = true;
      renderAll();
      window.AtelicShell.refresh();
      if (decision === 'request_changes') startPoll();
    } catch (e) {
      msg.classList.add('err'); msg.textContent = e.message;
    }
  }

  // ----- confirmation dialog (shell component); a reason is required when the body asks for one -----
  const openModal = (title, html, okLabel, okClass, onOk) => window.AtelicShell.modal({ title, html, okLabel, okClass, onOk, requireText: true });

  document.addEventListener('input', (e) => { if (e.target.id === 'rv-name') syncButtons(); });
  document.addEventListener('click', async (e) => {
    const who = ($('rv-name') && $('rv-name').value.trim()) || readName();
    const ask = e.target.closest('[data-ask]');
    if (ask && $('qa-text')) { $('qa-text').value = ask.dataset.ask; $('qa-text').focus(); return; }
    if (e.target.id === 'fn-send' || e.target.id === 'rs-send') {
      const note = e.target.id === 'fn-send';
      const text = (note ? $('fn-text') : $('rs-focus')).value.trim();
      if (note && !text) return;
      e.target.disabled = true;
      const data = await window.AtelicStory.send(`/incidents/${INCIDENT_ID}/${note ? 'field-notes' : 'research'}`,
        note ? { text, recorded_by: who } : { focus: text, requested_by: who }, note ? 'fn-msg' : 'rs-msg');
      e.target.disabled = false;
      if (data) {
        W = data;
        if ($(note ? 'fn-text' : 'rs-focus')) $(note ? 'fn-text' : 'rs-focus').value = '';
        renderAll();
        window.AtelicShell.refresh();
        startPoll();
      }
      return;
    }
    if (e.target.id !== 'qa-send') return;
    e.target.disabled = true;
    const data = await window.AtelicStory.ask(INCIDENT_ID, ($('rv-name') && $('rv-name').value.trim()) || readName());
    e.target.disabled = false;
    if (data) { W = data; renderAll(); $('qa-msg').textContent = 'Sent. The answer appears here in about a minute.'; startPoll(); }
  });
  document.addEventListener('click', (e) => {
    if (e.target.id === 'btn-weak') {
      const open = $('weak-list').hidden;
      $('weak-list').hidden = !open;
      e.target.setAttribute('aria-expanded', String(open));
      return;
    }
    const t = e.target.closest('[data-decide]');
    if (!t || t.disabled) return;
    const d = W.draft, sub = W.submitted, who = esc($('rv-name').value.trim());
    const kv = `<div class="kv"><span>Draft</span><span>v${sub.version} · ${esc(sub.hash)}</span>
      <span>Asset</span><span>${esc(d.fields.asset || '—')}</span><span>Priority</span><span>${esc(d.fields.priority || '—')}</span>
      <span>Flagged claims</span><span>${sub.open_flags.length}</span><span>Reviewer</span><span>${who}</span></div>`;
    if (t.dataset.decide === 'approve') {
      openModal('Approve this work order draft?', `${kv}<p>No physical plant action will occur.</p>`,
        'Approve draft', 'go', () => decide('approve', null));
    } else {
      const rej = t.dataset.decide === 'reject';
      openModal(rej ? 'Reject this draft?' : 'Request changes to this draft?', `${kv}
        <label for="rv-reason">Reason (required, shown on Plan and in the audit log)</label>
        <textarea id="rv-reason" rows="3"></textarea>
        <p>${rej ? 'A rejected draft cannot close the incident.' : 'The draft goes back to Plan for editing.'} No physical plant action will occur.</p>`,
        rej ? 'Reject draft' : 'Request changes', rej ? 'no' : '', () => decide(t.dataset.decide, $('rv-reason').value.trim()));
    }
  });

  // Keep the page live while an agent works (answering, revising after Request changes).
  const pending = (w) => w && ((w.agent && w.agent.status === 'running') || (w.research && w.research.requested)
    || (w.questions || []).some((q) => q.status !== 'answered'));
  let stopPoll = null;
  function startPoll() {
    if (stopPoll) stopPoll();
    stopPoll = window.AtelicStory.poll(async () => {
      if (document.activeElement && ['rv-name', 'qa-text', 'rv-reason', 'fn-text', 'rs-focus'].includes(document.activeElement.id)) return W;
      W = await get(`/incidents/${INCIDENT_ID}/work-order`);
      renderAll();
      return W;
    }, pending, 4000);
  }
  async function init() {
    try { W = await get(`/incidents/${INCIDENT_ID}/work-order`); } catch (e) { console.error(e); W = null; }
    renderAll();
    if (W) startPoll();
  }
  init();
})();
