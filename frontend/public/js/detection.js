// Screen 2 — Detect / Camera. Reads GET /incidents/:id/detections. A 2×2 wall plays all skid cameras;
// the incident's camera replays GET /incidents/:id/clip, a camera with another live incident shows that
// incident's view, the others loop GET /ogi/cams/:id.
// Overlays are drawn only for frames that have an annotation; nothing is interpolated or inferred.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const { badge } = window.AtelicShell;
  // Provenances a verified clip or annotation may carry (anything else shows Pending).
  const CLIP_PROV = new Set(['experimental_setpoint', 'manual_annotation', 'model_estimated', 'provider_reported', 'simulated']);
  const MODES = {
    external_detector_alert: 'External detector alert',
    local_model_inference: 'Local model inference',
    recorded_model_output: 'Recorded model output',
    manually_annotated_replay: 'Manually annotated replay',
  };

  // Only a verified record carries its stated provenance; a sample gets no badge; anything else is Pending.
  function clipProv(m) {
    if (m.status === 'verified' && CLIP_PROV.has(m.provenance)) return m.provenance;
    return m.status === 'sample' ? 'simulated' : 'pending';
  }
  // Simulated / synthetic provenance is tracked in the data but not shown.

  let CAM = 'CAM-01', live = [], raised = false, dismissal = null, meta, ann, prov, annP, fps = null, times = null, total = 0, firstFrame = null, observedNow = null, byFrame = new Map(), video = null, frame = -1;

  // ----- data rules -----
  const hasRegion = (a) => a && ((a.mask_polygon_normalized || []).length > 2 || a.bbox_normalized || a.origin_normalized);

  function usableAnnotations(d) {
    // A range applies one region to every frame from start_frame to end_frame.
    const expanded = (d.annotations.ranges || []).flatMap((r) =>
      Array.from({ length: Math.max(0, r.end_frame - r.start_frame + 1) }, (_, i) => ({ ...r, frame: r.start_frame + i })));
    const frames = [...(d.annotations.frames || []), ...expanded].filter(hasRegion);
    // Dataset identity: annotations must belong to this exact clip.
    if (!frames.length || !d.annotations.ogi_clip_id || d.annotations.ogi_clip_id !== d.metadata.ogi_clip_id) return [];
    return frames;
  }

  // ----- metadata panel -----
  function row(k, v, p, id) {
    const val = v == null
      ? '<span>Not available</span>'
      : `<span class="pdot ${p}"></span><span>${esc(v)}</span>`;
    return `<div class="k">${k}</div><div class="v${v == null ? ' na' : ''}"${id ? ` id="${id}"` : ''}>${val}</div>`;
  }

  function renderMeta(d, frames) {
    const observed = frames.length > 0;

    if (!observed) {
      noDetectionNote = (d.annotations.frames || []).length || (d.annotations.ranges || []).length
        ? 'Annotations do not match this clip ID, so they are not shown.'
        : 'No plume annotation or detector output is available for this clip.';
    }

    const origin = meta.source_url
      ? `<a href="${esc(meta.source_url)}" target="_blank" rel="noopener">Original clip ↗</a>`
      : '<span class="na">Source link pending</span>';

    // Header badges double as the legend for the value dots below.
    // A clip with the plume colourised into it gets no browser heat map (and no Model estimated badge).
    const heat = observed && meta.plume_rendering !== 'burned_in';
    const used = [...new Set([prov, observed && annP, observed && 'model_estimated'].filter((p) => p && p !== 'pending'))];
    $('meta-badge').innerHTML = (used.length ? used : ['pending']).map(badge).join('');

    $('meta-body').innerHTML = `
      <div id="status-block"></div>
      <div class="origin-line"><b>OGI footage: ${esc(window.AtelicShell.FACILITY.name)}</b>${origin}</div>
      <div class="kv">
        <div class="k">Plume region</div><div class="v na" id="v-region"><span>${observed ? '—' : 'Not available'}</span></div>
        ${heat ? row('Heat map', 'Pixel change, computed live', 'model_estimated') : ''}
        ${observed && !heat ? row('Plume rendering', 'Atelic detection model · pre-rendered', 'model_estimated') : ''}
        ${row('Source', `${CAM} · ${window.AtelicShell.FACILITY.short}, ${window.AtelicShell.FACILITY.location}`, prov)}
        ${row('Detection method', MODES[meta.detection_mode] || null, prov)}
      </div>
      <details class="more">
        <summary>More details <span>confidence, recording, camera, model, licence</span></summary>
        <div class="kv">
          <div class="k">Confidence (if supplied)</div><div class="v na" id="v-conf"><span>Not supplied</span></div>
          ${row('Recording time', meta.recording_date, prov)}
          ${row('Camera type', meta.camera_type, prov)}
          ${row('Resolution', meta.resolution, prov)}
          ${row('Frame rate', meta.frame_rate != null ? `${meta.frame_rate} fps${meta.variable_frame_rate ? ' (variable)' : ''}` : null, prov)}
          ${row('Model/version', meta.model_version, prov)}
          ${row('Licence', meta.license_status, meta.license_status === 'verified' ? prov : 'pending')}
        </div>
      </details>`;
  }


  // Status at the current replay moment: observed only once the replay reaches the first
  // annotated frame. Before that nothing has been detected yet, so the details stay hidden.
  let noDetectionNote = '';
  function renderStatus(now) {
    const el = $('status-block');
    if (!el) return;
    if (dismissal) { renderClosed(el); return; }
    let by;
    if (now) {
      by = annP === 'manual_annotation'
        ? `<span class="drawn"><svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8.5 1.5l2 2-6.5 6.5H2V8z"/></svg>Manually drawn</span>${ann.reviewed_by ? ` · reviewed by ${esc(ann.reviewed_by)}` : ''}`
        : (meta.model_version ? `Output of ${esc(meta.model_version)}` : 'Model output');
    } else if (byFrame.size) {
      by = 'All cameras watching. No plume seen yet.';
    } else if (!raised) {
      by = 'All cameras watching. No alert raised.';
    } else {
      by = noDetectionNote;
    }
    setAlert(now);
    // Nothing to investigate before the alert (or, on an annotated replay, before the plume appears).
    const waiting = !now && (byFrame.size > 0 || !raised);
    document.querySelector('.meta-panel').classList.toggle('waiting', waiting);
    $('btn-investigate').classList.toggle('disabled', waiting);
    $('btn-investigate').setAttribute('aria-disabled', String(waiting));
    $('btn-dismiss').hidden = !raised;
    el.className = `status ${now ? 'observed' : 'unconfirmed'}`;
    el.innerHTML = `<div class="lbl">Status</div>
      <div class="big"><span class="sig"></span>${now ? 'Plume observed' : waiting ? 'No detection' : 'Detection not confirmed'}</div>
      <div class="by">${by}</div>
      ${now ? `<div class="gb">${window.AtelicShell.traceLink('camera_event')}</div>` : ''}`;
  }

  // Closed as a false alarm by a named person: say who, why and what they saw; offer Reopen.
  function renderClosed(el) {
    const d = dismissal;
    setAlert(false);
    document.querySelector('.meta-panel').classList.remove('waiting');
    $('btn-investigate').classList.add('disabled');
    $('btn-investigate').setAttribute('aria-disabled', 'true');
    $('btn-dismiss').hidden = true;
    el.className = 'status closed';
    el.innerHTML = `<div class="lbl">Status</div>
      <div class="big"><span class="sig"></span>Closed as a false alarm</div>
      <div class="by">${esc(d.reason_label)} · by ${esc(d.by)} · ${esc(d.at.slice(11, 16))} UTC</div>
      <div class="said">“${esc(d.comment)}”</div>
      <div class="reopen"><button class="linkish" type="button" id="btn-reopen">Reopen incident</button></div>`;
    $('btn-reopen').addEventListener('click', () => window.AtelicShell.reopenDialog(window.AtelicShell.INCIDENT_ID, () => location.reload()));
  }

  function applyReplay(f) {
    if (firstFrame == null) return;
    // Latched like a real alarm: once seen, the alert stays when the replay loops.
    const now = observedNow || f >= firstFrame;
    if (now === observedNow) return;
    observedNow = now;
    renderStatus(now);
    window.AtelicShell.setReplayStatus('detect', now ? null : 'not_started');
  }

  // Per-frame fields: only what the annotation for this exact frame says.
  function renderFrameFields(a) {
    const conf = $('v-conf'), reg = $('v-region');
    if (!conf || !reg || !byFrame.size) return;
    const c = a && typeof a.confidence === 'number' ? a.confidence : null;
    conf.className = `v${c == null ? ' na' : ''}`;
    conf.innerHTML = c == null ? '<span>Not supplied</span>' : `<span class="pdot ${annP}"></span><span>${c.toFixed(2)}</span>`;
    const b = a && a.bbox_normalized;
    reg.className = `v${a ? '' : ' na'}`;
    reg.innerHTML = !a ? '<span>None on this frame</span>'
      : b ? `<span class="pdot ${annP}"></span><span>x ${b[0].toFixed(2)} y ${b[1].toFixed(2)} · ${b[2].toFixed(2)}×${b[3].toFixed(2)}</span>`
        : `<span class="pdot ${annP}"></span><span>Mask only</span>`;
  }

  // ----- camera wall -----
  // Every skid camera plays at once, so the viewer sees which one raises something.
  // The incident's camera is the annotated replay; the others loop a recorded segment picked as plume-free.
  let wall = null;
  const API = () => window.AtelicShell.API;

  // Incident camera screen: the pre-leak view during normal operations, the leak (with before/after compare) after the alert.
  function cam1Screen(mode, clipOk, origOk) {
    if (mode === 'before') {
      return `<div class="frame" id="frame-before"><video id="video-before" muted autoplay loop playsinline preload="auto" src="${API()}/incidents/${window.AtelicShell.INCIDENT_ID}/clip/before"></video><span class="tcx"><i></i><span>--:--</span></span></div>`;
    }
    return clipOk ? `<div class="frame${origOk ? ' cmp-on' : ''}" id="frame" style="--cut:50%">${origOk ? `<video id="video-raw" class="raw" muted autoplay loop playsinline preload="auto" src="${API()}/incidents/${window.AtelicShell.INCIDENT_ID}/clip/original"></video>` : ''}<video id="video" crossorigin="anonymous" muted autoplay loop playsinline preload="auto" src="${API()}/incidents/${window.AtelicShell.INCIDENT_ID}/clip"></video><canvas class="heat" id="heat" aria-hidden="true"></canvas><svg class="overlay" id="overlay" preserveAspectRatio="xMidYMin slice" aria-hidden="true"></svg><span class="tcx"><i></i><span>--:--</span></span>${origOk ? CMP_UI : ''}</div>`
      : `<div class="no-feed"><b>${esc(CAM)} leak recording not loaded</b><span>Licence status: ${esc(meta.license_status || 'unknown')}</span></div>`;
  }

  function renderWall(clipOk, origOk, mode) {
    const cams = wall ? wall.cameras : [{ camera_id: CAM, area: '', watches: [], feed: 'ogi_clip' }];
    const wallP = wall ? (wall.status === 'sample' ? 'simulated' : wall.provenance) : null;
    const video = (src) => `<div class="frame"><video muted autoplay loop playsinline preload="auto" src="${src}"></video><span class="tcx"><i></i><span>--:--</span></span></div>`;
    $('wall').innerHTML = cams.map((c) => {
      const mine = c.camera_id === CAM;
      // Another live incident on this camera: its normal view, or its alarm with a link to open it.
      const other = !mine && live.find((l) => l.camera_id === c.camera_id);
      const loop = !mine && !other && c.feed && typeof c.feed === 'object' && c.feed.file;
      const on = mine || other || loop;
      const tip = mine ? `${c.camera_id} · watches ${c.watches.join(', ')} · ${esc(window.AtelicShell.FACILITY.short)}`
        : on ? `${c.camera_id} · watches ${c.watches.join(', ')} · recorded, looped`
        : `${c.camera_id} · watches ${c.watches.join(', ')} · no feed`;
      const screen = mine ? cam1Screen(mode, clipOk, origOk)
        : other ? video(`${API()}/incidents/${encodeURIComponent(other.incident_id)}/clip${other.triggered && other.clip_available ? '' : '/before'}`)
        : loop ? video(`${API()}/ogi/cams/${encodeURIComponent(c.camera_id)}`)
        : '<div class="no-feed"><b>No feed</b></div>';
      const state = other && other.dismissal ? 'Closed · false alarm'
        : other && other.triggered
        ? `ALARM · <a class="open-inc" href="${window.AtelicShell.incHref('detection', other.incident_id)}">${esc(other.incident_id)} →</a>`
        : on ? 'Watching' : 'No feed';
      const otherOpen = other && other.triggered && !other.dismissal;
      return `<div class="cell${mine ? ' cam1' : ''}${otherOpen ? ' alert' : ''}" title="${esc(otherOpen ? `${tip} · click to investigate ${other.incident_id}` : tip)}"${otherOpen ? ` data-href="${window.AtelicShell.incHref('detection', other.incident_id)}"` : ''}>
        <div class="cv">${screen}</div>
        <div class="cl"><b>${esc(c.camera_id)}</b><span class="ar">${esc(c.area)}</span>
          <span class="st${on ? ' on' : ''}"><i></i><span class="sv">${state}</span></span>
          ${badge(mine ? prov : wallP)}</div>
      </div>`;
    }).join('');
    // Same replay clock on every feed (the shell's incident clock), not each clip's own position,
    // so the four cameras show one moment and the time keeps running across loops.
    const tick = () => {
      const now = window.AtelicShell.replayNow();
      const txt = now ? window.AtelicShell.fmtUtc(now, true) : '--:--:--';
      $('wall').querySelectorAll('.frame .tcx span').forEach((s) => { s.textContent = txt; });
    };
    tick();
    setInterval(tick, 1000);
    $('wall-foot').textContent = `Recorded replays, looped · ${window.AtelicShell.FACILITY.name}, ${window.AtelicShell.FACILITY.location}`;
  }

  // An alert raised with no annotated recording behind it says so instead of "Watching".
  // ----- active alarms -----
  // Open alarms (raised, not closed), oldest first. Listed when another incident's alarm is open, so you can see
  // them side by side and pick which one to investigate.
  const openAlarms = () => live.filter((l) => l.triggered && !l.dismissal)
    .sort((a, b) => String(a.triggered_at).localeCompare(String(b.triggered_at)));
  function renderAlarms() {
    const box = $('alarms'), open = openAlarms(), me = window.AtelicShell.INCIDENT_ID;
    if (!box) return;
    box.hidden = !open.some((l) => l.incident_id !== me);
    if (box.hidden) return;
    box.innerHTML = `<span class="al-k">Alarms · ${open.length}</span>${open.map((l) => {
      const w = window.AtelicShell.whereNow(l), mine = l.incident_id === me;
      const body = `<i></i><b>${esc(l.incident_id)}</b><span title="${esc(w.text)}">${esc(l.camera_id)}${l.asset_id ? ` · near ${esc(l.asset_id)}` : ''}</span>`;
      return mine ? `<span class="al" aria-current="true">${body}<span class="tag">Investigating</span></span>`
        : `<a class="al" href="${window.AtelicShell.incHref('detection', l.incident_id)}" title="Investigate ${esc(l.incident_id)}">${body}<span class="go">Investigate →</span></a>`;
    }).join('')}`;
  }

  function setAlert(now) {
    const c = document.querySelector('.cell.cam1');
    if (!c) return;
    const bare = raised && !byFrame.size;
    c.classList.toggle('alert', !dismissal && (!!now || bare));
    c.querySelector('.sv').textContent = dismissal ? 'Closed · false alarm' : now ? 'ALARM · Plume observed' : bare ? 'ALERT · no recording' : 'Watching';
  }

  // ----- incident camera before/after: original infrared (left) vs Atelic detection output (right) -----
  const CMP_UI = `<span class="cmp-tag l">Original IR</span><span class="cmp-tag r">Atelic detection</span>
    <span class="cmp-line" aria-hidden="true"></span>
    <span class="cmp-grip" id="cmp-grip" role="slider" tabindex="0" aria-label="Compare original infrared with Atelic detection"
      aria-valuemin="0" aria-valuemax="100" aria-valuenow="50" aria-valuetext="50% original"></span>`;

  function wireCompare() {
    const fr = $('frame'), grip = $('cmp-grip');
    if (!fr || !grip) return;
    const set = (pct) => {
      pct = Math.max(0, Math.min(100, pct));
      fr.style.setProperty('--cut', `${pct}%`);
      grip.setAttribute('aria-valuenow', String(Math.round(pct)));
      grip.setAttribute('aria-valuetext', `${Math.round(pct)}% original`);
    };
    const fromX = (x) => { const r = fr.getBoundingClientRect(); set((x - r.left) / r.width * 100); };
    // Drag anywhere on the frame, not just the grip, so it is easy to hit at booth distance.
    fr.addEventListener('pointerdown', (e) => {
      fr.setPointerCapture(e.pointerId); fr.classList.add('dragging'); fromX(e.clientX);
      const move = (ev) => fromX(ev.clientX);
      const up = () => { fr.classList.remove('dragging'); fr.removeEventListener('pointermove', move); };
      fr.addEventListener('pointermove', move);
      fr.addEventListener('pointerup', up, { once: true });
      fr.addEventListener('pointercancel', up, { once: true });
    });
    grip.addEventListener('keydown', (e) => {
      const now = Number(grip.getAttribute('aria-valuenow'));
      const to = { ArrowLeft: now - 5, ArrowRight: now + 5, Home: 0, End: 100 }[e.key];
      if (to == null) return;
      e.preventDefault(); set(to);
    });
  }

  // The Atelic output drives the timeline; the original follows it. Small drift is pulled in by nudging
  // the playback rate (seeking every frame would stutter); a loop wrap or a big jump gets one seek.
  function syncRaw(t) {
    const raw = $('video-raw');
    if (!raw || raw.readyState < 2) return;
    const diff = t - raw.currentTime;
    if (Math.abs(diff) > 0.25) { raw.currentTime = t; raw.playbackRate = 1; return; }
    raw.playbackRate = Math.max(0.9, Math.min(1.1, 1 + diff * 2));
  }

  // ----- incident camera replay -----
  // With a frame-time table (variable-rate clips) look the frame up; otherwise derive it from fps.
  function frameAt(t) {
    if (!times) return Math.min(total - 1, Math.max(0, Math.floor(t * fps + 1e-3)));
    let lo = 0, hi = times.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t + 1e-4) lo = mid; else hi = mid - 1; }
    return lo;
  }

  function drawOverlay(f) {
    const svg = $('overlay');
    const a = byFrame.get(f);
    if (!svg) return a;
    const W = video.videoWidth || 1, H = video.videoHeight || 1;
    let g = '';
    if (a) {
      const poly = a.mask_polygon_normalized || [];
      if (poly.length > 2) g += `<polygon class="mask" points="${poly.map(([x, y]) => `${x * W},${y * H}`).join(' ')}"/>`;
      if (a.origin_normalized) {
        const [x, y] = [a.origin_normalized[0] * W, a.origin_normalized[1] * H], r = W * 0.012;
        g += `<g class="origin"><circle cx="${x}" cy="${y}" r="${r}"/><line x1="${x - r * 2.2}" y1="${y}" x2="${x - r}" y2="${y}"/><line x1="${x + r}" y1="${y}" x2="${x + r * 2.2}" y2="${y}"/><line x1="${x}" y1="${y - r * 2.2}" x2="${x}" y2="${y - r}"/><line x1="${x}" y1="${y + r}" x2="${x}" y2="${y + r * 2.2}"/></g>`;
      }
    }
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = g;
    return a;
  }

  // Heat map: see plumeheat.js (computed from the incident camera's pixels, drawn only inside the drawn region).
  let heatStep = null;

  function update(t) {
    if (!fps) return;
    const f = frameAt(t);
    syncRaw(t);
    if (f === frame) return;
    frame = f;
    applyReplay(f);
    const a = drawOverlay(f);
    if (heatStep) heatStep(a && a.bbox_normalized);
    renderFrameFields(a);
  }

  function wireVideo(frames) {
    video = $('video');
    if (!video) return;
    video.addEventListener('loadedmetadata', () => {
      if (times) total = times.length;
      else if (fps) total = Math.max(1, Math.round(video.duration * fps));
      firstFrame = frames.length && fps ? Math.min(...frames.map((a) => a.frame)) : null;
      if (meta.plume_rendering !== 'burned_in') heatStep = window.PlumeHeat.create(video, $('heat'));
      window.PlumeHeat.eachFrame(video, update);
      update(video.currentTime);
    }, { once: true });
    video.addEventListener('error', () => {
      $('frame').outerHTML = `<div class="no-feed"><b>Recording could not be played</b><span>Check the ${esc(CAM)} clip file (H.264 MP4)</span></div>`;
    }, { once: true });
  }

  // ----- normal operations -> alert event -----
  function startEvent(d, frames, origOk) {
    wireCompare();
    if (frames.length) window.AtelicShell.setReplayStatus('detect', 'not_started');
    if (d.clip_available) wireVideo(frames);
  }

  // Presenter controls in the camera panel header: pick a camera with no alert yet and trigger it (or
  // press T), reset this incident for the next run.
  let pick = null;
  function renderControls(mode) {
    const box = $('event-ctl');
    if (!box) return;
    const idle = live.filter((l) => !l.triggered && !(l.incident_id === window.AtelicShell.INCIDENT_ID && mode === 'event'));
    if (!idle.some((l) => l.incident_id === pick)) {
      pick = (idle.find((l) => l.incident_id === window.AtelicShell.INCIDENT_ID) || idle[0] || {}).incident_id || null;
    }
    box.innerHTML = `${idle.length > 1 ? '<span class="ctl-pick" id="trig-pick"></span>' : ''}${pick
      ? `<button class="ctl" id="btn-trigger" type="button" title="Shortcut: T">Trigger ${esc(live.find((l) => l.incident_id === pick).camera_id)} event <kbd>T</kbd></button>` : ''}${mode === 'event'
      ? '<button class="ctl" id="btn-reset" type="button" title="Back to normal operations">Reset</button>' : ''}`;
    if (idle.length > 1) {
      window.AtelicShell.dropdown($('trig-pick'), {
        label: 'Camera to trigger', value: pick,
        // Triggering another camera opens its incident; the option says so.
        options: idle.map((l) => ({ value: l.incident_id, label: l.camera_id,
          sub: l.incident_id === window.AtelicShell.INCIDENT_ID ? `${l.incident_id} · this screen` : `opens ${l.incident_id}` })),
        onChange: (v) => { pick = v; renderControls(mode); },
      });
    }
    const t = $('btn-trigger'), r = $('btn-reset');
    if (t) t.addEventListener('click', trigger);
    if (r) r.addEventListener('click', reset);
  }

  let triggering = false;
  async function trigger() {
    if (triggering || !$('btn-trigger') || !pick) return;
    triggering = true;
    // Another camera's alert opens its own incident.
    if (pick !== window.AtelicShell.INCIDENT_ID) {
      try {
        await post(`/incidents/${pick}/trigger`);
        location.href = window.AtelicShell.incHref('detection', pick);
      } catch (e) { console.error(e); triggering = false; }
      return;
    }
    try {
      await post(`/incidents/${window.AtelicShell.INCIDENT_ID}/trigger`);
      live = live.map((l) => (l.incident_id === window.AtelicShell.INCIDENT_ID ? { ...l, triggered: true } : l));
      raised = true;
      const d = await window.AtelicShell.get(`/incidents/${window.AtelicShell.INCIDENT_ID}/detections`);
      const frames = usableAnnotations(d);
      const origOk = d.clip_available && d.original_available && meta.plume_rendering === 'burned_in';
      // The incident camera is a PTZ view: on the alert it zooms from the wide shot to the first asset it watches,
      // then the leak view takes over.
      const cell = document.querySelector('.cell.cam1'), cv = cell && cell.querySelector('.cv');
      if (cv) {
        cell.classList.add('ptz');
        await new Promise((ok) => setTimeout(ok, 650));
        const cam = wall && wall.cameras.find((c) => c.camera_id === CAM), target = cam && cam.watches[0];
        cv.innerHTML = cam1Screen('event', d.clip_available, origOk) + (target ? `<span class="ptz-note">PTZ · zoom to ${esc(target)}</span>` : '');
        const now = window.AtelicShell.replayNow(), stamp = cv.querySelector('.tcx span');
        if (now && stamp) stamp.textContent = window.AtelicShell.fmtUtc(now, true);
        cell.classList.remove('ptz');
        setTimeout(() => { const n = cv.querySelector('.ptz-note'); if (n) n.remove(); }, 2500);
      }
      startEvent(d, frames, origOk);
      renderStatus(observedNow);
      renderControls('event');
      window.AtelicShell.refresh();
      // Fresh list (likely source, stage) for the alarm list and tiles.
      live = (await window.AtelicShell.get('/incidents').catch(() => ({ live }))).live;
      renderAlarms();
    } catch (e) {
      console.error(e);
      triggering = false;
    }
  }

  async function reset() {
    try { await post(`/incidents/${window.AtelicShell.INCIDENT_ID}/reset`); } catch (e) { console.error(e); }
    location.reload();
  }

  async function post(path) {
    const r = await fetch(window.AtelicShell.API + path, { method: 'POST' });
    if (!r.ok) throw new Error(`${path} ${r.status}`);
    return r.json();
  }

  document.addEventListener('click', (e) => {
    const cell = e.target.closest('.cell[data-href]');
    if (cell && !e.target.closest('a, button')) location.href = cell.dataset.href;
    if (e.target.closest('#btn-dismiss')) window.AtelicShell.dismissDialog(window.AtelicShell.INCIDENT_ID, () => location.reload());
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 't' && e.key !== 'T') return;
    if (e.metaKey || e.ctrlKey || e.altKey || /input|textarea|select/i.test(e.target.tagName)) return;
    trigger();
  });

  async function init() {
    try {
      const [d, list] = await Promise.all([window.AtelicShell.get(`/incidents/${window.AtelicShell.INCIDENT_ID}/detections`),
        window.AtelicShell.get('/incidents').catch(() => ({ live: [] }))]);
      CAM = d.camera_id || CAM;
      raised = d.triggered;
      dismissal = d.dismissal || null;
      live = list.live || [];
      meta = d.metadata; ann = d.annotations; wall = d.camera_wall;
      prov = clipProv(meta);
      annP = ann.status === 'sample' ? 'simulated' : (CLIP_PROV.has(ann.provenance) ? ann.provenance : 'pending');
      fps = typeof meta.frame_rate === 'number' && meta.frame_rate > 0 ? meta.frame_rate : null;
      times = d.frame_times && d.frame_times.frame_start_s && d.frame_times.frame_start_s.length ? d.frame_times.frame_start_s : null;
      if (times && !fps) fps = times.length / d.frame_times.end_s;
      const frames = usableAnnotations(d);
      frames.forEach((a) => byFrame.set(a.frame, a));

      $('clip-badge').innerHTML = wall ? badge(wall.status === 'sample' ? 'simulated' : wall.provenance) : '';
      const origOk = d.clip_available && d.original_available && meta.plume_rendering === 'burned_in';
      // Normal operations (no alert yet) show the camera's pre-leak view; without that clip, go straight to the event.
      const before = !d.triggered && d.before_available;
      renderWall(d.clip_available, origOk, before ? 'before' : 'event');
      renderAlarms();
      renderMeta(d, frames);
      renderStatus(false);
      if (before) {
        renderControls('before');
        return;
      }
      startEvent(d, frames, origOk);
      renderControls('event');
    } catch (e) {
      console.error(e);
      $('meta-badge').innerHTML = badge('pending');
      $('meta-body').innerHTML = '<div class="status">Detection details are unavailable: backend is not reachable.</div>';
      $('wall').innerHTML = '<div class="no-feed wide"><b>Cameras unavailable</b><span>Backend is not reachable</span></div>';
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
