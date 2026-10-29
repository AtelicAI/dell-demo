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
      <span>${agent.replayed ? 'Recorded NVIDIA Nemotron run, replayed with live read-only tools. Questions and research run live.'
        : 'Live · NVIDIA Nemotron agents with read-only tools.'} This page updates by itself.</span></div>`;
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
      <div class="inv-h"><span class="lbl">Investigation agent${inv.round > 1 ? ` · research round ${inv.round}` : ''}</span>
        <span class="inv-by">${esc(inv.source_origin === 'llm' ? `${(inv.model || '').split('/').pop()} · ${inv.deployment === 'hosted_endpoint' ? 'NVIDIA hosted endpoint' : inv.deployment || ''}` : 'Deterministic template (model unavailable)')}${inv.replayed ? ' · recorded run' : ''}${inv.recorded_at ? ` · ${hm(inv.recorded_at)}` : ''}</span>
        <span class="inv-n">${n('contradiction')} contradiction${n('contradiction') === 1 ? '' : 's'} · ${n('gap')} gap${n('gap') === 1 ? '' : 's'} · ${n('supporting')} supporting</span></div>
      ${inv.round > 1 ? `<div class="inv-ch"><span class="lbl">What changed since round ${inv.round - 1}</span>
        ${inv.focus ? `<p>Focus from ${esc(inv.requested_by || 'the engineer')}: “${esc(inv.focus)}”</p>` : ''}
        <ul>${(inv.changes || []).map((c) => `<li>${esc(c)}</li>`).join('')}</ul></div>` : ''}
      <p class="inv-sum">${esc(inv.summary)}</p>
      ${inv.source_origin !== 'llm' ? '<p class="inv-tools">The model was unavailable, so this is a rule-based listing. Use Research again on Approve to rerun the investigation with the model.</p>' : ''}
      <ul class="inv-f">${fs.map((f) => {
        const [cls, label] = KIND[f.kind] || ['ctx', f.kind];
        return `<li class="${cls}"><span class="k">${label}</span><span class="t">${esc(f.statement)}${cites(f.evidence_ids)}</span></li>`;
      }).join('')}</ul>
      ${(inv.open_questions || []).length ? `<details class="inv-q"><summary>Open questions <b>${inv.open_questions.length}</b></summary><ul>${inv.open_questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul></details>` : ''}
      ${(inv.tools || []).length ? `<p class="inv-tools">Tools the agent called: ${[...new Set(inv.tools)].map((t) => `<code>${esc(t)}</code>`).join(' ')}</p>` : ''}
    </div>`;
  }

  function questions(list, canAsk) {
    const items = (list || []).slice().reverse().map((q) => `<li class="qa ${q.status}">
      <div class="qa-q"><b>${esc(q.asked_by)}</b> asked ${hm(q.asked_at)}<p>${esc(q.question)}</p></div>
      ${q.status === 'answered' && q.source_origin !== 'llm' ? `<div class="qa-a"><span class="lbl">Not answered</span>
        <p>The model was unavailable when this was asked, so no answer was generated.${canAsk ? ` <button class="linkish" type="button" data-ask="${esc(q.question)}">Ask again</button>` : ''}</p></div>`
      : q.status === 'answered' ? `<div class="qa-a"><span class="lbl">Agent answer · Nemotron</span><p>${esc(q.answer)}</p>
        ${q.cannot_determine ? `<p class="qa-cd"><b>Cannot determine from the evidence:</b> ${esc(Array.isArray(q.cannot_determine) ? q.cannot_determine.join('; ') : q.cannot_determine)}</p>` : ''}
        ${(q.suggested_follow_up || []).length ? `<ul class="qa-fu">${q.suggested_follow_up.map((x) => {
          const t = typeof x === 'string' ? x : x.statement || JSON.stringify(x);
          return `<li>${esc(t)}${canAsk ? ` <button class="linkish" type="button" data-ask="${esc(t)}">Ask this</button>` : ''}</li>`;
        }).join('')}</ul>` : ''}</div>`
        : '<div class="qa-a pending"><span class="ag-spin"></span>The agent is reading the frozen evidence…</div>'}
    </li>`).join('');
    return `<div class="qa-box">
      <div class="qa-h"><span class="lbl">Ask the agent</span><span>Follow-up questions keep the conversation. Answers use the frozen evidence and your field notes, and never change the draft.</span></div>
      ${canAsk ? `<div class="qa-form"><textarea id="qa-text" rows="2" placeholder="e.g. WO-009 says the gland was re-torqued. Why is XV-2104 still the leading candidate?"></textarea>
        <button class="btn sm" id="qa-send" type="button">Ask</button><span class="qa-msg" id="qa-msg" role="status"></span></div>`
        : '<p class="qa-off">Questions open while a draft awaits review, and before closure.</p>'}
      ${items ? `<ul class="qa-list">${items}</ul>` : ''}</div>`;
  }

  // Further research before approval: field notes the engineer adds, and another investigation round that the
  // planner turns into a revised draft (which then needs approval again).
  function research(W) {
    const notes = W.field_notes || [], r = W.research || {};
    const canNote = W.can_ask;
    const list = notes.length ? `<ul class="fn-list">${notes.map((n) => `<li><code>${esc(n.id.split(':')[1])}</code><span><b>${esc(n.recorded_by)}</b> · ${hm(n.recorded_at)}<p>${esc(n.text)}</p></span></li>`).join('')}</ul>` : '';
    const running = r.requested ? `<div class="ag-banner" role="status"><span class="ag-spin" aria-hidden="true"></span><b>Research round ${esc(r.round)} under way</b>
      <span>The investigator re-reads the evidence with ${notes.length} field note${notes.length === 1 ? '' : 's'} and the answers so far; the planner then revises the draft. Approval waits for the new draft.</span></div>` : '';
    return `<div class="rs-box">
      <div class="qa-h"><span class="lbl">Field notes</span><span>What you or the crew saw on site. Added beside the frozen evidence, labelled as unverified human observations; agents read them from then on.</span></div>
      ${list}
      ${canNote ? `<div class="qa-form"><textarea id="fn-text" rows="2" placeholder="e.g. Soap test on F-205 flange: no bubbles. Hissing heard near the XV-2104 stem."></textarea>
        <button class="btn sm" id="fn-send" type="button">Add field note</button><span class="qa-msg" id="fn-msg" role="status"></span></div>` : ''}
      ${running || (r.can_request ? `<div class="rs-go"><div class="qa-h"><span class="lbl">Research again</span><span>The investigator runs round ${esc(r.next_round)} with your field notes and the answers so far, shows what changed, and the planner revises the draft. The current draft is withdrawn until then.</span></div>
        <div class="qa-form"><textarea id="rs-focus" rows="2" placeholder="Optional focus, e.g. Recheck F-205 against the soap test and the K-201 load change."></textarea>
        <button class="btn sm" id="rs-send" type="button">Research again</button><span class="qa-msg" id="rs-msg" role="status"></span></div></div>` : '')}
    </div>`;
  }

  async function send(path, body, msgId) {
    const msg = document.getElementById(msgId);
    if (msg) msg.textContent = 'Sending…';
    const r = await fetch(`${window.AtelicShell.API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { if (msg) msg.textContent = typeof data.detail === 'string' ? data.detail : 'Could not send'; return null; }
    return data;
  }

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

  window.AtelicStory = { agentBanner, investigation, questions, research, send, ask, poll, followUp, answerKey, esc };
})();
