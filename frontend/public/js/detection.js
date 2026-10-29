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
      <div class="origin-line"><b>OGI camera: ${esc(meta.camera_type || 'Not recorded')}</b>${origin}</div>
      <div class="kv">
        <div class="k">Plume region</div><div class="v na" id="v-region"><span>${observed ? '—' : 'Not available'}</span></div>
        ${heat ? row('Heat map', 'Pixel change, computed live', 'model_estimated') : ''}
        ${observed && !heat ? row('Plume rendering', 'Atelic detection model · pre-rendered', 'model_estimated') : ''}
        ${row('Source', `${CAM} · ${meta.camera_type ? `${meta.camera_type} · ` : ''}${window.AtelicShell.FACILITY.short}, ${window.AtelicShell.FACILITY.location}`, prov)}
        ${meta.view_note ? row('Camera view', meta.view_note, prov) : ''}
        ${row('Detection method', MODES[meta.detection_mode] || null, prov)}
        ${meta.metered_release && meta.metered_release.rate_g_h ? row('Release on camera', `${meta.metered_release.rate_g_h} g/h metered (METEC, GasVid class 1)`, 'experimental_setpoint', 'v-release') : ''}
        ${d.alert && d.alert.clip_time_seconds ? row('Model alert', `Clip ${d.alert.clip_time_seconds.toFixed(1)} s · 3 of 5 one-second samples positive`, 'model_estimated') : ''}
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
          ${row('Footage', 'Recorded METEC controlled release, a different scene from the synthetic plant; only the metered rate is matched', 'experimental_setpoint')}
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

  // Incident camera screen: one continuous recording of the event. The Atelic view (the plume as the model
  // captured it, rendered into the video) with the untouched original infrared on the slider, from the start of the
  // monitored window; the alarm latches from the executed detector samples while it plays. Until the alarm only the
  // original infrared shows (no slider, no Atelic view); the alarm reveals the comparison (revealCompare).
  // Default state is quiet: until Trigger the camera loops plume-free footage (#video-quiet) over the paused event
  // recording, so nothing is detected or raised on its own.
  function cam1Screen(mode, clipOk, origOk) {
    const quiet = !raised, auto = quiet ? '' : ' autoplay', inc = window.AtelicShell.INCIDENT_ID;
    const low = meta.frame_anchor === 'bottom';
    return clipOk ? `<div class="frame${origOk ? ' cmp-on pre' : ''}${low ? ' anc-b' : ''}" id="frame" style="--cut:50%">${origOk ? `<video id="video-raw" class="raw" muted${auto} playsinline preload="auto" src="${API()}/incidents/${inc}/clip/original"></video>` : ''}<video id="video" crossorigin="anonymous" muted${auto} playsinline preload="auto" src="${API()}/incidents/${inc}/clip"></video>${quiet ? `<video id="video-quiet" muted autoplay loop playsinline preload="auto" src="${API()}/incidents/${inc}/clip/before"></video>` : ''}<canvas class="heat" id="heat" aria-hidden="true"></canvas><svg class="overlay" id="overlay" preserveAspectRatio="xMid${low ? 'YMax' : 'YMin'} slice" aria-hidden="true"></svg><span class="mon-tag" id="mon-tag"></span><span class="tcx"><i></i><span>--:--</span></span>${origOk ? CMP_UI : ''}</div>`
      : `<div class="no-feed"><b>${esc(CAM)} leak recording not loaded</b><span>Licence status: ${esc(meta.license_status || 'unknown')}</span></div>`;
  }

  function renderWall(clipOk, origOk, mode) {
    const cams = wall ? wall.cameras : [{ camera_id: CAM, area: '', watches: [], feed: 'ogi_clip' }];
    const wallP = wall ? (wall.status === 'sample' ? 'simulated' : wall.provenance) : null;
    const video = (src, low) => `<div class="frame${low ? ' anc-b' : ''}"><video muted autoplay loop playsinline preload="auto" src="${src}"></video><span class="tcx"><i></i><span>--:--</span></span></div>`;
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
        : other ? video(`${API()}/incidents/${encodeURIComponent(other.incident_id)}/clip${other.triggered && other.clip_available ? '' : '/before'}`, other.frame_anchor === 'bottom')
        : loop ? video(`${API()}/ogi/cams/${encodeURIComponent(c.camera_id)}`)
        : '<div class="no-feed"><b>No feed</b></div>';
      const state = other && other.dismissal ? 'Closed · false alarm'
        : other && other.triggered
        ? `ALERT · <a class="open-inc" href="${window.AtelicShell.incHref('detection', other.incident_id)}">${esc(other.incident_id)} →</a>`
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
    box.innerHTML = `<span class="al-k">Alerts · ${open.length}</span>${open.map((l) => {
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
    c.querySelector('.sv').textContent = dismissal ? 'Closed · false alarm' : now ? 'ALERT · Plume observed' : bare ? 'ALERT · no recording' : 'Watching';
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
      if (fr.classList.contains('pre')) return;
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

  // At the alarm: the Atelic view slides in over the original, leaving the slider at the middle.
  function revealCompare() {
    const fr = $('frame');
    if (!fr || !fr.classList.contains('pre')) return;
    fr.classList.add('reveal');
    fr.classList.remove('pre');
    setTimeout(() => fr.classList.remove('reveal'), 1000);
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
  // press T), reset every live incident for the next run.
  let pick = null;
  function renderControls(mode) {
    const box = $('event-ctl');
    if (!box) return;
    const idle = live.filter((l) => !l.triggered && !(l.incident_id === window.AtelicShell.INCIDENT_ID && mode === 'event'));
    if (!idle.some((l) => l.incident_id === pick)) {
      pick = (idle.find((l) => l.incident_id === window.AtelicShell.INCIDENT_ID) || idle[0] || {}).incident_id || null;
    }
    box.innerHTML = `${idle.length > 1 ? '<span class="ctl-pick" id="trig-pick"></span>' : ''}${pick
      ? `<button class="ctl" id="btn-trigger" type="button" title="Shortcut: T">Trigger ${esc(live.find((l) => l.incident_id === pick).camera_id)} event <kbd>T</kbd></button>` : ''}${mode === 'event' || live.some((l) => l.triggered)
      ? '<button class="ctl" id="btn-reset" type="button" title="Back to normal operations: clears every incident\'s alert and results">Reset</button>' : ''}`;
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

  // Trigger while the camera is quiet: cut from the quiet loop straight to the model alarm in the event recording, so
  // the plume and the detection appear together; the alarm latches from the model's own samples and raises the
  // incident (raise below). Otherwise raise directly.
  let monitor = null, alerted = false;
  function fastForward() {
    const v = $('video');
    if (!monitor || monitor.alert_t == null || !v || v.readyState < 1) return false;
    if (v.currentTime < monitor.alert_t) v.currentTime = monitor.alert_t;
    v.play().catch(() => {});
    const raw = $('video-raw');
    if (raw) { raw.currentTime = monitor.alert_t; raw.play().catch(() => {}); }
    const q = $('video-quiet');
    if (q) q.remove();
    const tag = $('mon-tag');
    if (tag) { tag.textContent = 'Model alert'; tag.className = 'mon-tag ff'; setTimeout(() => tag.classList.remove('ff'), 2500); }
    return true;
  }

  // Follows the recording: model status from the executed samples (causal: at or before the playhead), the metered
  // release on camera, and the alarm moment. Nothing is drawn on the picture; the plume is in the render itself.
  function wireMonitor(m, liveRun) {
    monitor = m;
    const v = $('video'), tag = $('mon-tag');
    if (!v || !m) return;
    const seg = (t) => (m.segments || []).find((x) => x.start <= t && t < x.end);
    // After the alarm the recording keeps looping its post-alarm part, never back to quiet footage.
    v.addEventListener('ended', () => { v.currentTime = m.alert_t != null ? m.alert_t : 0; v.play().catch(() => {}); });
    if (!liveRun) revealCompare();
    if (!liveRun && m.alert_t != null) {
      const seek = () => { v.currentTime = m.alert_t; };
      if (v.readyState >= 1) seek(); else v.addEventListener('loadedmetadata', seek, { once: true });
    }
    let shown = '';
    const step = () => {
      const t = v.currentTime;
      let i = -1;
      for (let k = 0; k < m.samples.length && m.samples[k].t <= t + 1e-3; k++) i = k;
      const recent = m.samples.slice(Math.max(0, i - m.policy.window_samples + 1), i + 1).filter((x) => x.positive).length;
      const past = m.alert_t != null && t >= m.alert_t;
      if (past) revealCompare();
      const text = past || raised ? '' : recent ? `Model: possible plume · ${recent} of ${m.policy.window_samples} recent samples` : '';  // idle: the tile's "Watching" says it
      if (tag && !tag.classList.contains('ff') && text !== shown) {
        shown = text;
        tag.textContent = text;
        tag.className = `mon-tag${recent ? ' pos' : ''}`;
      }
      const g = seg(t), rel = $('v-release');
      if (rel && g) rel.innerHTML = `<span class="pdot experimental_setpoint"></span><span>${g.rate_g_h} g/h metered · ${esc(g.label || '')}</span>`;
      if (liveRun && !alerted && !raised && past) { alerted = true; raise(); }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  let triggering = false;
  async function trigger() {
    if (triggering || !$('btn-trigger') || !pick) return;
    if (pick === window.AtelicShell.INCIDENT_ID && !raised && fastForward()) return;
    // Another camera: open its screen and fast-forward there, so its own model alarm raises its incident.
    if (pick !== window.AtelicShell.INCIDENT_ID) { location.href = `${window.AtelicShell.incHref('detection', pick)}&ff=1`; return; }
    raise();
  }

  async function raise() {
    if (triggering || !pick) return;
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
      const continuous = Boolean($('video'));  // the event recording is already playing: keep it, no cut
      if (cv && !continuous) {
        cell.classList.add('ptz');
        await new Promise((ok) => setTimeout(ok, 650));
        const cam = wall && wall.cameras.find((c) => c.camera_id === CAM), target = cam && cam.watches[0];
        cv.innerHTML = cam1Screen('event', d.clip_available, origOk) + (target ? `<span class="ptz-note">PTZ · zoom to ${esc(target)}</span>` : '');
        const now = window.AtelicShell.replayNow(), stamp = cv.querySelector('.tcx span');
        if (now && stamp) stamp.textContent = window.AtelicShell.fmtUtc(now, true);
        cell.classList.remove('ptz');
        setTimeout(() => { const n = cv.querySelector('.ptz-note'); if (n) n.remove(); }, 2500);
      }
      if (!continuous) startEvent(d, frames, origOk);
      renderStatus(observedNow);
      renderControls('event');
      window.AtelicShell.refresh();
      // Fresh list (likely source, stage) for the alarm list and tiles.
      live = (await window.AtelicShell.get('/incidents').catch(() => ({ live }))).live;
      renderAlarms();
      renderControls('event');
      triggering = false;
    } catch (e) {
      console.error(e);
      triggering = false;
    }
  }

  async function reset() {
    // All live incidents together, so the next run starts from a quiet site.
    await Promise.all(live.map((l) => post(`/incidents/${l.incident_id}/reset`).catch((e) => console.error(e))));
    location.reload();
  }

  async function post(path) {
    const r = await fetch(window.AtelicShell.API + path, { method: 'POST' });
    if (!r.ok) throw new Error(`${path} ${r.status}`);
    return r.json();
  }

  document.addEventListener('click', (e) => {
    const cell = e.target.closest('.cell[data-href]');
    if (cell && !e.target.closest('a, button')) window.AtelicShell.confirmSwitch(cell.dataset.href);
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
      // One continuous recording: from the start of the monitored window while no alarm has been raised, from the
      // alarm on once the incident exists.
      const before = !d.triggered;
      renderWall(d.clip_available, origOk, 'event');
      renderAlarms();
      renderMeta(d, frames);
      renderStatus(false);
      startEvent(d, frames, origOk);
      wireMonitor(d.monitor, before);
      renderControls(before ? 'before' : 'event');
      // Arrived from another camera's Trigger: fast-forward this recording to its alarm.
      if (before && new URLSearchParams(location.search).get('ff') === '1') {
        history.replaceState(null, '', location.pathname + location.search.replace(/[?&]ff=1/, ''));
        const v = $('video');
        if (v && v.readyState >= 1) fastForward(); else if (v) v.addEventListener('loadedmetadata', fastForward, { once: true });
      }
    } catch (e) {
      console.error(e);
      $('meta-badge').innerHTML = badge('pending');
      $('meta-body').innerHTML = '<div class="status">Detection details are unavailable: backend is not reachable.</div>';
      $('wall').innerHTML = '<div class="no-feed wide"><b>Cameras unavailable</b><span>Backend is not reachable</span></div>';
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
