// Shared pieces for the agent story: live agent banner, investigation findings, engineer Q&A, plant history,
// repair → verification → closure, and the answer key. Everything renders backend values; nothing is invented.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hm = (t) => (t ? `${t.slice(11, 16)} UTC` : '');
  const KIND = { supporting: ['for', 'Supports'], contradiction: ['con', 'Contradiction'], gap: ['gap', 'Gap'], context: ['ctx', 'Context'] };

  // Live status of the engine's agents (orchestrator). Shown while they work, or when they failed.
  function agentBanner(agent, opts = {}) {
    if (!agent) return '';
    if (agent.failed) {
      return `<div class="ag-banner bad" role="alert"><b>Agents stopped</b><span>${esc(agent.detail || agent.status)}</span>
        ${opts.restart ? '<button class="btn sm" data-ag-restart>Restart agents</button>' : ''}</div>`;
    }
    if (agent.status !== 'running') return '';
    return `<div class="ag-banner" role="status"><span class="ag-spin" aria-hidden="true"></span><b>${esc(agent.text || 'Agents working')}</b>
      <span>Live · NVIDIA Nemotron agents with read-only tools. This page updates by itself.</span></div>`;
  }

  function cites(ids) {
    return (ids || []).length ? `<span class="st-cites">${ids.map((i) => `<code title="${esc(i)}">${esc(i.split(':').slice(1).join(':').split('#')[0] || i)}</code>`).join('')}</span>` : '';
  }

  function investigation(inv) {
    if (!inv) return '';
    const order = ['contradiction', 'gap', 'supporting', 'context'];
    const fs = (inv.findings || []).slice().sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
    const n = (k) => fs.filter((f) => f.kind === k).length;
    return `<div class="inv">
      <div class="inv-h"><span class="lbl">Investigation agent</span>
        <span class="inv-by">${esc(inv.source_origin === 'llm' ? `${(inv.model || '').split('/').pop()} · ${inv.deployment === 'hosted_endpoint' ? 'NVIDIA hosted endpoint' : inv.deployment || ''}` : 'Deterministic template (model unavailable)')}${inv.recorded_at ? ` · ${hm(inv.recorded_at)}` : ''}</span>
        <span class="inv-n">${n('contradiction')} contradictions · ${n('gap')} gaps · ${n('supporting')} supporting</span></div>
      <p class="inv-sum">${esc(inv.summary)}</p>
      <ul class="inv-f">${fs.map((f) => {
        const [cls, label] = KIND[f.kind] || ['ctx', f.kind];
        return `<li class="${cls}"><span class="k">${label}</span><span class="t">${esc(f.statement)}${cites(f.evidence_ids)}</span></li>`;
      }).join('')}</ul>
      ${(inv.open_questions || []).length ? `<details class="inv-q"><summary>Open questions <b>${inv.open_questions.length}</b></summary><ul>${inv.open_questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul></details>` : ''}
      ${(inv.tools || []).length ? `<p class="inv-tools">Tools the agent called: ${[...new Set(inv.tools)].map((t) => `<code>${esc(t)}</code>`).join(' ')}</p>` : ''}
    </div>`;
  }

  // ----- "Add to plan" tray: items an engineer picked from agent answers, kept per tab and per engine incident.
  // Nothing here changes the draft; a named reviewer sends the items to the planner with Request changes.
  const trayKey = (k) => `atelic.tray.${k}`;
  function trayGet(k) {
    if (!k) return [];
    try { return JSON.parse(sessionStorage.getItem(trayKey(k)) || '[]'); } catch (e) { return []; }
  }
  function traySet(k, items) {
    try { sessionStorage.setItem(trayKey(k), JSON.stringify(items)); } catch (e) { /* storage blocked: tray lives for this render only */ }
    document.dispatchEvent(new CustomEvent('atelic:tray'));
  }
  const trayText = (items) => items.map((x, i) => `${i + 1}. ${x.text}`).join('\n');

  function trayBox(k, opts = {}) {
    const items = trayGet(k);
    if (!items.length) return '';
    return `<div class="tray" aria-label="Changes to ask the planner for">
      <div class="tray-h"><span class="lbl">Changes to ask the planner for</span><b>${items.length}</b>
        <span>Picked from agent answers. They change nothing until a named reviewer sends them with Request changes.</span></div>
      <ol>${items.map((x, i) => `<li><span>${esc(x.text)}</span>${x.from ? `<span class="tray-from">from ${esc(x.from)}</span>` : ''}
        <button type="button" class="tray-x" data-tray-remove="${i}" aria-label="Remove">×</button></li>`).join('')}</ol>
      ${opts.send ? '<div class="tray-acts"><button type="button" class="btn sm" data-tray-send>Request these changes…</button><span class="qa-msg" id="tray-msg" role="status"></span></div>'
        : opts.sendHref ? `<p class="tray-note">Send them from <a href="${esc(opts.sendHref)}">Approve →</a> with Request changes.</p>` : ''}
    </div>`;
  }

  const CHIPS = [
    'WO-009 says the gland was re-torqued. Why is XV-2104 still the leading candidate?',
    'Could F-205 be the source instead? What would rule it out?',
    'What is still unknown, and which check would confirm the source fastest?',
  ];
  const since = (t) => {
    const s = Math.max(0, Math.round((Date.now() - Date.parse(t)) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };

  // Simulated / synthetic provenance stays in the data but is not shown (team decision, 26 Oct).
  const SHOWN_PROV = { model_estimated: 'Model estimated', observed: 'Observed', experimental_setpoint: 'Experimental setpoint', provider_reported: 'Provider reported' };
  function answerBody(q, tray) {
    const src = q.sources || {}, no = {};
    let n = 0;
    (q.claims || []).forEach((c) => (c.evidence_ids || []).forEach((id) => { if (!no[id]) no[id] = ++n; }));
    const sup = (ids) => (ids || []).map((id) => {
      const e = src[id] || {};
      return `<sup class="qa-c" title="${esc([e.group, e.label, e.excerpt].filter(Boolean).join(' · ') || id)}">${no[id]}</sup>`;
    }).join('');
    const picked = new Set(trayGet(tray).map((x) => x.text));
    const fu = (q.suggested_follow_up || []).map((x) => (typeof x === 'string' ? x : x.statement || '')).filter(Boolean);
    const cd = q.cannot_determine === true ? 'The agent could not answer this from the frozen evidence.'
      : Array.isArray(q.cannot_determine) ? q.cannot_determine.join('; ') : typeof q.cannot_determine === 'string' ? q.cannot_determine : '';
    return `<div class="qa-a"><span class="lbl">${esc(q.source_origin === 'llm' ? 'Agent answer · Nemotron' : 'Answer · deterministic template (no model)')}</span><p>${esc(q.answer)}</p>
      ${cd ? `<p class="qa-cd"><b>Cannot determine:</b> ${esc(cd)}</p>` : ''}
      ${(q.claims || []).length ? `<ul class="qa-cl">${q.claims.map((c) => `<li>${esc(c.statement)}${sup(c.evidence_ids)}</li>`).join('')}</ul>` : ''}
      ${n ? `<details class="qa-srcs"><summary>Sources <b>${n}</b></summary><ol class="qa-src">${Object.keys(no).map((id) => { const e = src[id] || {}, p = SHOWN_PROV[e.provenance];
        return `<li value="${no[id]}"><b>${esc(e.group || id)}</b> ${esc(e.label && e.label !== id ? e.label : '')}${p ? ` <span class="qa-prov">${p}</span>` : ''}</li>`; }).join('')}</ol></details>` : ''}
      ${fu.length ? `<div class="qa-fu"><span class="lbl">Suggested next steps</span><ul>${fu.map((t) => `<li><span>${esc(t)}</span>${tray ? (picked.has(t)
        ? '<span class="qa-added">✓ Added</span>'
        : `<button type="button" class="btn sm ghost" data-tray-add="${esc(t)}" data-tray-from="${esc(q.asked_by)}">+ Add to plan</button>`) : ''}</li>`).join('')}</ul></div>` : ''}
    </div>`;
  }

  // opts.tray: the tray key (enables "Add to plan"); opts.offText: why questions are closed right now.
  function questions(list, canAsk, opts = {}) {
    const items = (list || []).slice().reverse().map((q) => `<li class="qa ${q.status}">
      <div class="qa-q"><b>${esc(q.asked_by)}</b> asked ${hm(q.asked_at)}<p>${esc(q.question)}</p></div>
      ${q.status === 'answered' ? answerBody(q, opts.tray)
        : `<div class="qa-a pending"><span class="ag-spin"></span>The agent is reading the frozen evidence…${q.asked_at ? ` <span class="qa-t" data-since="${esc(q.asked_at)}">${since(q.asked_at)}</span>` : ''}<span class="qa-tn">usually 30–80 s</span></div>`}
    </li>`).join('');
    const asked = new Set((list || []).map((q) => q.question));
    const chips = CHIPS.filter((c) => !asked.has(c));
    return `<div class="qa-box">
      <div class="qa-h"><span class="lbl">Ask the agent</span><span>Answers come only from the frozen evidence and never change the draft.</span></div>
      ${canAsk ? `<div class="qa-form">${chips.length ? `<div class="qa-chips">${chips.map((c) => `<button type="button" class="qa-chip" data-qa-chip="${esc(c)}">${esc(c)}</button>`).join('')}</div>` : ''}
        <textarea id="qa-text" rows="2" placeholder="Ask about the evidence, the candidates or the draft"></textarea>
        <button class="btn sm" id="qa-send" type="button">Ask</button><span class="qa-msg" id="qa-msg" role="status"></span></div>`
        : `<p class="qa-off">${esc(opts.offText || 'Questions open while a draft awaits review, and before closure.')}</p>`}
      ${items ? `<ul class="qa-list">${items}</ul>` : ''}</div>`;
  }

  // What a planner revision changed, line by line per section (text compared after trimming).
  const SEC = { observed: 'What we observed', uncertain: 'What remains uncertain', inspection: 'Inspection', isolation: 'Isolation', safety: 'Safety' };
  function revisionDiff(w) {
    const prev = w && w.previous_draft, cur = w && w.draft;
    if (!prev || !cur) return '';
    const norm = (t) => String(t || '').trim().replace(/\s+/g, ' ');
    let added = 0, removed = 0;
    const rows = Object.keys(SEC).map((k) => {
      const a = (prev.sections[k] || []).map((c) => norm(c.text)), b = (cur.sections[k] || []).map((c) => norm(c.text));
      const plus = b.filter((t) => !a.includes(t)), minus = a.filter((t) => !b.includes(t));
      added += plus.length; removed += minus.length;
      if (!plus.length && !minus.length) return '';
      return `<div class="rd-sec"><h4>${SEC[k]}</h4><ul>${plus.map((t) => `<li class="add"><span>+</span>${esc(t)}</li>`).join('')}${minus.map((t) => `<li class="del"><span>−</span>${esc(t)}</li>`).join('')}</ul></div>`;
    }).join('');
    return `<details class="rev-diff" open><summary><span class="lbl">Changed in this revision</span>
      <b>planner draft ${prev.engine_draft_version} → ${cur.engine_draft_version}</b><span>${added} added · ${removed} removed</span></summary>
      ${rows || '<p class="qa-off">Same lines as the previous draft.</p>'}</details>`;
  }

  // Chips fill the question box; tray add/remove; pending-answer timers tick every second.
  document.addEventListener('click', (e) => {
    const chip = e.target.closest('[data-qa-chip]');
    if (chip) { const box = document.getElementById('qa-text'); if (box) { box.value = chip.dataset.qaChip; box.focus(); } return; }
    const add = e.target.closest('[data-tray-add]');
    if (add) {
      const k = add.closest('[data-tray-key]') && add.closest('[data-tray-key]').dataset.trayKey;
      const items = trayGet(k);
      if (k && !items.some((x) => x.text === add.dataset.trayAdd)) traySet(k, [...items, { text: add.dataset.trayAdd, from: `answer to ${add.dataset.trayFrom || 'Engineer'}` }]);
      return;
    }
    const rm = e.target.closest('[data-tray-remove]');
    if (rm) {
      const k = rm.closest('[data-tray-key]') && rm.closest('[data-tray-key]').dataset.trayKey;
      traySet(k, trayGet(k).filter((_, i) => i !== Number(rm.dataset.trayRemove)));
    }
  });
  setInterval(() => document.querySelectorAll('[data-since]').forEach((el) => (el.textContent = since(el.dataset.since))), 1000);

  const tray = { get: trayGet, set: traySet, box: trayBox, text: trayText, key: (inc, w) => (w && w.engine_incident_id ? `${inc}.${w.engine_incident_id}` : null) };

  async function ask(incidentId, name) {
    const box = document.getElementById('qa-text'), msg = document.getElementById('qa-msg');
    const question = box && box.value.trim();
    if (!question) return null;
    msg.textContent = 'Sending…';
    const r = await fetch(`${window.AtelicShell.API}/incidents/${incidentId}/questions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, asked_by: name || 'Engineer' }),
    });
    const data = await r.json();
    if (!r.ok) { msg.textContent = typeof data.detail === 'string' ? data.detail : 'Could not send'; return null; }
    msg.textContent = 'Sent. The answer appears here in about a minute.';
    return data;
  }

  // Poll a loader while a condition holds (agents running, an answer pending). Stops when the tab is hidden.
  function poll(load, keepGoing, ms = 4000) {
    let t = null;
    const tick = async () => {
      if (!document.hidden) {
        const v = await load().catch(() => null);
        if (v && !keepGoing(v)) return;
      }
      t = setTimeout(tick, ms);
    };
    t = setTimeout(tick, ms);
    return () => clearTimeout(t);
  }

  function followUp(fu, name) {
    if (!fu) return '';
    const v = fu.verification, rep = fu.repair, cl = fu.closure;
    const checks = (fu.approved_checks || []).map((c, i) => `(${i + 1}) ${c}`).join('\n');
    const tpl = `Field crew completed the approved checks:\n${checks}\nResult: `;
    return `<div class="fu">
      <ol class="fu-steps">
        <li class="${rep ? 'done' : fu.can_record_repair ? 'now' : ''}"><b>Record the repair</b><span>Human account of the field work. The system performs no physical action.</span></li>
        <li class="${v ? 'done' : rep ? 'now' : ''}"><b>Verifier agent</b><span>Compares post-repair readings with the pre-alert baseline.</span></li>
        <li class="${cl ? 'done' : fu.can_close ? 'now' : ''}"><b>Close or keep open</b><span>An engineer decides; a closed incident is written to the knowledge graph.</span></li>
      </ol>
      ${rep ? `<div class="fu-rec"><span class="lbl">Repair record · ${hm(rep.at)}</span><p>${esc(rep.work_performed)}</p></div>` : ''}
      ${fu.can_record_repair && !rep ? `<div class="fu-form"><label for="fu-by">Your name</label><input id="fu-by" value="${esc(name)}" maxlength="80">
        <label for="fu-text">Work performed (edit before sending)</label><textarea id="fu-text" rows="7">${esc(tpl)}</textarea>
        <button class="btn primary" id="fu-repair" type="button">Record repair</button><span class="qa-msg" id="fu-msg" role="status"></span></div>` : ''}
      ${rep && !v ? '<div class="ag-banner"><span class="ag-spin"></span><b>Verifier agent checking post-repair readings…</b></div>' : ''}
      ${v ? `<div class="fu-ver ${v.recommendation === 'close' ? 'ok' : 'warn'}"><span class="lbl">Verifier · ${esc(v.source_origin === 'llm' ? 'Nemotron' : 'template')}</span>
        <p class="big">Recommends: <b>${esc((v.recommendation || '').replace('_', ' '))}</b> · readings ${esc((v.recovery_check || '').replace(/_/g, ' '))}${v.consistency === 'conflicts_with_deterministic_check' ? ' · <b class="bad">disagrees with the physics check</b>' : ''}</p>
        <p>${esc(v.summary)}</p>
        ${(v.sensors || []).length ? `<table class="fu-s"><tr><th>Sensor</th><th>Baseline max</th><th>Post-repair max</th><th></th></tr>${v.sensors.map((x) => `<tr><td>${esc(x.sensor_id)}</td><td>${x.baseline_max_ppm.toFixed(2)} ppm</td><td>${x.post_repair_max_ppm.toFixed(2)} ppm</td><td>${x.within_baseline ? '✓ within baseline' : '✕ above'}</td></tr>`).join('')}</table>` : ''}
        ${(v.residual_risks || []).length ? `<details><summary>Residual risks <b>${v.residual_risks.length}</b></summary><ul>${v.residual_risks.map((r) => `<li>${esc(typeof r === 'string' ? r : r.statement || '')}</li>`).join('')}</ul></details>` : ''}
        <p class="fu-warn">${esc(v.warning || '')}</p></div>` : ''}
      ${fu.can_close ? `<div class="fu-form"><label for="fu-cby">Your name</label><input id="fu-cby" value="${esc(name)}" maxlength="80">
        <label for="fu-why">Reason</label><textarea id="fu-why" rows="2">Verifier and post-repair readings agree; approved checks signed off.</textarea>
        <div class="acts"><button class="btn go" data-close="close" type="button">Close incident</button><button class="btn" data-close="keep_open" type="button">Keep open</button></div>
        <span class="qa-msg" id="fu-cmsg" role="status"></span></div>` : ''}
      ${cl ? `<div class="fu-ver ok"><span class="lbl">Closure · ${hm(cl.at)}</span><p class="big"><b>${cl.decision === 'close' ? 'Closed' : 'Kept open'}</b></p><p>${esc(cl.rationale)}</p>
        ${fu.graph_memory ? `<p class="fu-mem">Knowledge graph: ${esc(fu.graph_memory.status)} ${esc(fu.graph_memory.node || '')}. The next investigation on these assets will see this incident.</p>` : ''}</div>` : ''}
    </div>`;
  }

  function answerKey(key, est) {
    if (!key) return '<p class="ak-locked">Locked until the report is issued, so it cannot anchor the review.</p>';
    const f = key.footage || {};
    const metered = f.metered_rate_g_h != null ? f.metered_rate_g_h / 1000 : null;
    const e = (key.estimate || {}).rate_g_h != null ? key.estimate.rate_g_h / 1000 : est;
    return `<div class="ak">
      <div class="ak-n"><span class="lbl">Metered release on camera</span><b>${metered ? (metered * 1000).toFixed(1) : '—'}</b><span>g/h · ${esc(f.site ? 'METEC' : '')} GasVid class ${esc(f.gasvid_class)} ± ${esc(f.ci95_g_h)}</span></div>
      <div class="ak-n"><span class="lbl">Engine estimate</span><b>${e ? (e * 1000).toFixed(1) : '—'}</b><span>g/h · frozen before review</span></div>
      <div class="ak-n"><span class="lbl">Difference</span><b>${key.error_percent != null ? `${key.error_percent > 0 ? '+' : ''}${key.error_percent.toFixed(1)}%` : '—'}</b><span>estimate vs metered</span></div>
      <p class="ak-note">${esc(key.plant_alignment || '')} ${esc(key.note || '')}</p>
      <p class="ak-src">${esc(f.camera || '')} · ${esc(f.recorded_date || '')} · ${esc(f.release_location || '')} · ${esc(f.source || '')}</p></div>`;
  }

  window.AtelicStory = { agentBanner, investigation, questions, ask, poll, followUp, answerKey, revisionDiff, tray, esc };
})();
