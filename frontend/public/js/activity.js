// Screen 8 — Agent Activity / Glass box. Renders GET /incidents/:id/agent-runs: real spans recorded by
// the backend code that ran, plus the reason for every step that did not run. Replay only re-walks
// the recorded spans; it never adds one.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const { INCIDENT_ID, get } = window.AtelicShell;

  const STATUS = {
    ran: 'Ran', not_run: 'Not run', approved: 'Approved', waiting: 'Waiting for engineer',
    returned: 'Returned', not_reached: 'Not reached', blocked: 'Blocked', manual: 'Demo trigger',
  };
  const FILTERS = [['all', 'All'], ['ran', 'Ran'], ['not_run', 'Not run'], ['human', 'Human']];
  const EVENT = { submitted_for_review: 'Submitted for review', review_approve: 'Approved', review_request_changes: 'Changes requested', review_reject: 'Rejected',
    question_asked: 'Question asked', report_issued: 'Report issued', repair_recorded: 'Repair recorded', incident_closed: 'Closure decision' };
  const DELL = { running: 'Running', target_deployment: 'Target deployment' };
  const NVIDIA = { local_nim: 'Local NIM', hosted_api: 'Hosted API', not_connected: 'Not connected' };
  const hms = (t) => (t ? t.slice(11, 19) : '—');
  const human = (s) => s.id === 'approval' || s.id === 'closure';

  // Where each step's result is shown in the app (for "Shown on …" links and the back link).
  const SCREEN = {
    camera_event: ['detection.html', 'Detect'], equipment_mapping: ['locate.html', 'Locate'], graph: ['graph.html', 'Knowledge graph'],
    procedure_passages: ['plan.html', 'Plan'], emission_estimate: ['quantify.html', 'Quantify'], work_order: ['plan.html', 'Plan'],
    approval: ['review.html', 'Approve'], investigation: ['plan.html', 'Plan'], answer: ['review.html', 'Approve'],
    verification: ['report.html', 'Report'], closure: ['report.html', 'Report'],
  };
  // ?step=<id>&from=<page>: opened from a "View in Glass box" link on another screen.
  const params = new URLSearchParams(location.search);
  const focusStep = params.get('step');

  let A = null, filter = 'all', open = new Set(), timer = null, active = null;

  function fmtMs(ms) { return ms === null || ms === undefined ? 'not timed' : ms < 1 ? `${ms} ms` : `${Math.round(ms * 10) / 10} ms`; }

  function spanCard(sp, maxMs) {
    const w = sp.duration_ms ? Math.max(2, Math.round((sp.duration_ms / maxMs) * 70)) : 0;
    const ev = sp.evidence.length ? sp.evidence.map((e) => `${e.screen ? `<a href="${e.screen}">${esc(e.label)}</a>` : esc(e.label)}
      <span class="path">${esc(e.path)}</span>`).join('<br>') : '—';
    return `<div class="sp${open.has(sp.id) ? ' open' : ''}" id="sp-${sp.id}">
      <button aria-expanded="${open.has(sp.id)}" data-sp="${sp.id}">
        <span class="id">${sp.id}</span>
        <span class="tl"><b>${esc(sp.tool.split(':')[0])}</b> <span>${esc(sp.result)}</span></span>
        <span class="dur">${w ? `<i style="width:${w}px"></i>` : ''}${fmtMs(sp.duration_ms)}</span>
        <span class="cv">▸</span>
      </button>
      <div class="det"><table class="kv">
        <tr><td>Component</td><td>${esc(sp.component)}</td></tr>
        <tr><td>Model/version</td><td class="m">${sp.model ? esc(sp.model) : 'No model'}${sp.model_version ? ` · ${esc(sp.model_version)}` : ''}</td></tr>
        <tr><td>Provider</td><td>${esc(sp.provider)}</td></tr>
        <tr><td>Execution location</td><td>${esc(sp.location)}</td></tr>
        <tr><td>Tool</td><td class="m">${esc(sp.tool)}</td></tr>
        <tr><td>Duration</td><td class="m">${fmtMs(sp.duration_ms)}${sp.note ? ` · ${esc(sp.note)}` : ''}</td></tr>
        <tr><td>Evidence</td><td>${ev}</td></tr>
        <tr><td>Result</td><td>${esc(sp.result)}</td></tr>
        <tr><td>Guardrail decision</td><td>${esc(sp.guardrail)}</td></tr>
        <tr><td>Started</td><td class="m">${esc(sp.started_at)}</td></tr>
      </table></div>
    </div>`;
  }

  function stepStatus(s) { return s.spans.some((x) => x.status === 'blocked') && !s.spans.some((x) => x.status === 'ok') ? 'blocked' : s.status; }

  function render() {
    if (!A) {
      $('trace').innerHTML = '<li class="why" style="padding:14px 0">Backend not reachable. No activity can be shown.</li>';
      $('hw').innerHTML = '';
      return;
    }
    const counts = { all: A.steps.length, ran: A.steps.filter((s) => s.status === 'ran').length,
      not_run: A.steps.filter((s) => s.status === 'not_run').length, human: A.steps.filter(human).length };
    $('chips').innerHTML = FILTERS.map(([k, l]) => `<button data-filter="${k}" aria-pressed="${k === filter}">${l}<span class="n">${counts[k]}</span></button>`).join('');
    $('sum').textContent = `${A.span_count} recorded span${A.span_count === 1 ? '' : 's'}`;
    $('btn-replay').disabled = !A.span_count;
    $('nvidia-line').hidden = A.hardware.nvidia_inference !== 'not_connected';
    const maxMs = Math.max(1, ...A.steps.flatMap((s) => s.spans.map((x) => x.duration_ms || 0)));

    const shown = A.steps.filter((s) => filter === 'all' || (filter === 'human' ? human(s) : s.status === filter && !human(s)));
    $('trace').innerHTML = shown.map((s) => {
      const st = stepStatus(s);
      const first = s.spans[0] || (s.human && s.human[0]) || (s.at && { at: s.at });
      const body = human(s)
        ? `${s.human.length ? `<ul class="hm">${s.human.map((e) => `<li><span class="m">${hms(e.at)} UTC</span>
              <span><b>${esc(EVENT[e.event] || e.event)}</b> by ${esc(e.actor)}${e.version ? ` · v${e.version}` : ''} · ${esc(e.detail)}</span></li>`).join('')}</ul>` : ''}
           ${s.reason ? `<p class="why">${esc(s.reason)}</p>` : ''}
           <p class="rule"><b>Guardrail decision:</b> ${esc(s.guardrail)}</p>`
        : s.spans.length ? s.spans.map((x) => spanCard(x, maxMs)).join('') : `<p class="why">${esc(s.reason)}</p>`;
      return `<li class="st ${st}${s.id === active ? ' focus' : ''}" id="st-${s.id}">
        <span class="tm">${first ? `${hms(first.started_at || first.at)} UTC` : '—'}</span><span class="nd"></span>
        <div><div class="st-h"><h3>${esc(s.label)}</h3><span class="chip ${st}">${STATUS[st] || st}</span>
          ${SCREEN[s.id] ? `<a class="shown" href="${SCREEN[s.id][0]}">Shown on ${SCREEN[s.id][1]} →</a>` : ''}</div>${body}</div></li>`;
    }).join('') || '<li class="why" style="padding:14px 0">No steps match this filter.</li>';

    const h = A.hardware;
    const li = (k, v, cls, d) => `<li><span class="k">${k}</span><span class="v ${cls}">${esc(v)}</span>${d ? `<span class="d">${esc(d)}</span>` : ''}</li>`;
    $('hw').innerHTML = [
      li('Dell Edge', DELL[h.dell_edge] || 'Unknown', h.dell_edge === 'running' ? 'ok' : 'off', h.dell_edge === 'running' ? null : 'PowerEdge XR7620 is the target; not running on it yet.'),
      li('Execution location', h.location.split(' (')[0], '', h.location),
      li('NVIDIA inference', NVIDIA[h.nvidia_inference] || 'Unknown', h.nvidia_inference === 'not_connected' ? 'off' : 'ok',
        h.nvidia_inference === 'not_connected' ? 'NVIDIA inference not connected in this run.' : null),
      li('GPU', 'Not measured', 'off', 'No GPU metrics are collected in this run.'),
      li('Agent orchestrator', h.orchestrator || 'None', h.orchestrator ? 'ok' : 'off', h.orchestrator ? 'Runs Locate → Quantify → Investigate → Plan and stops at every human decision.' : null),
      li('Knowledge graph', h.graph ? `Neo4j · ${h.graph}` : 'Unknown', h.graph === 'connected' ? 'ok' : 'off', null),
      li('Telemetry', h.historian ? `InfluxDB · ${h.historian}` : 'Unknown', h.historian === 'connected' ? 'ok' : 'off', null),
      li('Retriever', h.retriever || 'None', h.retriever ? 'ok' : 'off', 'Token-hash cosine over plant documents; NeMo Retriever not connected.'),
      li('Guardrails', h.guardrails || 'None', h.guardrails ? 'ok' : 'off', 'NeMo Guardrails not connected; boundaries are enforced in the engine.'),
    ].join('');
    $('safety').textContent = A.safety;
  }

  function toggle(id, force) {
    const on = force === undefined ? !open.has(id) : force;
    if (on) open.add(id); else open.delete(id);
    const el = $(`sp-${id}`);
    if (el) { el.classList.toggle('open', on); el.querySelector('button').setAttribute('aria-expanded', String(on)); }
  }

  // Replay: walk the recorded spans in the order they started. Display only.
  function replay() {
    if (timer) { stop(); return; }
    filter = 'all'; open.clear(); render();
    const order = A.steps.flatMap((s) => s.spans).sort((a, b) => (a.started_at + a.id).localeCompare(b.started_at + b.id));
    let i = 0;
    $('btn-replay').textContent = 'Stop';
    const next = () => {
      document.querySelectorAll('.sp.on').forEach((e) => e.classList.remove('on'));
      if (i >= order.length) { stop(); return; }
      const sp = order[i++];
      toggle(sp.id, true);
      const el = $(`sp-${sp.id}`);
      el.classList.add('on');
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      timer = setTimeout(next, 1400);
    };
    next();
  }
  function stop() {
    clearTimeout(timer); timer = null;
    $('btn-replay').textContent = 'Replay';
    document.querySelectorAll('.sp.on').forEach((e) => e.classList.remove('on'));
  }

  document.addEventListener('click', (e) => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.dataset.filter) { filter = t.dataset.filter; stop(); render(); }
    else if (t.dataset.sp) toggle(t.dataset.sp);
    else if (t.id === 'btn-replay') replay();
  });

  // Focus the step a screen linked to: banner, latest span expanded, step highlighted and in view.
  function applyFocus() {
    const s = A && focusStep && A.steps.find((x) => x.id === focusStep);
    if (!s) return;
    const last = s.spans[s.spans.length - 1];
    if (last) open.add(last.id);
    active = s.id;
    render();
    const back = window.AtelicShell.cameFrom();
    $('focus-bar').hidden = false;
    $('focus-bar').innerHTML = `<span>Step behind this result: <b>${esc(s.label)}</b>${back ? ` · opened from ${back.label}` : ''}</span>
      ${back ? `<a href="${back.href}">← Back to ${back.label}</a>` : ''}
      <button type="button" id="focus-clear">Show all steps</button>`;
    const el = $(`st-${s.id}`);
    requestAnimationFrame(() => el.scrollIntoView({ block: 'center', behavior: 'smooth' }));
  }

  document.addEventListener('click', (e) => {
    if (e.target.id !== 'focus-clear') return;
    $('focus-bar').hidden = true;
    active = null;
    document.querySelectorAll('.st.focus').forEach((x) => x.classList.remove('focus'));
    history.replaceState(null, '', 'activity.html');
  });

  async function init() {
    try { A = await get(`/incidents/${INCIDENT_ID}/agent-runs`); } catch (e) { console.error(e); A = null; }
    render();
    applyFocus();
    // Live while agents run: new spans appear as the engine records them.
    if (A) window.AtelicStory.poll(async () => { if (!timer) { A = await get(`/incidents/${INCIDENT_ID}/agent-runs`); render(); } return A; }, (a) => a.status === 'running', 3000);
  }
  init();
})();
