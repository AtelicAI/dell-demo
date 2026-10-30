// Shared pieces for the agent story: live agent banner, investigation findings, engineer Q&A, plant history,
// repair → verification → closure, and the answer key. Everything renders backend values; nothing is invented.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // Times of things done in this session (questions, notes, agent runs) on the replay clock, so they match the cameras.
  const hm = (t) => (t ? `${window.AtelicShell.toReplay(t).toISOString().slice(11, 16)} UTC` : '');
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
      ${trace(agent)}<span>${agent.replayed ? 'Recorded NVIDIA Nemotron run, replayed with live read-only tools. Questions and research run live.'
        : 'Live · NVIDIA Nemotron agents with read-only tools.'} This page updates by itself.</span></div>`;
  }

  // The running agent's steps so far (tools read, model turns), then the model working, with a live timer.
  const dur = (ms) => (ms >= 60000 ? `${Math.floor(ms / 60000)} m ${String(Math.round(ms % 60000 / 1000)).padStart(2, '0')} s` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`);
  function trace(agent) {
    const t = agent && agent.trace;
    if (!t) return '';
    const tools = t.done.filter((d) => !d.model && d.status === 'completed').map((d) => d.label);
    const turns = t.done.filter((d) => d.model);
    return `<ol class="ag-trace">
      ${tools.length ? `<li class="ok">Read ${esc([...new Set(tools)].join(' · '))}</li>` : ''}
      ${turns.map((d, i) => `<li class="ok">Nemotron turn ${i + 1} · ${dur(d.ms || 0)}</li>`).join('')}
      <li class="now"><span class="ag-spin" aria-hidden="true"></span>${turns.length || tools.length ? 'Nemotron reasoning over the evidence' : 'Starting'} · <span data-since="${esc(t.last)}"></span></li></ol>`;
  }
  setInterval(() => document.querySelectorAll('[data-since]').forEach((el) => { el.textContent = dur(Math.max(0, Date.now() - Date.parse(el.dataset.since))); }), 1000);

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
  // After the repair, questions go to the verifier's evidence (Report).
  const VERIFY_CHIPS = [
    'Are all the sensors back to the pre-incident baseline?',
    'What residual risk remains after the repair?',
  ];
  // Offline laptop build: Request changes offers these reasons instead of free text (their revisions are recorded).
  const REVIEW_REASONS = [
    'Before any packing work, add a check of the missing F-205 bolt torque record from WO-004, and make the OGI re-survey requested in WO-008 the first field check.',
  ];

  // Simulated / synthetic provenance stays in the data but is not shown (team decision, 26 Oct).
  const SHOWN_PROV = { model_estimated: 'Model estimated', observed: 'Observed', experimental_setpoint: 'Experimental setpoint', provider_reported: 'Provider reported' };
  function answerBody(q, tray, canAsk) {
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
    return `<div class="qa-a"><span class="lbl">Agent answer · Nemotron</span><p>${esc(q.answer)}</p>
      ${cd ? `<p class="qa-cd"><b>Cannot determine from the evidence:</b> ${esc(cd)}</p>` : ''}
      ${(q.claims || []).length ? `<ul class="qa-cl">${q.claims.map((c) => `<li>${esc(c.statement)}${sup(c.evidence_ids)}</li>`).join('')}</ul>` : ''}
      ${n ? `<details class="qa-srcs"><summary>Sources <b>${n}</b></summary><ol class="qa-src">${Object.keys(no).map((id) => { const e = src[id] || {}, p = SHOWN_PROV[e.provenance];
        return `<li value="${no[id]}"><b>${esc(e.group || id)}</b> ${esc(e.label && e.label !== id ? e.label : '')}${p ? ` <span class="qa-prov">${p}</span>` : ''}</li>`; }).join('')}</ol></details>` : ''}
      ${fu.length ? `<div class="qa-fu"><span class="lbl">Suggested next steps</span><ul>${fu.map((t) => `<li><span class="qa-fu-t">${esc(t)}</span>
        <span class="qa-fu-acts">${canAsk ? `<button class="linkish" type="button" data-ask="${esc(t)}">Ask this</button>` : ''}${tray ? (picked.has(t)
          ? `<button type="button" class="btn sm qa-added" data-tray-undo="${esc(t)}" title="Remove from the changes to ask for"><span class="on">✓ Added</span><span class="off">Undo</span></button>`
          : `<button type="button" class="btn sm ghost" data-tray-add="${esc(t)}" data-tray-from="${esc(q.asked_by)}">+ Add to plan</button>`) : ''}</span></li>`).join('')}</ul></div>` : ''}
    </div>`;
  }

  // opts.agent: live engine agent (answer trace); opts.tray: the tray key (enables "Add to plan");
  // opts.offText: why questions are closed right now; opts.chips: suggested questions (default CHIPS);
  // opts.offline: the offline build, where the suggestions are the only questions and send on click.
  function questions(list, canAsk, opts = {}) {
    const agent = opts.agent;
    // The answer agent works the oldest unanswered question first; its live steps show there.
    const working = agent && agent.current_step === 'answer' ? ((list || []).find((q) => q.status !== 'answered') || {}).id : null;
    const items = (list || []).slice().reverse().map((q) => `<li class="qa ${q.status}">
      <div class="qa-q"><b>${esc(q.asked_by)}</b> asked ${hm(q.asked_at)}<p>${esc(q.question)}</p></div>
      ${q.status === 'answered' && q.source_origin !== 'llm' ? `<div class="qa-a"><span class="lbl">Not answered</span>
        <p>The model was unavailable when this was asked, so no answer was generated.${canAsk ? ` <button class="linkish" type="button" data-ask="${esc(q.question)}">Ask again</button>` : ''}</p></div>`
      : q.status === 'answered' ? answerBody(q, opts.tray, canAsk)
        : q.id === working ? `<div class="qa-a pending live">${trace(agent)}</div>`
        : `<div class="qa-a pending"><span class="ag-spin"></span>Queued: the agent answers questions in order…<span class="qa-tn">usually 30–80 s each</span></div>`}
    </li>`).join('');
    const asked = new Set((list || []).map((q) => q.question));
    const chips = (opts.chips || CHIPS).filter((c) => !asked.has(c));
    const off = opts.offline;
    return `<div class="qa-box">
      <div class="qa-h"><span class="lbl">Ask the agent</span><span>${off ? 'Offline demo: pick a question; the agent\'s recorded answer to it replays here.'
        : 'Follow-up questions keep the conversation. Answers use the frozen evidence and your field notes, and never change the draft.'}</span></div>
      ${canAsk ? `<div class="qa-form"${off ? ' data-offline' : ''}>${chips.length ? `<div class="qa-chips">${chips.map((c) => `<button type="button" class="qa-chip" data-qa-chip="${esc(c)}">${esc(c)}</button>`).join('')}</div>`
          : off ? '<p class="qa-off">Every recorded question has been asked.</p>' : ''}
        <textarea id="qa-text" rows="2" placeholder="Ask about the evidence, the candidates or the draft"${off ? ' hidden' : ''}></textarea>
        <button class="btn sm" id="qa-send" type="button"${off ? ' hidden' : ''}>Ask</button><span class="qa-msg" id="qa-msg" role="status"></span></div>`
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

  // Chips and Ask this / Ask again fill the question box; tray add/remove.
  document.addEventListener('click', (e) => {
    const chip = e.target.closest('[data-qa-chip], [data-ask]');
    if (chip) {
      const box = document.getElementById('qa-text');
      if (!box) return;
      box.value = chip.dataset.qaChip || chip.dataset.ask;
      // Offline there is no text box to edit: a suggestion is sent as it is.
      if (box.closest('[data-offline]')) document.getElementById('qa-send').click(); else box.focus();
      return;
    }
    const add = e.target.closest('[data-tray-add]');
    if (add) {
      const k = add.closest('[data-tray-key]') && add.closest('[data-tray-key]').dataset.trayKey;
      const items = trayGet(k);
      if (k && !items.some((x) => x.text === add.dataset.trayAdd)) traySet(k, [...items, { text: add.dataset.trayAdd, from: `answer to ${add.dataset.trayFrom || 'Engineer'}` }]);
      return;
    }
    const undo = e.target.closest('[data-tray-undo]');
    if (undo) {
      const k = undo.closest('[data-tray-key]') && undo.closest('[data-tray-key]').dataset.trayKey;
      traySet(k, trayGet(k).filter((x) => x.text !== undo.dataset.trayUndo));
      return;
    }
    const rm = e.target.closest('[data-tray-remove]');
    if (rm) {
      const k = rm.closest('[data-tray-key]') && rm.closest('[data-tray-key]').dataset.trayKey;
      traySet(k, trayGet(k).filter((_, i) => i !== Number(rm.dataset.trayRemove)));
    }
  });

  const tray = { get: trayGet, set: traySet, box: trayBox, text: trayText, key: (inc, w) => (w && w.engine_incident_id ? `${inc}.${w.engine_incident_id}` : null) };

  // Further research before approval: field notes the engineer adds, and another investigation round that the
  // planner turns into a revised draft (which then needs approval again).
  function research(W) {
    const notes = W.field_notes || [], r = W.research || {};
    // Offline, research rounds and field notes are left out: their agent runs answer free text, which is not recorded.
    if (W.offline) return '';
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

  // "Ask the agent ↓": floats while the question box is below the screen, scrolls to it on click, and hides as soon
  // as the box is in view (also when the engineer scrolls there). Sits above Plan's sticky readiness bar.
  (function jumpToAsk() {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'qa-jump'; btn.hidden = true;
    btn.innerHTML = '<span>Ask the agent</span><span aria-hidden="true">↓</span>';
    btn.addEventListener('click', () => {
      const box = document.getElementById('qa');
      if (!box) return;
      box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const t = document.getElementById('qa-text');
      if (t) setTimeout(() => t.focus({ preventScroll: true }), 450);
    });
    let queued = false;
    const update = () => {
      queued = false;
      const box = document.getElementById('qa');
      const shown = box && box.offsetParent !== null && box.querySelector('.qa-box');
      const below = shown && box.getBoundingClientRect().top > window.innerHeight - 80;
      if (btn.hidden === !!below) btn.hidden = !below; // only on change: the observer below watches 'hidden'
      if (below) {
        const bar = document.querySelector('.ready');
        btn.style.bottom = `${bar && bar.offsetParent !== null ? bar.offsetHeight + 26 : 22}px`;
      }
    };
    const later = () => { if (!queued) { queued = true; requestAnimationFrame(update); } };
    document.addEventListener('DOMContentLoaded', () => {
      document.body.appendChild(btn);
      window.addEventListener('scroll', later, { passive: true });
      window.addEventListener('resize', later);
      // The box fills in and re-renders as answers arrive.
      new MutationObserver(later).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
      later();
    });
  })();

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

  window.AtelicStory = { agentBanner, investigation, questions, research, send, ask, poll, followUp, answerKey, revisionDiff, tray, esc, VERIFY_CHIPS, REVIEW_REASONS };
})();
