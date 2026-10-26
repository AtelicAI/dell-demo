// Screen 5 — Plan / Engineering action. Reads GET /incidents/:id/work-order; drafts, edits, flags and
// submits through the work-order POSTs. Nothing on this page can operate equipment.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const { API, INCIDENT_ID, get } = window.AtelicShell;

  const BADGE = {
    simulated: 'Simulated', synthetic: 'Simulated', model_estimated: 'Model estimated',
    manual_annotation: 'Manual annotation', hypothesis: 'Hypothesis', gap: 'Gap', pending: 'Pending',
  };
  // Simulated / synthetic provenance is tracked in the data but not shown.
  const badge = (p, title) => p === 'simulated' || p === 'synthetic' ? '' : `<span class="badge ${p}"${title ? ` title="${esc(title)}"` : ''}>${BADGE[p] || 'Pending'}</span>`;
  const KIND = {
    candidates: 'Locate', candidate_evidence: 'Locate', procedure: 'Procedure', maintenance: 'Maintenance record',
    detection: 'Camera', quantification: 'Estimate',
  };
  const SCREEN = { 'locate.html': 'Locate', 'quantify.html': 'Quantify', 'detection.html': 'Camera' };
  // Footnotes are numbered in reading order: summary first, then the document.
  const ORDER = ['observed', 'uncertain', 'inspection', 'isolation', 'safety'];
  const PRIORITIES = ['Low', 'Medium', 'High'];
  const hhmmss = (t) => (t ? `${t.slice(11, 19)} UTC` : '—');
  const FLAG_ICON = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 15V2M3 2h9l-2 3.5L12 9H3"/></svg>';

  let W = null, editing = false, editPriority = null, fnNo = {}, evById = {}, lineIx = 0;

  async function post(path, body) {
    const r = await fetch(`${API}/incidents/${INCIDENT_ID}/work-order${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    });
    const data = await r.json();
    if (!r.ok) {
      const d = data.detail;
      throw new Error(typeof d === 'string' ? d : (d && d.missing) ? `Missing: ${d.missing.join('; ')}` : r.status);
    }
    return data;
  }

  function indexEvidence(d) {
    fnNo = {}; evById = {};
    d.evidence.forEach((e) => (evById[e.id] = e));
    let n = 0;
    ORDER.forEach((s) => (d.sections[s] || []).forEach((c) => c.cites.forEach((id) => { if (!fnNo[id]) fnNo[id] = ++n; })));
  }
  const claims = () => ORDER.flatMap((s) => (W.draft.sections[s] || []));

  // ----- one claim line -----
  // A line shows its own badge when its provenance differs from the section's (or always, in the summary).
  function line(c, sectionProv) {
    const f = W.flags[c.id];
    const fns = c.cites.map((id) => `<button class="fn" data-ev="${esc(id)}" aria-label="Source ${fnNo[id]}">${fnNo[id]}</button>`).join('');
    const b = c.provenance !== sectionProv ? badge(c.provenance) : '';
    return `<li class="ln${f ? ' flagged' : ''}" id="ln-${c.id}" data-cites="${esc(c.cites.join(' '))}" style="--i:${lineIx++}">
      <span class="mk"></span>
      <span class="tx">${esc(c.text)}${b}${fns || ' <span class="lbl">no source</span>'}</span>
      <button class="flag-btn" data-flag="${c.id}" aria-label="${f ? 'Clear flag' : 'Flag as unsupported'}" title="${f ? 'Clear flag' : 'Flag as unsupported'}">${FLAG_ICON}</button>
      ${f ? `<span class="fl">Flagged as unsupported${f.note ? `: ${esc(f.note)}` : ''}<button data-unflag="${c.id}">Clear flag</button></span>` : ''}
    </li>`;
  }
  function dominant(cs) {
    const n = {};
    cs.forEach((c) => (n[c.provenance] = (n[c.provenance] || 0) + 1));
    return Object.keys(n).sort((a, b) => n[b] - n[a])[0];
  }

  // ----- incident summary band -----
  function renderBrief() {
    const d = W && W.draft;
    if (!W) {
      $('assess').innerHTML = '<p class="big">Backend not reachable</p><p class="sub">No draft or inputs can be shown.</p>';
      $('facts').innerHTML = '';
      return;
    }
    if (d) {
      const a = d.fields.asset;
      const big = a ? esc(d.assessment).replace(esc(a), `<em>${esc(a)}</em>`) : esc(d.assessment);
      $('assess').innerHTML = `<span class="lbl">Assessment</span><p class="big">${big}</p>
        <p class="sub">Assembled by <b>${esc(W.generator.id)} ${esc(W.generator.version)}</b> from ${d.evidence.length} sources. No language model was used.</p>
        <div class="gb-row">${window.AtelicShell.traceLink('work_order', 'See how this draft was made')}</div>`;
    } else {
      $('assess').innerHTML = `<span class="lbl">Assessment</span><p class="big">No response drafted yet</p>
        <p class="sub">${esc(W.generator.note)}${W.nvidia_inference === 'not_connected' ? ' NVIDIA inference not connected in this run.' : ''}</p>
        <div class="cta">${W.can_draft
          ? '<button class="btn primary" id="btn-draft">Draft work order</button>'
          : `<span class="run-msg err">Cannot draft: ${W.missing.map(esc).join('; ')}</span>`}
          <span class="run-msg" id="draft-msg" role="status" aria-live="polite"></span></div>`;
    }
    $('facts').innerHTML = (W.inputs || []).map((f) => {
      const tag = f.screen ? 'a' : 'div';
      return `<${tag} class="fx"${f.screen ? ` href="${f.screen}"` : ''}>
        <span class="lbl">${esc(f.label)}</span>
        <span class="v${f.value ? '' : ' none'}" title="${esc(f.value || '')}">${esc(f.value || '—')}</span>
        <span class="d">${esc(f.detail)}</span>
        <span class="ft">${badge(f.provenance)}${f.screen ? `<span class="go">${SCREEN[f.screen]} →</span>` : ''}</span>
      </${tag}>`;
    }).join('');
    $('brief-more').hidden = !d;
    if (d) {
      ['observed', 'uncertain'].forEach((s) => {
        const cs = d.sections[s] || [];
        $(`sec-${s}`).innerHTML = cs.map((c) => line(c, null)).join('');
        $(`n-${s}`).textContent = cs.length;
        $(`sum-${s}`).textContent = cs.length;
      });
    }
  }

  // ----- the work order document -----
  function priorityBox(v) {
    const seg = (sel) => `<div class="seg" role="group" aria-label="Priority">${PRIORITIES.map((p) =>
      `<button data-prio="${p}" aria-pressed="${p === sel}">${p}</button>`).join('')}</div>`;
    if (editing) return `<div class="box"><span class="lbl">Priority</span>${seg(editPriority)}</div>`;
    if (!v) return `<div class="box need"><span class="lbl">Priority · engineer to set</span>${seg(null)}<div class="hint">Required before submitting. Not set by the template.</div></div>`;
    return `<div class="box"><span class="lbl">Priority</span><div class="val mono">${esc(v)}</div></div>`;
  }
  function textBox(k, label, v, rows) {
    const val = editing
      ? `<textarea data-f="${k}" rows="${rows}">${esc(k === 'required_permits' ? (v || []).join('\n') : v || '')}</textarea>${k === 'required_permits' ? '<div class="hint">One permit per line.</div>' : ''}`
      : k === 'required_permits'
        ? (v && v.length ? `<ul>${v.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : '<span class="lbl">None listed</span>')
        : esc(v || '—');
    return `<div class="box full"><span class="lbl">${label}</span><div class="val">${val}</div></div>`;
  }
  function section(ix, title, key, listClass) {
    const cs = W.draft.sections[key] || [];
    const dom = dominant(cs);
    const first = cs.find((c) => c.provenance === dom && c.cites.length);
    const src = first ? evById[first.cites[0]].group.split(' · ')[0] : '';
    return `<section class="dsec" aria-label="${title}">
      <div class="dsec-h"><span class="ix">${ix}</span><h3>${title}</h3>
        <span class="src">${src ? `from ${esc(src)}` : ''}${dom ? badge(dom) : ''}</span></div>
      <ul class="lines ${listClass}">${cs.map((c) => line(c, dom)).join('')}</ul>
    </section>`;
  }

  function renderDoc() {
    const d = W && W.draft, body = $('doc-body');
    $('stamp').hidden = !d;
    $('tools').hidden = !d;
    if (!d) {
      $('doc-meta').textContent = W ? 'No draft yet' : '—';
      body.innerHTML = `<div class="outline" aria-label="Draft outline">
        <div><span>Work order fields</span><span>Asset · Problem · Proposed inspection · Priority · Required permits</span></div>
        <div><span>A · Recommended inspection</span><span>Steps from the matching procedure</span></div>
        <div><span>B · Isolation prerequisites</span><span>From the isolation checklist</span></div>
        <div><span>C · Safety considerations</span><span>Permits and gas tests</span></div></div>`;
      return;
    }
    const f = d.fields, sub = W.submitted, rv = W.review, approved = !!(rv && rv.decision === 'approve');
    // An approved draft is locked by the backend; a returned one shows the reviewer's reason.
    document.body.classList.toggle('locked', approved);
    $('stamp').textContent = approved ? 'Approved' : sub ? 'Submitted for review' : 'Status: Draft';
    $('stamp').className = `stamp${approved ? ' ok' : sub ? ' sub' : ''}`;
    $('tools').hidden = approved;
    const latest = W.versions[W.versions.length - 1];
    $('doc-meta').innerHTML = `<span><b>v${d.version}</b></span><span>${esc(d.hash)}</span><span>${esc(latest.author)}</span><span>${hhmmss(latest.created_at)}</span>`;
    $('btn-edit').textContent = editing ? 'Save as new version' : 'Edit fields';
    $('btn-edit').classList.toggle('primary', editing);
    $('btn-edit').classList.toggle('ghost', !editing);
    $('btn-cancel').hidden = !editing;
    $('btn-regen').hidden = editing;
    body.innerHTML = `
      ${rv && !approved ? `<div class="banner returned"><span><b>${rv.decision === 'reject' ? 'Rejected' : 'Changes requested'}</b> by ${esc(rv.reviewer)}: ${esc(rv.reason)}</span></div>` : ''}
      ${W.stale && !approved ? '<div class="banner">The emission estimate changed after this draft was made.<button class="btn sm" data-regen>Regenerate draft</button></div>' : ''}
      <div class="doc-msg" id="doc-msg" role="status" aria-live="polite" hidden></div>
      <div class="wo">
        <div class="box"><span class="lbl">Asset</span><div class="val mono">${esc(f.asset || '—')}</div></div>
        ${priorityBox(f.priority)}
        <div class="box"><span class="lbl">Status</span><div class="val mono">${esc(d.status)}</div></div>
        ${textBox('problem', 'Problem', f.problem, 2)}
        ${textBox('proposed_inspection', 'Proposed inspection', f.proposed_inspection, 2)}
        ${textBox('required_permits', 'Required permits', f.required_permits, 3)}
      </div>
      ${section('A', 'Recommended inspection', 'inspection', 'steps')}
      ${section('B', 'Isolation prerequisites', 'isolation', 'boxes')}
      ${section('C', 'Safety considerations', 'safety', 'bullets')}
      <p class="warning">No equipment action will be executed.</p>
      <details class="fold">
        <summary>Version history<span class="meta">${W.versions.length} version${W.versions.length > 1 ? 's' : ''}</span></summary>
        <div class="fold-body"><table class="vers">${W.versions.slice().reverse().map((v) => `<tr>
          <td class="m">v${v.version}</td><td class="m">${esc(v.hash)}</td><td>${esc(v.author)}</td>
          <td>${esc(v.changed.map((c) => c.replace(/_/g, ' ')).join(', '))}</td><td class="m">${hhmmss(v.created_at)}</td></tr>`).join('')}</table></div>
      </details>
      <details class="fold">
        <summary>Agent trace<span class="meta">No agent run</span></summary>
        <div class="fold-body">No agent or model call was made for this draft. It was assembled by
          <b>${esc(W.generator.id)} ${esc(W.generator.version)}</b> from the cited sources.
          ${W.nvidia_inference === 'not_connected' ? '<br>NVIDIA inference not connected in this run.' : ''}
          <br>${window.AtelicShell.traceLink('work_order', 'Open this step in the Glass box')}</div>
      </details>`;
  }

  // ----- evidence used: one group per source document, numbered like footnotes -----
  function renderEvidence() {
    const d = W && W.draft;
    if (!d) { $('evidence').innerHTML = '<div class="ev-empty">Sources appear here once the draft cites them.</div>'; $('ev-meta').textContent = '—'; return; }
    const groups = [];
    d.evidence.slice().sort((a, b) => (fnNo[a.id] || 1e9) - (fnNo[b.id] || 1e9)).forEach((e) => {
      let g = groups.find((x) => x.name === e.group);
      if (!g) groups.push(g = { name: e.group, items: [] });
      g.items.push(e);
    });
    $('ev-meta').textContent = `${d.evidence.length} sources · ${groups.length} documents`;
    $('evidence').innerHTML = groups.map((g) => {
      const e0 = g.items[0];
      const provs = [...new Set(g.items.map((e) => e.provenance))];
      return `<div class="grp">
        <div class="grp-h"><span class="lbl k">${KIND[e0.kind] || 'Source'}</span><b>${esc(g.name)}</b>${provs.length === 1 ? badge(provs[0]) : ''}
          <span class="path">${esc((e0.uri || e0.source).split('#')[0])}</span></div>
        ${g.items.map((e) => {
          const [text, sub] = e.kind === 'candidate_evidence' ? [e.label, e.excerpt] : [e.excerpt || e.label, e.kind === 'procedure' ? e.label.split(' · ')[1] : e.label];
          return `<button class="it" data-item="${esc(e.id)}" id="it-${esc(e.id)}">
            <span class="no">[${fnNo[e.id] || '–'}]</span><span>${esc(text)}${provs.length > 1 ? ' ' + badge(e.provenance) : ''}</span>
            ${sub && sub !== text ? `<span class="s">${esc(sub)}</span>` : ''}</button>`;
        }).join('')}
      </div>`;
    }).join('');
  }

  // ----- readiness bar -----
  function renderReady() {
    const d = W && W.draft, btn = $('btn-submit'), body = $('ready-body');
    btn.textContent = 'Submit for engineer review';
    if (!d) { btn.disabled = true; body.innerHTML = '<b>Submit for engineer review</b>Draft a work order first.'; return; }
    const rv = W.review;
    if (rv && rv.decision === 'approve') {
      btn.disabled = true; btn.textContent = 'Approved';
      body.innerHTML = `<b>Approved by ${esc(rv.reviewer)} at ${hhmmss(rv.at)}</b>v${rv.version} · ${esc(rv.hash)} · The draft is locked. <a href="review.html">Open Approve →</a>`;
      return;
    }
    if (W.submitted) {
      const s = W.submitted;
      btn.disabled = true; btn.textContent = 'Submitted';
      body.innerHTML = `<b>v${s.version} submitted for engineer review</b>${esc(s.hash)} · ${hhmmss(s.at)} · ${s.open_flags.length} flagged claim${s.open_flags.length === 1 ? '' : 's'} · Not approved yet. <a href="review.html">Open Approve →</a>`;
      return;
    }
    const flags = Object.keys(W.flags).length, p = d.fields.priority;
    const li = (cls, html) => `<li class="${cls}"><span class="ic">${cls === 'ok' ? '✓' : cls === 'info' ? '!' : ''}</span>${html}</li>`;
    body.innerHTML = `<ul class="checks">
      ${li('ok', `Draft <b>v${d.version}</b>`)}
      ${rv ? li('todo', `Returned by ${esc(rv.reviewer)}: edit or regenerate first`) : ''}
      ${li(p ? 'ok' : 'todo', p ? `Priority <b>${esc(p)}</b>` : 'Set a priority')}
      ${li(flags ? 'info' : 'ok', flags ? `${flags} flagged claim${flags > 1 ? 's' : ''} go${flags > 1 ? '' : 'es'} to the reviewer` : 'No flagged claims')}
      ${editing ? li('todo', 'Save or cancel your edits') : ''}
    </ul>`;
    btn.disabled = editing || !p || !!rv;
  }

  function renderAll() { lineIx = 0; if (W && W.draft) indexEvidence(W.draft); renderBrief(); renderDoc(); renderEvidence(); renderReady(); }

  async function act(fn, msgId) {
    const show = (t, err) => { const m = $(msgId || 'doc-msg'); if (m) { m.hidden = !t; m.textContent = t; m.classList.toggle('err', !!err); } };
    show('Working…');
    try {
      W = await fn();
      renderAll();
      window.AtelicShell.refresh();
      return true;
    } catch (e) {
      show(e.message, true);
      return false;
    }
  }
  // Lines slide in once, right after a draft is generated.
  function reveal() { document.body.classList.add('reveal'); setTimeout(() => document.body.classList.remove('reveal'), 1600); }

  // ----- citations: footnote <-> source, both ways -----
  function select(id, from) {
    document.querySelectorAll('.fn.on, .it.on, .ln.hl').forEach((el) => el.classList.remove('on', 'hl'));
    document.querySelectorAll(`.fn[data-ev="${CSS.escape(id)}"]`).forEach((el) => el.classList.add('on'));
    const lines = [...document.querySelectorAll('.ln')].filter((l) => l.dataset.cites.split(' ').includes(id));
    lines.forEach((l) => l.classList.add('hl'));
    const it = document.getElementById(`it-${id}`), box = $('evidence');
    if (it) {
      it.classList.add('on');
      box.scrollTo({ top: it.offsetTop - box.offsetTop - box.clientHeight / 3, behavior: 'smooth' });
    }
    if (from === 'rail' && lines[0]) {
      const r = lines[0].getBoundingClientRect();
      if (r.top < 70 || r.bottom > innerHeight - 100) lines[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
  const pop = $('pop');
  function showPop(btn) {
    const e = evById[btn.dataset.ev];
    if (!e) return;
    const text = e.kind === 'candidate_evidence' ? e.label : (e.excerpt || e.label);
    pop.innerHTML = `<div class="h">[${fnNo[e.id]}] ${esc(e.group)}</div>${esc(text)}<div class="p">${e.provenance === 'synthetic' || e.provenance === 'simulated' ? '' : `${esc(BADGE[e.provenance] || e.provenance)} · `}${esc((e.uri || e.source))}</div>`;
    const r = btn.getBoundingClientRect();
    pop.classList.add('open');
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    pop.style.top = `${r.top - h - 8 < 56 ? r.bottom + 8 : r.top - h - 8}px`;
  }
  const hidePop = () => pop.classList.remove('open');
  document.addEventListener('mouseover', (e) => { const b = e.target.closest('.fn'); if (b) showPop(b); });
  document.addEventListener('mouseout', (e) => { if (e.target.closest('.fn')) hidePop(); });
  document.addEventListener('focusin', (e) => { if (e.target.matches('.fn')) showPop(e.target); });
  document.addEventListener('focusout', (e) => { if (e.target.matches('.fn')) hidePop(); });
  addEventListener('scroll', hidePop, { passive: true });

  // ----- modal (flag, regenerate, submit) -----
  let onOk = null;
  function openModal(title, html, okLabel, fn) {
    $('modal-title').textContent = title;
    $('modal-body').innerHTML = html;
    $('modal-ok').textContent = okLabel;
    onOk = fn;
    $('modal').classList.add('open');
    ($('modal-body').querySelector('textarea') || $('modal-ok')).focus();
  }
  const closeModal = () => { $('modal').classList.remove('open'); onOk = null; };
  $('modal-cancel').addEventListener('click', closeModal);
  $('modal').addEventListener('click', (e) => { if (e.target === $('modal')) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('modal').classList.contains('open')) closeModal(); });
  // closeModal only hides the dialog, so a callback can still read its fields.
  $('modal-ok').addEventListener('click', async () => { const fn = onOk; closeModal(); if (fn) await fn(); });

  const regenerate = () => openModal('Regenerate the draft?',
    '<p>A new version is built from the current evidence. Field edits and flags on the current version are replaced.</p>',
    'Regenerate', async () => { if (await act(() => post('/draft'))) reveal(); });

  document.addEventListener('click', async (e) => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.matches('.fn')) { hidePop(); select(t.dataset.ev, 'doc'); return; }
    if (t.matches('.it')) { select(t.dataset.item, 'rail'); return; }
    if (t.id === 'btn-draft') { if (await act(() => post('/draft'), 'draft-msg')) reveal(); return; }
    if (t.matches('[data-regen]') || t.id === 'btn-regen') { regenerate(); return; }
    if (t.dataset.prio) {
      if (editing) { editPriority = t.dataset.prio; t.parentNode.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === t))); return; }
      act(() => post('/edit', { base_version: W.draft.version, fields: { priority: t.dataset.prio } }));
      return;
    }
    if (t.dataset.flag) {
      const id = t.dataset.flag;
      if (W.flags[id]) { act(() => post('/flag', { claim_id: id, flagged: false })); return; }
      const c = claims().find((x) => x.id === id);
      openModal('Flag claim as unsupported', `<blockquote>${esc(c.text)}</blockquote>
        <label for="flag-note">Why is it unsupported? (optional, shown to the reviewer)</label>
        <textarea id="flag-note" rows="3"></textarea>`, 'Flag claim',
        () => act(() => post('/flag', { claim_id: id, note: $('flag-note').value, flagged: true })));
      return;
    }
    if (t.dataset.unflag) { act(() => post('/flag', { claim_id: t.dataset.unflag, flagged: false })); }
  });

  $('btn-edit').addEventListener('click', async () => {
    if (!editing) { editing = true; editPriority = W.draft.fields.priority; renderDoc(); renderReady(); return; }
    const fields = { priority: editPriority || '' };
    document.querySelectorAll('[data-f]').forEach((el) => {
      fields[el.dataset.f] = el.dataset.f === 'required_permits' ? el.value.split('\n') : el.value;
    });
    editing = false;
    if (!(await act(() => post('/edit', { base_version: W.draft.version, fields })))) { editing = true; renderReady(); }
  });
  $('btn-cancel').addEventListener('click', () => { editing = false; renderAll(); });

  $('btn-submit').addEventListener('click', () => {
    const d = W.draft, flags = Object.keys(W.flags).length;
    openModal('Submit for engineer review?', `<div class="kv">
        <span>Draft</span><span>v${d.version} · ${esc(d.hash)}</span>
        <span>Asset</span><span>${esc(d.fields.asset || '—')}</span>
        <span>Priority</span><span>${esc(d.fields.priority)}</span>
        <span>Flagged claims</span><span>${flags}</span></div>
      <p>An engineer reviews this draft on the next screen. Submitting does not approve it. No equipment action will be executed.</p>`,
      'Submit for engineer review', () => act(() => post('/submit', { version: d.version })));
  });

  async function init() {
    try { W = await get(`/incidents/${INCIDENT_ID}/work-order`); } catch (e) { console.error(e); W = null; }
    renderAll();
  }
  init();
})();
