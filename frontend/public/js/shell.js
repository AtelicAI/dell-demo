// Shared shell: left rail, top bar status pills, incident strip, 6-stage stepper, plus the shared
// components every screen uses: provenance badge, confirmation dialog, back bar, dropdown.
// Everything is rendered from backend state; nothing is hard-coded.
// A page opts in with <body data-page="overview"> and empty #rail / #topbar / #incident-strip / #stepper.
(function () {
  // The UI API sits behind the same origin at /api (Caddy proxy). Local development without the proxy only: a page
  // served from localhost may point at a loopback API with ?api=http://127.0.0.1:<port> (never stored, never remote).
  const API = (() => {
    const local = /^(localhost|127\.0\.0\.1)$/;
    const q = new URLSearchParams(location.search).get('api');
    if (q && local.test(location.hostname)) {
      try {
        const u = new URL(q);
        if (u.protocol === 'http:' && local.test(u.hostname) && u.pathname === '/') return u.origin;
      } catch (e) { /* not a URL: ignore */ }
    }
    return '/api';
  })();
  // Incident this tab is working on. The link decides (?inc=); without one, the last incident used in this
  // tab (sessionStorage, so two booth screens never change each other); else INC-001.
  const INCIDENT_ID = (() => {
    const q = new URLSearchParams(location.search).get('inc'), ok = (v) => /^INC-\d{3}$/.test(v || '');
    try {
      if (ok(q)) { sessionStorage.setItem('atelic.incident', q); return q; }
      const v = sessionStorage.getItem('atelic.incident');
      return ok(v) ? v : 'INC-001';
    } catch (e) { return ok(q) ? q : 'INC-001'; }
  })();
  let liveIncidents = null;
  // Fictional facility the local investigation is set at (no real operator or site).
  // The site stays anonymous: a generic description, never a station name.
  const FACILITY = { name: 'Gas gathering station', short: 'Gas gathering station', location: 'Bahrain' };

  const STAGE_LABELS = {
    detect: 'Detect', locate: 'Locate', quantify: 'Quantify',
    plan: 'Plan', approve: 'Approve', report: 'Report', close: 'Close',
  };
  const STATUS_LABELS = {
    not_started: 'Not started', in_progress: 'In progress',
    ready_for_review: 'Ready for review', completed: 'Completed', blocked: 'Blocked', not_needed: 'Not needed',
  };
  // Stage → rail page, so stepper boxes can link to their screen.
  const STAGE_PAGE = { detect: 'detection', locate: 'locate', quantify: 'quantify', plan: 'plan', approve: 'review', report: 'report', close: 'report' };
  const DELL_LABELS = { running: 'Running', target_deployment: 'Target deployment' };
  const NVIDIA_LABELS = { local_nim: 'Local NIM', hosted_api: 'Hosted API', not_connected: 'Not connected' };
  // Partner roles for the Dell / NVIDIA pills. Status lines come from /status; nothing here claims a live connection.
  const PARTNERS = {
    dell: {
      title: 'Dell PowerEdge XR7620',
      role: 'Rugged edge server at the plant site. The whole system is designed to run on it, with no cloud. Dell NativeEdge rolls it out to more sites with zero-touch setup.',
      status: (s) => s.dell_edge === 'running'
        ? ['ok', 'Running on the edge server (reported by the backend).']
        : ['off', 'Target deployment. Not running on the XR7620 yet.'],
    },
    nvidia: {
      title: 'NVIDIA AI stack',
      role: 'Components:',
      parts: [
        ['Nemotron 3 Ultra', 'investigates, drafts and revises work orders, answers questions, verifies repairs (NVIDIA hosted endpoint in this demo)'],
        ['Speech NIM', 'Arabic and English voice'],
        ['NeMo Retriever', 'finds the right plant documents'],
        ['NeMo Guardrails', 'blocks unsafe or out-of-scope actions'],
        ['VSS', 'video understanding for the camera'],
      ],
      status: (s) => s.nvidia_inference === 'local_nim' ? ['ok', 'Local NIM: inference runs on the edge server.']
        : s.nvidia_inference === 'hosted_api' ? ['ok', 'Hosted API: inference runs on NVIDIA-hosted endpoints.']
        : ['off', 'Not connected. No NVIDIA model is in use; the engine falls back to a labelled template.'],
    },
  };

  const ICONS = {
    overview: '<path d="M3 3h10v10H3z"/><path d="M5 6h6M5 9h4"/>',
    incidents: '<path d="M2.5 3.5h11M2.5 8h11M2.5 12.5h11"/><circle cx="2.5" cy="3.5" r=".6"/>',
    detection: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M5 7h2m2 0h2M5 10h4"/>',
    locate: '<circle cx="8" cy="8" r="4.5"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/>',
    graph: '<circle cx="3" cy="8" r="1.8"/><circle cx="13" cy="3" r="1.8"/><circle cx="13" cy="13" r="1.8"/><circle cx="8" cy="8" r="1.8"/><path d="M4.5 7l2 .5M10.5 4.5 9 7M10.5 11.5 9 9"/>',
    quantify: '<path d="M3 14V7M7 14V3M11 14v-5M15 14v2" stroke-linecap="round"/>',
    plan: '<path d="M3 4h10v8H3z"/><path d="M5 7h6"/>',
    review: '<path d="M3 8l3 3 7-7"/>',
    report: '<path d="M4 2h6l3 3v9H4z"/><path d="M6 7h5M6 10h3"/>',
    activity: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M5 7h6M5 10h4"/>',
  };
  // href: null = screen not built yet (shown disabled). inc: the screen shows one incident (its links carry ?inc=);
  // the others are site pages.
  const NAV = [
    { page: 'overview', label: 'Overview', href: 'index.html' },
    { page: 'incidents', label: 'Incidents', href: 'incidents.html' },
    { page: 'graph', label: 'Knowledge graph', href: 'graph.html' },
    { sec: 'Investigation' },
    { page: 'detection', label: 'Detect', href: 'detection.html', inc: true },
    { page: 'locate', label: 'Locate', href: 'locate.html', inc: true },
    { page: 'quantify', label: 'Quantify', href: 'quantify.html', inc: true },
    { sec: 'Response' },
    { page: 'plan', label: 'Plan', href: 'plan.html', inc: true },
    { page: 'review', label: 'Approve', href: 'review.html', inc: true },
    { page: 'report', label: 'Report', href: 'report.html', inc: true },
    { sec: 'Observability' },
    { page: 'activity', label: 'Glass box', href: 'activity.html', inc: true },
  ];
  const navOf = (page) => NAV.find((n) => n.page === page) || {};
  const IS_INC_PAGE = Boolean(navOf(document.body.dataset.page).inc);

  // Link to a screen. Incident screens carry the incident; a link to a different incident also carries where
  // it came from (sw = incident, swp = page), so the arrival page can say "switched from …" and offer the way back.
  function incHref(page, inc = INCIDENT_ID) {
    const n = navOf(page);
    if (!n.inc) return n.href;
    const q = new URLSearchParams({ inc });
    if (inc !== INCIDENT_ID) {
      q.set('sw', INCIDENT_ID);
      if (document.body.dataset.page) q.set('swp', document.body.dataset.page);
    }
    return `${n.href}?${q}`;
  }

  // Where an incident stands: closed -> its closure record; no alert -> Camera; else a blocked stage, or the
  // furthest stage that has started (Detect and Locate stay "ready for review" until signed off). Report holds
  // a draft from the first run on, so it only counts once Approve is done.
  function whereNow(l) {
    if (!l.triggered) return { page: 'detection', text: 'No alert · cameras watching', kind: 'idle' };
    if (l.dismissal) return { page: 'report', text: 'Closed as a false alarm', kind: 'closed' };
    if (l.stages.every((st) => st.status === 'completed')) return { page: 'report', text: 'Report filed', kind: 'closed' };
    const st = l.stages.find((x) => x.status === 'blocked')
      || l.stages.slice().reverse().find((x) => !['not_started', 'not_needed'].includes(x.status)
        && (x.stage !== 'report' || l.stages.find((y) => y.stage === 'approve').status === 'completed')) || l.stages[0];
    return { page: STAGE_PAGE[st.stage], stage: st.stage, status: st.status,
      text: `${STAGE_LABELS[st.stage]} · ${STATUS_LABELS[st.status]}`, kind: st.status === 'blocked' ? 'bad' : 'open' };
  }

  const $ = (id) => document.getElementById(id);

  function renderRail(current) {
    const el = $('rail');
    if (!el) return;
    const links = NAV.map((n) => {
      if ('sec' in n) return `<span class="sec">${n.sec}</span>`;
      const icon = `<span class="ic"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3">${ICONS[n.page]}</svg></span>`;
      const attrs = n.href
        ? `href="${incHref(n.page)}"${n.page === current ? ' aria-current="page"' : ''}`
        : 'aria-disabled="true" title="Not built yet"';
      return `<a ${attrs} data-label="${n.label}${n.href ? '' : ' · not built yet'}">${icon}<span class="lb">${n.label}</span><span class="nd"></span></a>`;
    }).join('');
    el.innerHTML = `
      <button type="button" class="rail-tg" id="rail-tg" aria-label="Collapse menu" title="Collapse menu"><svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 2.5 4 6l3.5 3.5"/></svg></button>
      <div class="brand"><img class="mk" src="img/atelic-logo.png" alt="Atelic"><span class="tg">edge</span></div>
      <nav class="nv">${links}</nav>
      <div class="ft" data-label="Control-room engineer"><div class="av">CR</div><div class="who"><b>Control-room engineer</b><span>Operator · <button type="button" class="linkish" id="sign-out">Sign out</button></span></div></div>`;
    $('rail-tg').addEventListener('click', () => setRailMin(!document.documentElement.classList.contains('rail-min')));
    setRailMin(readRailMin(), true);
  }

  // Collapsed rail (icons only) is a per-viewer preference, remembered across pages.
  function readRailMin() {
    try { return localStorage.getItem('atelic.railMin') === '1'; } catch (e) { return false; }
  }
  function setRailMin(min, initial) {
    document.documentElement.classList.toggle('rail-min', min);
    const b = $('rail-tg');
    if (b) {
      const t = min ? 'Expand menu' : 'Collapse menu';
      b.setAttribute('aria-label', t); b.title = t; b.setAttribute('aria-expanded', String(!min));
    }
    if (initial) return;
    try { localStorage.setItem('atelic.railMin', min ? '1' : '0'); } catch (e) { /* storage blocked: still works for this page */ }
    // Animate only on a click (not on page load), then let charts re-measure their new width.
    const root = document.documentElement;
    root.classList.add('rail-anim');
    setTimeout(() => { root.classList.remove('rail-anim'); window.dispatchEvent(new Event('resize')); }, 420);
  }

  function renderTopbar() {
    const el = $('topbar');
    if (!el) return;
    el.innerHTML = `
      <div class="crumb"><span>ATELIC</span><span class="sep">|</span><span class="cur">Methane incident intelligence</span></div>
      <div class="right-controls">
        <button type="button" id="pill-sys" class="status-pill" data-partner="system" title="Dell edge server, NVIDIA inference and replay clock" aria-expanded="false"><span class="dot"></span>System · …</button>
        <button type="button" id="nt-btn" class="nt-btn" aria-label="Notifications" title="Notifications" aria-expanded="false"><svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11V7a4 4 0 0 1 8 0v4l1.5 1.5h-11z"/><path d="M6.5 14a1.6 1.6 0 0 0 3 0"/></svg><span class="nt-n" hidden></span></button>
      </div>
      <div class="hw-pop" id="hw-pop" role="dialog" hidden></div>
      <div class="nt-pop" id="nt-pop" role="dialog" aria-label="Notifications" hidden></div>`;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-partner]');
      if (b) { closeNotes(); togglePartner(b); }
      if (e.target.closest('#nt-btn')) { closePartner(); toggleNotes(); }
      if (e.target.closest('[data-nt-all]')) { markSeen(noteEvents.map(noteKey)); renderNotes(); }
      const it = e.target.closest('[data-nt-key]');
      if (it) markSeen([it.dataset.ntKey]);
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('[data-partner], #hw-pop')) closePartner();
      if (!e.target.closest('#nt-btn, #nt-pop')) closeNotes();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closePartner(); closeNotes(); } });
  }

  // Notifications: human hand-offs read from the audit log (nothing invented). Each one points at the
  // screen where someone acts next. Read state is per browser; opening that screen counts as read.
  const NOTE = {
    alert_triggered: { page: 'detection', kind: 'bad', title: (e) => e.detail.split(';')[0] },
    submitted_for_review: { page: 'review', kind: 'warn', title: (e) => `Work order v${e.version} waiting for approval` },
    review_request_changes: { page: 'plan', kind: 'warn', title: (e) => `Changes requested on v${e.version}` },
    review_reject: { page: 'plan', kind: 'bad', title: (e) => `Work order v${e.version} rejected` },
    review_approve: { page: 'report', kind: 'good', title: (e) => `Work order v${e.version} approved` },
    incident_dismissed: { page: 'incidents', kind: 'good', title: () => 'Closed as a false alarm' },
    incident_reopened: { page: 'detection', kind: 'warn', title: () => 'Reopened after a false-alarm close' },
    agent_investigation: { page: 'plan', kind: 'good', title: () => 'Investigation agent reported findings' },
    draft_version: { page: 'plan', kind: 'warn', title: (e) => `Work order v${e.version} drafted`, only: (e) => !String(e.actor).startsWith('Operator') },
    agent_answer: { page: 'review', kind: 'good', title: () => 'Agent answered your question' },
    report_issued: { page: 'report', kind: 'good', title: () => 'Final report issued' },
    agent_verification: { page: 'report', kind: 'warn', title: (e) => e.detail },
    incident_closed: { page: 'report', kind: 'good', title: () => 'Incident closed and written to the knowledge graph' },
  };
  const SEVERITY = { good: 1, warn: 2, bad: 3 };
  const noteKey = (e) => `${e.incident_id}:${e.seq}@${e.at}`;
  // Notifications link to the incident they belong to.
  const noteHref = (e) => incHref(NOTE[e.event].page, e.incident_id);
  let noteEvents = [], noteKnown = null, notePoll = null;
  function readSeen() {
    try { return new Set(JSON.parse(localStorage.getItem('atelic.notesSeen') || '[]')); } catch (e) { return new Set(); }
  }
  function markSeen(keys) {
    const seen = readSeen();
    keys.forEach((k) => seen.add(k));
    // Keep only keys still in the log (a reset starts a new log).
    const live = new Set(noteEvents.map(noteKey));
    try { localStorage.setItem('atelic.notesSeen', JSON.stringify([...seen].filter((k) => live.has(k)))); } catch (e) { /* storage blocked */ }
  }
  function ago(at) {
    const s = (Date.now() - Date.parse(at)) / 1000;
    return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${at.slice(11, 16)} UTC`;
  }

  async function loadNotes() {
    let events;
    try { events = (await get('/audit')).events; } catch (e) { return; }
    noteEvents = events.filter((e) => NOTE[e.event] && (!NOTE[e.event].only || NOTE[e.event].only(e)));
    const here = document.body.dataset.page;
    if (noteKnown === null) {
      // First load on this page: no toasts; what targets this screen is being looked at now.
      markSeen(noteEvents.filter((e) => NOTE[e.event].page === here && e.incident_id === INCIDENT_ID).map(noteKey));
    } else {
      const fresh = noteEvents.filter((e) => !noteKnown.has(noteKey(e)));
      fresh.forEach(toast);
      // Already on the screen it points at (e.g. the alert you just raised): pop it up, but no unread dot.
      markSeen(fresh.filter((e) => NOTE[e.event].page === here && e.incident_id === INCIDENT_ID).map(noteKey));
    }
    noteKnown = new Set(noteEvents.map(noteKey));
    renderNotes();
    if (!notePoll) notePoll = setInterval(() => { if (!document.hidden) loadNotes(); }, 8000);
  }

  function renderNotes() {
    const seen = readSeen();
    const unread = noteEvents.filter((e) => !seen.has(noteKey(e)));
    const worst = (list) => list.reduce((k, e) => (SEVERITY[NOTE[e.event].kind] > SEVERITY[k] ? NOTE[e.event].kind : k), 'good');
    const btn = $('nt-btn');
    if (btn) {
      const n = btn.querySelector('.nt-n');
      n.hidden = !unread.length;
      n.textContent = unread.length;
      n.className = `nt-n ${worst(unread)}`;
      btn.setAttribute('aria-label', `Notifications${unread.length ? `, ${unread.length} unread` : ''}`);
    }
    // Rail: a coloured dot on each screen with unread items for it.
    document.querySelectorAll('#rail .nv a').forEach((a) => {
      a.classList.remove('note-good', 'note-warn', 'note-bad');
      const page = (NAV.find((n) => n.href && a.getAttribute('href').split('?')[0] === n.href) || {}).page;
      // The rail shows the current incident's screens, so only its items get a dot.
      const mine = unread.filter((e) => NOTE[e.event].page === page && e.incident_id === INCIDENT_ID);
      if (mine.length) a.classList.add(`note-${worst(mine)}`);
    });
    const pop = $('nt-pop');
    if (!pop) return;
    pop.innerHTML = `<div class="nt-hd"><b>Notifications</b>${unread.length ? '<button type="button" class="linkish" data-nt-all>Mark all read</button>' : ''}</div>${noteEvents.length
      ? `<div class="nt-list">${noteEvents.slice().reverse().map((e) => {
        const n = NOTE[e.event], nav = navOf(n.page);
        return `<a class="nt-it ${n.kind}${seen.has(noteKey(e)) ? '' : ' new'}" href="${noteHref(e)}" data-nt-key="${escAttr(noteKey(e))}">
          <span class="nt-d"></span><span><b>${escAttr(n.title(e))}</b><span class="nt-x">${escAttr(e.detail)}</span>
          <small>${escAttr(e.incident_id)} · ${escAttr(e.actor)} · ${ago(e.at)} · ${nav.label}</small></span></a>`;
      }).join('')}</div>`
      : '<p class="nt-empty">Nothing yet. Camera alerts, submissions for approval and reviewer decisions show here.</p>'}`;
  }
  function toggleNotes() {
    const pop = $('nt-pop');
    if (!pop.hidden) { closeNotes(); return; }
    renderNotes();
    pop.hidden = false;
    $('nt-btn').setAttribute('aria-expanded', 'true');
  }
  function closeNotes() {
    const pop = $('nt-pop');
    if (!pop || pop.hidden) return;
    pop.hidden = true;
    $('nt-btn').setAttribute('aria-expanded', 'false');
  }
  // A new item arriving while a page is open pops up for a few seconds.
  function toast(e) {
    let box = $('nt-toasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'nt-toasts';
      box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }
    const n = NOTE[e.event], nav = navOf(n.page), t = document.createElement('div');
    t.className = `nt-toast ${n.kind}`;
    t.innerHTML = `<span class="nt-d"></span><div><b>${escAttr(n.title(e))}</b><span class="nt-x">${escAttr(e.incident_id)} · ${escAttr(e.actor)}</span>
      <a href="${noteHref(e)}" data-nt-key="${escAttr(noteKey(e))}">Open ${nav.label} →</a></div><button type="button" aria-label="Dismiss">×</button>`;
    t.querySelector('a').addEventListener('click', () => markSeen([noteKey(e)]));
    t.querySelector('button').addEventListener('click', () => t.remove());
    box.appendChild(t);
    setTimeout(() => t.remove(), 9000);
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
    const sec = (p) => {
      const [kind, line] = lastStatus ? p.status(lastStatus) : ['err', 'Unknown: the backend is not reachable.'];
      return `<div class="hw-sec"><b class="t">${p.title}</b><p>${p.role}</p>${p.parts
        ? `<ul>${p.parts.map(([n, d]) => `<li><b>${n}</b>${d}</li>`).join('')}</ul>` : ''}
        <div class="hw-st ${kind}"><span class="dot"></span>${line}</div></div>`;
    };
    const now = replayNow();
    pop.innerHTML = `${sec(PARTNERS.dell)}${sec(PARTNERS.nvidia)}
      <div class="hw-sec"><b class="t">Replay clock</b><div class="hw-st ${now ? 'ok' : 'off'}"><span class="dot"></span>${now
        ? `Recorded data is replayed from ${fmtUtc(now, true)}.` : 'No replay running.'}</div></div>`;
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

  // One chip for the system: green only when the edge server runs and NVIDIA inference is connected;
  // the details (and the honest "target deployment / not connected") are one click away.
  function sysPill() {
    const s = lastStatus, now = replayNow(), el = $('pill-sys');
    if (!el) return;
    const live = s && s.dell_edge === 'running' && s.nvidia_inference !== 'not_connected';
    pill(el, `System${now ? ` · <span class="mono">${fmtUtc(now)}</span>` : ''}`, live ? 'ok' : 'off');
    if (s) el.title = `Dell Edge: ${DELL_LABELS[s.dell_edge] || 'Unknown'} · NVIDIA inference: ${NVIDIA_LABELS[s.nvidia_inference] || 'Unknown'}${now ? ` · Replay ${fmtUtc(now, true)}` : ''}`;
  }

  function renderStatus(s) {
    lastStatus = s;
    replayStart = s.replay_start_utc ? Date.parse(s.replay_start_utc) : null;
    tickReplay();
  }

  // One replay clock for the whole app: incident start + real time elapsed since the first page of this
  // browser session, so it keeps running across clip loops and page changes.
  let replayStart = null, replayTimer = null;
  function replayT0() {
    try {
      const v = Number(sessionStorage.getItem('atelic.replayT0'));
      if (v) return v;
      sessionStorage.setItem('atelic.replayT0', String(Date.now()));
    } catch (e) { /* storage blocked: clock restarts per page */ }
    return T0;
  }
  const T0 = Date.now();
  function replayNow() {
    return replayStart == null ? null : new Date(replayStart + (Date.now() - replayT0()));
  }
  const fmtUtc = (d, withDate) => `${withDate ? `${d.toISOString().slice(0, 10)} ` : ''}${d.toISOString().slice(11, 19)} UTC`;
  function tickReplay() {
    const now = replayNow();
    sysPill();
    if (now && !replayTimer) replayTimer = setInterval(tickReplay, 1000);
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
    renderNoIncident(inc);
    renderClosedBar(inc);
    renderContext(inc);
    const stepper = $('stepper');
    if (stepper) {
      // data-stage marks the stage this page shows; its status still comes from the backend.
      const viewing = document.body.dataset.stage;
      // A stage whose screen exists links to it (same href as the rail); the current one does not.
      stepper.innerHTML = inc.stages.map((st, i) => ({ ...st, status: replayStatus[st.stage] || st.status })).map((st, i) => {
        const cur = st.stage === viewing;
        const href = !cur && navOf(STAGE_PAGE[st.stage]).href && incHref(STAGE_PAGE[st.stage]);
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

  // Live incidents (GET /incidents) feed the incident switcher and the context strip.
  async function loadLive() {
    try { liveIncidents = (await get('/incidents')).live; } catch (e) { return; }
    if (lastIncident) renderContext(lastIncident);
  }
  // Which incident this screen shows: the strip next to the page title on incident screens.
  // Colour = state (watching, open, blocked, closed).
  function renderContext(inc) {
    const live = (liveIncidents || []).find((l) => l.incident_id === inc.local_incident_id) || {};
    const w = whereNow({ ...live, ...inc });
    const asset = inc.triggered && !inc.dismissal ? live.asset_id : null;
    const e = escAttr;
    if (!IS_INC_PAGE) return;
    let strip = $('incident-strip');
    if (!strip) {
      const ph = document.querySelector('main .page-head');
      if (!ph) return;
      strip = document.createElement('div');
      strip.className = 'incident-strip';
      strip.id = 'incident-strip';
      ph.appendChild(strip);
    }
    const ic = (d) => `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
    strip.setAttribute('aria-label', `Showing ${inc.local_incident_id}: ${[inc.camera_id, asset && `near ${asset}`, w.text].filter(Boolean).join(', ')}`);
    strip.innerHTML = `
      <span class="is-id ${w.kind}"><i></i>${e(inc.local_incident_id)}</span>
      ${(liveIncidents || []).length > 1
        ? `<button type="button" class="is-seg is-cam" id="is-cam" title="Switch camera" aria-haspopup="dialog">${ic('<rect x="1.5" y="4.5" width="9" height="7" rx="1"/><path d="M10.5 7.5l4-2v5l-4-2"/>')}${e(inc.camera_id)}${CHEVRON}</button>`
        : `<span class="is-seg">${ic('<rect x="1.5" y="4.5" width="9" height="7" rx="1"/><path d="M10.5 7.5l4-2v5l-4-2"/>')}${e(inc.camera_id)}</span>`}
      ${asset ? `<span class="is-seg" title="Likely source (hypothesis)">${ic('<circle cx="8" cy="8" r="4.5"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/>')}near ${e(asset)}</span>` : ''}
      <a class="is-seg is-src ${w.kind}" href="incidents.html?id=${encodeURIComponent(inc.local_incident_id)}" title="Where this incident stands (Incidents page)">${e(w.text)}</a>`;
    const cam = $('is-cam');
    if (cam) cam.addEventListener('click', switchCamera);
  }

  // Camera picker from the strip: one row per camera with its incident. Same screen for an incident that has an
  // alert; Camera for one that has none yet (that is where it can be triggered).
  function switchCamera() {
    const page = document.body.dataset.page;
    const rows = (liveIncidents || []).map((l) => {
      const w = whereNow(l), me = l.incident_id === INCIDENT_ID;
      const body = `<b class="mono">${escAttr(l.camera_id)}</b><span class="mono">${escAttr(l.incident_id)}</span><span class="sw-st ${w.kind}">${escAttr(w.text)}</span>`;
      return me ? `<div class="sw-opt" aria-current="true">${body}<em>Showing</em></div>`
        : `<a class="sw-opt" href="${escAttr(incHref(l.triggered ? page : 'detection', l.incident_id))}">${body}<em>Open →</em></a>`;
    }).join('');
    modal({ title: 'Switch camera', html: `<div class="sw-list">${rows}</div>`, okLabel: null, cancelLabel: 'Close' });
  }

  // A link to another incident (incHref marks it with sw=) asks first, so the user knows the incident changes.
  function confirmSwitch(href) {
    const to = new URL(href, location.href).searchParams.get('inc');
    const l = (liveIncidents || []).find((x) => x.incident_id === to);
    const w = l ? whereNow(l) : null;
    const target = NAV.find((n) => n.href && href.split('?')[0].endsWith(n.href)) || {};
    modal({
      title: `Switch to ${to}?`,
      html: `<p>You are on <b>${escAttr(INCIDENT_ID)}</b>. This opens <b>${escAttr(to)}</b>${target.label ? ` · ${escAttr(target.label)}` : ''}.</p>
        ${l ? `<p class="sw-to"><span class="mono">${escAttr(l.camera_id)}</span>${l.asset_id && !l.dismissal ? ` · near <span class="mono">${escAttr(l.asset_id)}</span>` : ''} · ${escAttr(w.text)}</p>` : ''}
        <p style="color:var(--ink-mute)">Every incident screen will then show ${escAttr(to)} until you switch back.</p>`,
      okLabel: `Open ${to}`,
      okHref: href,
      cancelLabel: `Stay on ${INCIDENT_ID}`,
    });
  }
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.closest('#modal') || !/[?&]sw=/.test(a.getAttribute('href'))) return;
    e.preventDefault();
    confirmSwitch(a.href);
  });

  // Arrived through a link that switched incident: say so, and offer the way back. Shown once; the URL is cleaned.
  function renderSwitchBar() {
    const q = new URLSearchParams(location.search), from = q.get('sw'), fromPage = q.get('swp');
    if (!from || from === INCIDENT_ID) return;
    q.delete('sw'); q.delete('swp');
    try { history.replaceState(null, '', `${location.pathname}?${q}`); } catch (e) { /* ignore */ }
    const back = navOf(fromPage);
    const href = back.inc ? `${back.href}?inc=${encodeURIComponent(from)}` : back.href;
    const bar = document.createElement('div');
    bar.className = 'switch-bar';
    bar.setAttribute('role', 'status');
    bar.innerHTML = `<span>Now showing <b>${escAttr(INCIDENT_ID)}</b> · switched from ${escAttr(from)}</span>
      ${href ? `<a href="${href}">← Back to ${back.inc ? `${escAttr(from)} ${back.label}` : back.label}</a>` : ''}
      <button type="button" aria-label="Hide">×</button>`;
    bar.querySelector('button').addEventListener('click', () => bar.remove());
    const after = $('stepper') || document.querySelector('main .lede') || document.querySelector('main .page-head');
    if (after) after.after(bar);
  }

  function renderUnreachable() {
    pill($('pill-sys'), 'System · Backend unreachable', 'err');
    const stepper = $('stepper');
    if (stepper) stepper.innerHTML = '<div class="step-empty">Stage status unavailable: backend is not reachable.</div>';
  }

  // Session expired (or signed out in another tab): back to the sign-in page, then here again.
  const toLogin = () => location.replace(`/login.html?next=${encodeURIComponent(location.pathname + location.search)}`);
  document.addEventListener('click', async (e) => {
    if (e.target.id !== 'sign-out') return;
    await fetch(`${API}/logout`, { method: 'POST' }).catch(() => null);
    location.replace('/login.html');
  });
  async function get(path) {
    const r = await fetch(API + path);
    if (r.status === 401 && API === '/api') { toLogin(); throw new Error('Sign in required'); }
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  }

  // "View in Glass box": opens the investigation trace focused on the step that produced a value.
  // Pages call traceLink(step) in rendered HTML, or mark a static spot with data-trace="step".
  const TRACE_ICON = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="3.5" cy="3.5" r="1.8"/><circle cx="3.5" cy="12.5" r="1.8"/><path d="M3.5 5.3v5.4M7.5 3.5h6M7.5 8h6M7.5 12.5h6"/></svg>';
  function traceLink(step, text) {
    const from = encodeURIComponent(document.body.dataset.page || '');
    return `<a class="gb-link" href="activity.html?step=${encodeURIComponent(step)}&from=${from}" title="See how this was produced in the Glass box">${TRACE_ICON}<span>${text || 'View in Glass box'}</span></a>`;
  }
  function fillTraceSlots(root) {
    (root || document).querySelectorAll('[data-trace]').forEach((el) => { el.innerHTML = traceLink(el.dataset.trace, el.dataset.traceText); });
  }

  // Provenance badge (truth rule). Simulated / synthetic provenance stays in the data but is not shown;
  // an unknown value shows Pending, never a guessed label.
  const BADGE = {
    observed: 'Observed', provider_reported: 'Provider reported', experimental_setpoint: 'Experimental setpoint',
    model_estimated: 'Model estimated', simulated: 'Simulated', synthetic: 'Simulated', manual_annotation: 'Manual annotation',
    hypothesis: 'Hypothesis', approved: 'Approved', pending: 'Pending', gap: 'Gap',
    human_decision: 'Human decision', system: 'System status', agent_drafted: 'Agent drafted',
  };
  const escAttr = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function badge(p, title) {
    if (p === 'simulated' || p === 'synthetic') return '';
    return `<span class="badge ${escAttr(p)}"${title ? ` title="${escAttr(title)}"` : ''}>${BADGE[p] || 'Pending'}</span>`;
  }

  // Confirmation dialog, built on first use. OK runs onOk after the dialog hides (its fields can still
  // be read), or follows okHref. With requireText, OK waits until the textarea in the body has text.
  let modalOk = null, modalReturn = null;
  function modalEl() {
    let m = $('modal');
    if (m) return m;
    m = document.createElement('div');
    m.className = 'modal-backdrop';
    m.id = 'modal';
    m.setAttribute('role', 'dialog');
    m.setAttribute('aria-modal', 'true');
    m.setAttribute('aria-labelledby', 'modal-title');
    m.innerHTML = '<div class="modal"><h3 id="modal-title"></h3><div id="modal-body"></div><div class="actions"></div></div>';
    document.body.appendChild(m);
    m.addEventListener('click', async (e) => {
      if (e.target === m || e.target.closest('[data-modal-cancel]')) { closeModal(); return; }
      if (e.target.closest('button[data-modal-ok]')) { const fn = modalOk; closeModal(); if (fn) await fn(); }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && m.classList.contains('open')) closeModal(); });
    return m;
  }
  function modal({ title, html = '', okLabel = 'OK', okClass = '', okHref, cancelLabel = 'Cancel', requireText, isValid, onOk }) {
    const m = modalEl();
    modalReturn = document.activeElement;
    modalOk = onOk || null;
    $('modal-title').textContent = title;
    $('modal-body').innerHTML = html;
    m.querySelector('.actions').innerHTML = `<button type="button" class="btn" data-modal-cancel>${cancelLabel}</button>${okLabel === null ? '' : okHref
      ? `<a class="btn primary ${okClass}" data-modal-ok href="${escAttr(okHref)}">${okLabel}</a>`
      : `<button type="button" class="btn primary ${okClass}" data-modal-ok>${okLabel}</button>`}`;
    const ok = m.querySelector('[data-modal-ok]'), ta = $('modal-body').querySelector('textarea');
    if (requireText && ta) {
      ok.disabled = true;
      ta.addEventListener('input', () => { ok.disabled = !ta.value.trim(); });
    }
    // isValid(body): OK stays disabled until it returns true (re-checked on every input in the body).
    if (isValid) {
      const check = () => { ok.disabled = !isValid($('modal-body')); };
      $('modal-body').oninput = check;
      $('modal-body').onchange = check;
      check();
    } else {
      $('modal-body').oninput = null;
      $('modal-body').onchange = null;
    }
    m.classList.add('open');
    const first = [...$('modal-body').querySelectorAll('input[type=text], textarea')].find((f) => !f.value.trim());
    (first || ta || ok).focus();
  }
  function closeModal() {
    const m = $('modal');
    if (!m) return;
    m.classList.remove('open');
    modalOk = null;
    if (modalReturn && modalReturn.isConnected) modalReturn.focus();
    modalReturn = null;
  }

  // Closing an alert as a false alarm, and reopening it: a named person and a reason, never automatic.
  // Same dialog on the Camera screen and the Incidents page. done(incident) runs after the backend accepts it.
  const DISMISS_REASONS = [['steam', 'Steam or water vapour'], ['exhaust', 'Hot exhaust or engine heat'],
    ['reflection', 'Reflection or sun glint'], ['planned', 'Planned venting or maintenance'], ['other', 'Other']];
  const readName = () => { try { return localStorage.getItem('atelic.reviewer') || ''; } catch (e) { return ''; } };
  const saveName = (v) => { try { localStorage.setItem('atelic.reviewer', v); } catch (e) { /* storage blocked */ } };
  async function postJson(path, body) {
    const r = await fetch(API + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : `Request failed (${r.status})`);
    return j;
  }
  const failed = (title, e) => modal({ title, html: `<p>${escAttr(e.message)}</p>`, okLabel: 'OK', cancelLabel: 'Close' });
  function dismissDialog(id, done) {
    modal({
      title: `Close ${id} as a false alarm?`,
      html: `<p class="dm-lead">Nothing more will be needed on this incident: no estimate review, work order or approval. The decision is recorded with your name, and the incident can be reopened.</p>
        <label for="dm-by">Your name</label><input type="text" id="dm-by" autocomplete="name" value="${escAttr(readName())}">
        <label>What caused it?</label>
        <div class="dm-reasons" role="radiogroup">${DISMISS_REASONS.map(([v, l]) => `<label class="dm-r"><input type="radio" name="dm-reason" value="${v}"><span>${l}</span></label>`).join('')}</div>
        <label for="dm-comment">What did you see?</label>
        <textarea id="dm-comment" rows="3" placeholder="For example: steam from the separator drain; handheld detector read background."></textarea>`,
      okLabel: 'Close as false alarm', okClass: 'go',
      isValid: (b) => b.querySelector('#dm-by').value.trim() && b.querySelector('input[name=dm-reason]:checked') && b.querySelector('#dm-comment').value.trim(),
      onOk: async () => {
        const by = $('dm-by').value.trim();
        saveName(by);
        try {
          done(await postJson(`/incidents/${id}/dismiss`, { by, reason: document.querySelector('input[name=dm-reason]:checked').value, comment: $('dm-comment').value.trim() }));
        } catch (e) { failed('Could not close the incident', e); }
      },
    });
  }
  function reopenDialog(id, done) {
    modal({
      title: `Reopen ${id}?`,
      html: `<p class="dm-lead">The false-alarm close is undone and the investigation continues where it left off. Your name goes in the audit log.</p>
        <label for="dm-by">Your name</label><input type="text" id="dm-by" autocomplete="name" value="${escAttr(readName())}">`,
      okLabel: 'Reopen incident',
      isValid: (b) => b.querySelector('#dm-by').value.trim(),
      onOk: async () => {
        const by = $('dm-by').value.trim();
        saveName(by);
        try { done(await postJson(`/incidents/${id}/reopen`, { by })); } catch (e) { failed('Could not reopen the incident', e); }
      },
    });
  }
  // Incident screens of a closed incident say so under the stepper (content stays visible, read-only).
  function renderClosedBar(inc) {
    let bar = $('closed-bar');
    const d = inc.dismissal, stage = document.body.dataset.stage;
    // Workflow buttons on a closed incident would only be refused by the backend, so they show as unavailable.
    document.documentElement.classList.toggle('incident-closed', Boolean(d));
    if (!d || !stage || stage === 'detect') { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'closed-bar';
      bar.className = 'closed-bar';
      const after = $('stepper') || document.querySelector('main .lede');
      if (after) after.after(bar); else return;
    }
    bar.innerHTML = `<span class="ic" aria-hidden="true">✓</span><span><b>Closed as a false alarm</b> by ${escAttr(d.by)} · ${escAttr(d.reason_label)} · “${escAttr(d.comment)}”</span>
      <a href="detection.html?inc=${encodeURIComponent(inc.local_incident_id)}">Reopen on Detect →</a>`;
  }

  // Back bar: a page opened with ?from=<page> (e.g. from a "View in Glass box" link) links back to it.
  function cameFrom() {
    const from = new URLSearchParams(location.search).get('from');
    const n = NAV.find((x) => x.page === from && x.href);
    return n ? { page: n.page, label: n.label, href: incHref(n.page) } : null;
  }
  function backBar(detail) {
    const el = $('back-bar'), back = cameFrom();
    if (!el || !back) return;
    el.hidden = false;
    el.innerHTML = `<span>Opened from ${back.label}${detail || ''}</span><a href="${back.href}">← Back to ${back.label}</a>`;
  }

  // Dropdown in the app style, in place of a native <select>. host = an empty element.
  // options: [{ value, label, sub, group, color }] (sorted by group; color = a CSS token for a dot).
  // search = placeholder for a filter box. onChange(value) on a pick; returns { set(value) }.
  const CHEVRON = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3.5 5 6.5 8 3.5"/></svg>';
  function dropdown(host, { label, options, value, onChange, search }) {
    host.classList.add('dd');
    host.innerHTML = `<button type="button" class="dd-btn" aria-haspopup="listbox" aria-expanded="false" aria-label="${escAttr(label)}"><span class="dd-v"></span>${CHEVRON}</button>
      <div class="dd-pop" hidden>${search ? `<input class="dd-q" type="text" autocomplete="off" placeholder="${escAttr(search)}" aria-label="${escAttr(search)}">` : ''}
      <div class="dd-list" role="listbox" tabindex="-1" aria-label="${escAttr(label)}"></div></div>`;
    const btn = host.querySelector('.dd-btn'), pop = host.querySelector('.dd-pop'), list = host.querySelector('.dd-list'), q = host.querySelector('.dd-q');
    let cur = value, vis = [], active = -1;
    const dot = (o) => (o.color ? `<i style="background:var(${o.color})"></i>` : '');
    const body = (o) => `${dot(o)}<b>${escAttr(o.label)}</b>${o.sub ? `<span>${escAttr(o.sub)}</span>` : ''}`;
    const show = () => { host.querySelector('.dd-v').innerHTML = body(options.find((o) => o.value === cur) || options[0]); };
    function draw() {
      const f = q ? q.value.trim().toLowerCase() : '';
      vis = options.filter((o) => !f || `${o.label} ${o.sub || ''} ${o.group || ''}`.toLowerCase().includes(f));
      let g = null;
      list.innerHTML = vis.map((o, i) => {
        const head = o.group && o.group !== g ? `<div class="dd-g">${escAttr(o.group)}</div>` : '';
        g = o.group;
        return `${head}<div class="dd-o${i === active ? ' on' : ''}" role="option" data-i="${i}" aria-selected="${o.value === cur}">${body(o)}</div>`;
      }).join('') || '<div class="dd-none">No match</div>';
      const a = list.querySelector('.dd-o.on');
      if (a) a.scrollIntoView({ block: 'nearest' });
    }
    function open() {
      if (q) q.value = '';
      vis = options;
      active = Math.max(0, options.findIndex((o) => o.value === cur));
      pop.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      draw();
      (q || list).focus();
    }
    function close(refocus) {
      if (pop.hidden) return;
      pop.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      if (refocus) btn.focus();
    }
    function pick(o) {
      close(true);
      if (!o || o.value === cur) return;
      cur = o.value;
      show();
      onChange(cur);
    }
    btn.addEventListener('click', () => (pop.hidden ? open() : close()));
    list.addEventListener('click', (e) => { const o = e.target.closest('.dd-o'); if (o) pick(vis[o.dataset.i]); });
    list.addEventListener('mousemove', (e) => {
      const o = e.target.closest('.dd-o');
      if (o && Number(o.dataset.i) !== active) { active = Number(o.dataset.i); list.querySelectorAll('.dd-o').forEach((x) => x.classList.toggle('on', x === o)); }
    });
    if (q) q.addEventListener('input', () => { active = 0; draw(); });
    host.addEventListener('keydown', (e) => {
      if (pop.hidden) {
        if (['ArrowDown', 'ArrowUp'].includes(e.key)) { e.preventDefault(); open(); }
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        active = Math.min(vis.length - 1, Math.max(0, active + (e.key === 'ArrowDown' ? 1 : -1)));
        draw();
      } else if (e.key === 'Enter') { e.preventDefault(); pick(vis[active]); }
      else if (e.key === 'Tab') close();
    });
    document.addEventListener('click', (e) => { if (!host.contains(e.target)) close(); });
    show();
    return { set(v) { cur = v; show(); } };
  }

  async function init() {
    // Incident screens always say which incident they show in the URL (copyable, and explicit after a menu click).
    if (IS_INC_PAGE && new URLSearchParams(location.search).get('inc') !== INCIDENT_ID) {
      const q = new URLSearchParams(location.search);
      q.set('inc', INCIDENT_ID);
      try { history.replaceState(null, '', `${location.pathname}?${q}`); } catch (e) { /* ignore */ }
    }
    renderRail(document.body.dataset.page);
    renderSwitchBar();
    fillTraceSlots();
    // Static labels that name the incident (<span data-inc>) show the current one.
    document.querySelectorAll('[data-inc]').forEach((el) => { el.textContent = INCIDENT_ID; });
    renderTopbar();
    try {
      const [status, incident] = await Promise.all([get('/status'), get(`/incidents/${INCIDENT_ID}`)]);
      renderStatus(status);
      renderIncident(incident);
      loadLive();
      loadNotes();
    } catch (e) {
      console.error(e);
      renderUnreachable();
    }
  }

  // Before the camera's alert there is no incident to work on: Locate, Quantify and Plan show an empty state
  // in place of their content (Camera handles its own normal-operations view).
  function renderNoIncident(inc) {
    const stage = document.body.dataset.stage;
    // Boolean: classList.toggle(name, undefined) would flip the class on pages without a stage (Overview).
    const off = Boolean(inc.triggered === false && stage && stage !== 'detect');
    document.documentElement.classList.toggle('no-incident', off);
    let card = $('no-incident');
    if (!off) { if (card) card.remove(); return; }
    if (card) return;
    card = document.createElement('section');
    card.id = 'no-incident';
    card.className = 'panel no-incident-card';
    card.innerHTML = `<div class="t">No active incident</div>
      <p>All cameras at the ${FACILITY.name.toLowerCase()} are watching. This screen opens when a camera raises an alert.</p>
      <a class="btn" href="detection.html">Go to cameras →</a>`;
    const after = $('stepper') || document.querySelector('main .lede');
    if (after) after.after(card); else document.querySelector('main').prepend(card);
  }

  // Re-read stage status after a page action changes backend state (e.g. an estimate ran).
  async function refresh() {
    try { renderIncident(await get(`/incidents/${INCIDENT_ID}`)); } catch (e) { console.error(e); }
    loadNotes();
    loadLive();
  }

  window.AtelicShell = {
    API, INCIDENT_ID, FACILITY, get, setReplayStatus, refresh, replayNow, fmtUtc, traceLink,
    BADGE, badge, modal, closeModal, cameFrom, backBar, dropdown, dismissDialog, reopenDialog, incHref, whereNow, confirmSwitch,
  };
  // Deferred script: the DOM is parsed already, so render now (before first paint) to keep the
  // page transition from flashing an empty rail.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
