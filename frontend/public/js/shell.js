// Shared shell: left rail, top bar status pills, incident strip, disclosure, 6-stage stepper.
// Everything is rendered from backend state; nothing is hard-coded.
// A page opts in with <body data-page="overview"> and empty #rail / #topbar / #incident-strip / #stepper.
(function () {
  const API = `${location.protocol}//${location.hostname}:8000`;
  const INCIDENT_ID = 'INC-001';

  const STAGE_LABELS = {
    detect: 'Detect', locate: 'Locate', quantify: 'Quantify',
    plan: 'Plan', approve: 'Approve', report: 'Report',
  };
  const STATUS_LABELS = {
    not_started: 'Not started', in_progress: 'In progress',
    ready_for_review: 'Ready for review', completed: 'Completed', blocked: 'Blocked',
  };
  // Stage → rail page, so stepper boxes can link to their screen.
  const STAGE_PAGE = { detect: 'detection', locate: 'locate', quantify: 'quantify', plan: 'plan', approve: 'review', report: 'report' };
  const DELL_LABELS = { running: 'Running', target_deployment: 'Target deployment' };
  const NVIDIA_LABELS = { local_nim: 'Local NIM', hosted_api: 'Hosted API', not_connected: 'Not connected' };
  // Partner roles for the Dell / NVIDIA pills. Status lines come from /status; nothing here claims a live connection.
  const PARTNERS = {
    dell: {
      title: 'Dell PowerEdge XR7620',
      role: 'Rugged edge server at the plant site. The whole system is designed to run on it, with no cloud. Dell NativeEdge rolls it out to more sites with zero-touch setup.',
      status: (s) => s.dell_edge === 'running'
        ? ['ok', 'Running on the edge server (reported by the backend).']
        : ['off', 'Target deployment. This demo is not running on the XR7620 yet.'],
    },
    nvidia: {
      title: 'NVIDIA AI stack',
      role: 'Planned components:',
      parts: [
        ['NIM · Nemotron', 'drafts work orders and answers questions'],
        ['Speech NIM', 'Arabic and English voice'],
        ['NeMo Retriever', 'finds the right plant documents'],
        ['NeMo Guardrails', 'blocks unsafe or out-of-scope actions'],
        ['VSS', 'video understanding for the camera'],
      ],
      status: (s) => s.nvidia_inference === 'local_nim' ? ['ok', 'Local NIM: inference runs on the edge server.']
        : s.nvidia_inference === 'hosted_api' ? ['ok', 'Hosted API: inference runs on NVIDIA-hosted endpoints.']
        : ['off', 'Not connected. No NVIDIA model is used in this demo yet; the Plan draft comes from a fixed template.'],
    },
  };
  const DISCLOSURE = 'Composite demonstration. Satellite observations and METEC OGI footage are independent. Equipment, sensors and maintenance workflow are illustrative.';

  const ICONS = {
    overview: '<path d="M3 3h10v10H3z"/><path d="M5 6h6M5 9h4"/>',
    detection: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M5 7h2m2 0h2M5 10h4"/>',
    locate: '<circle cx="3" cy="8" r="1.8"/><circle cx="13" cy="3" r="1.8"/><circle cx="13" cy="13" r="1.8"/><circle cx="8" cy="8" r="1.8"/><path d="M4.5 7l2 .5M10.5 4.5 9 7M10.5 11.5 9 9"/>',
    quantify: '<path d="M3 14V7M7 14V3M11 14v-5M15 14v2" stroke-linecap="round"/>',
    plan: '<path d="M3 4h10v8H3z"/><path d="M5 7h6"/>',
    review: '<path d="M3 8l3 3 7-7"/>',
    report: '<path d="M4 2h6l3 3v9H4z"/><path d="M6 7h5M6 10h3"/>',
    activity: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M5 7h6M5 10h4"/>',
  };
  // href: null = screen not built yet (shown disabled).
  const NAV = [
    { page: 'overview', label: 'Overview', href: 'index.html' },
    { sec: 'Investigation' },
    { page: 'detection', label: 'Camera', href: 'detection.html' },
    { page: 'locate', label: 'Locate', href: 'locate.html' },
    { page: 'quantify', label: 'Quantify', href: 'quantify.html' },
    { sec: 'Response' },
    { page: 'plan', label: 'Plan', href: 'plan.html' },
    { page: 'review', label: 'Approve', href: null },
    { page: 'report', label: 'Report', href: null },
    { sec: 'Observability' },
    { page: 'activity', label: 'Glass box', href: null },
  ];

  const $ = (id) => document.getElementById(id);

  function renderRail(current) {
    const el = $('rail');
    if (!el) return;
    const links = NAV.map((n) => {
      if (n.sec) return `<span class="sec">${n.sec}</span>`;
      const icon = `<span class="ic"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3">${ICONS[n.page]}</svg></span>`;
      const attrs = n.href
        ? `href="${n.href}"${n.page === current ? ' aria-current="page"' : ''}`
        : 'aria-disabled="true" title="Not built yet"';
      return `<a ${attrs}>${icon}${n.label}<span class="nd"></span></a>`;
    }).join('');
    el.innerHTML = `
      <div class="brand"><img class="mk" src="img/atelic-logo.png" alt="Atelic"><span class="tg">edge</span></div>
      <nav class="nv">${links}</nav>
      <div class="hw"><span class="k">Built for</span><b>Dell PowerEdge XR7620</b><b>NVIDIA NIM · NeMo</b></div>
      <div class="ft"><div class="av">CR</div><div><b>Control-room engineer</b><span>Demo operator</span></div></div>`;
  }

  function renderTopbar() {
    const el = $('topbar');
    if (!el) return;
    el.innerHTML = `
      <div class="crumb"><span>ATELIC</span><span class="sep">|</span><span class="cur">Methane Incident Intelligence</span></div>
      <div class="right-controls">
        <button type="button" id="pill-dell" class="status-pill" data-partner="dell" title="About the Dell edge server" aria-expanded="false"><span class="dot"></span>Dell Edge · …</button>
        <button type="button" id="pill-nvidia" class="status-pill" data-partner="nvidia" title="About the NVIDIA AI stack" aria-expanded="false"><span class="dot"></span>NVIDIA inference · …</button>
        <span id="pill-replay" class="status-pill" title="Replay clock (UTC)"><span class="dot"></span>Replay · …</span>
      </div>
      <div class="hw-pop" id="hw-pop" role="dialog" hidden></div>`;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-partner]');
      if (b) togglePartner(b);
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('[data-partner], #hw-pop')) closePartner(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePartner(); });
  }

  let lastStatus = null;
  function closePartner() {
    const pop = $('hw-pop');
    if (!pop || pop.hidden) return;
    pop.hidden = true;
    document.querySelectorAll('[data-partner]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  }
  function togglePartner(btn) {
    const pop = $('hw-pop'), key = btn.dataset.partner;
    if (!pop.hidden && pop.dataset.for === key) { closePartner(); return; }
    const p = PARTNERS[key];
    const [kind, line] = lastStatus ? p.status(lastStatus) : ['err', 'Unknown: the backend is not reachable.'];
    pop.innerHTML = `<b class="t">${p.title}</b><p>${p.role}</p>${p.parts
      ? `<ul>${p.parts.map(([n, d]) => `<li><b>${n}</b>${d}</li>`).join('')}</ul>` : ''}
      <div class="st ${kind}"><span class="dot"></span>${line}</div>`;
    pop.dataset.for = key;
    pop.hidden = false;
    document.querySelectorAll('[data-partner]').forEach((b) => b.setAttribute('aria-expanded', String(b === btn)));
    const bar = $('topbar').getBoundingClientRect(), r = btn.getBoundingClientRect();
    pop.style.left = `${Math.max(12, Math.min(r.left - bar.left, bar.width - pop.offsetWidth - 12))}px`;
  }

  function pill(el, text, kind) {
    if (!el) return;
    el.className = `status-pill ${kind}`;
    el.innerHTML = `<span class="dot"></span>${text}`;
  }

  function renderStatus(s) {
    lastStatus = s;
    pill($('pill-dell'), `Dell Edge · ${DELL_LABELS[s.dell_edge] || 'Unknown'}`,
      s.dell_edge === 'running' ? 'ok' : 'off');
    pill($('pill-nvidia'), `NVIDIA inference · ${NVIDIA_LABELS[s.nvidia_inference] || 'Unknown'}`,
      s.nvidia_inference === 'not_connected' ? 'off' : 'ok');
    pill($('pill-replay'), `Replay · ${s.replay_time_utc || '—'}`, 'off');
  }

  // Replay-time overrides: a screen can show a stage as it was at the current replay moment
  // (e.g. Detect before the first detected frame). null falls back to the backend status.
  let lastIncident = null;
  const replayStatus = {};
  function setReplayStatus(stage, status) {
    if ((replayStatus[stage] || null) === (status || null)) return;
    if (status) replayStatus[stage] = status; else delete replayStatus[stage];
    if (lastIncident) renderIncident(lastIncident);
  }

  function renderIncident(inc) {
    lastIncident = inc;
    const strip = $('incident-strip');
    if (strip) {
      strip.textContent = [inc.local_incident_id, inc.site, inc.camera_id, inc.source_status, inc.scenario].join(' | ');
    }
    const stepper = $('stepper');
    if (stepper) {
      // data-stage marks the stage this page shows; its status still comes from the backend.
      const viewing = document.body.dataset.stage;
      // A stage whose screen exists links to it (same href as the rail); the current one does not.
      stepper.innerHTML = inc.stages.map((st, i) => ({ ...st, status: replayStatus[st.stage] || st.status })).map((st, i) => {
        const cur = st.stage === viewing;
        const href = !cur && (NAV.find((n) => n.page === STAGE_PAGE[st.stage]) || {}).href;
        const tag = href ? 'a' : 'div';
        return `
        <${tag} class="step st-${st.status}${cur ? ' current' : ''}"${cur ? ' aria-current="step"' : ''}${href ? ` href="${href}" title="Open ${STAGE_LABELS[st.stage]}"` : ''}>
          <div class="n">${String(i + 1).padStart(2, '0')}</div>
          <div class="t">${STAGE_LABELS[st.stage]}</div>
          <div class="s"><span class="dot"></span>${STATUS_LABELS[st.status]}</div>
        </${tag}>`;
      }).join('');
    }
  }

  function renderUnreachable() {
    pill($('pill-dell'), 'Dell Edge · Backend unreachable', 'err');
    pill($('pill-nvidia'), 'NVIDIA inference · Unknown', 'off');
    pill($('pill-replay'), 'Replay · —', 'off');
    const stepper = $('stepper');
    if (stepper) stepper.innerHTML = '<div class="step-empty">Stage status unavailable: backend is not reachable.</div>';
  }

  async function get(path) {
    const r = await fetch(API + path);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  }

  async function init() {
    renderRail(document.body.dataset.page);
    renderTopbar();
    const disc = $('disclosure');
    if (disc) disc.textContent = DISCLOSURE;
    try {
      const [status, incident] = await Promise.all([get('/status'), get(`/incidents/${INCIDENT_ID}`)]);
      renderStatus(status);
      renderIncident(incident);
    } catch (e) {
      console.error(e);
      renderUnreachable();
    }
  }

  // Re-read stage status after a page action changes backend state (e.g. an estimate ran).
  async function refresh() {
    try { renderIncident(await get(`/incidents/${INCIDENT_ID}`)); } catch (e) { console.error(e); }
  }

  window.AtelicShell = { API, INCIDENT_ID, get, setReplayStatus, refresh };
  // Deferred script: the DOM is parsed already, so render now (before first paint) to keep the
  // page transition from flashing an empty rail.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
