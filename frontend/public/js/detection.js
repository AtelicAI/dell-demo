// Screen 2 — Detect / Camera. Reads GET /incidents/:id/detections. A 2×2 wall plays all skid cameras;
// CAM-01 replays GET /ogi/clip, the others loop GET /ogi/cams/:id.
// Overlays are drawn only for frames that have an annotation; nothing is interpolated or inferred.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const BADGE = {
    experimental_setpoint: 'Experimental setpoint', manual_annotation: 'Manual annotation',
    model_estimated: 'Model estimated', provider_reported: 'Provider reported', simulated: 'Simulated',
  };
  const MODES = {
    external_detector_alert: 'External detector alert',
    local_model_inference: 'Local model inference',
    recorded_model_output: 'Recorded model output',
    manually_annotated_replay: 'Manually annotated replay',
  };

  // Only a verified record carries its stated provenance; a sample gets no badge; anything else is Pending.
  function clipProv(m) {
    if (m.status === 'verified' && BADGE[m.provenance]) return m.provenance;
    return m.status === 'sample' ? 'simulated' : 'pending';
  }
  // Simulated / synthetic provenance is tracked in the data but not shown.
  const badge = (p) => p === 'simulated' || p === 'synthetic' ? '' : `<span class="badge ${p}">${BADGE[p] || 'Pending'}</span>`;

  let meta, ann, prov, annP, fps = null, times = null, total = 0, firstFrame = null, observedNow = null, byFrame = new Map(), video = null, frame = -1;

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
        ${row('Source', `CAM-01 · ${window.AtelicShell.FACILITY.short}, ${window.AtelicShell.FACILITY.location}`, prov)}
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
    let by;
    if (now) {
      by = annP === 'manual_annotation'
        ? `<span class="drawn"><svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8.5 1.5l2 2-6.5 6.5H2V8z"/></svg>Manually drawn</span>${ann.reviewed_by ? ` · reviewed by ${esc(ann.reviewed_by)}` : ''}`
        : (meta.model_version ? `Output of ${esc(meta.model_version)}` : 'Model output');
    } else if (byFrame.size) {
      by = 'All cameras watching. No plume seen yet.';
    } else {
      by = noDetectionNote;
    }
    setAlert(now);
    const waiting = !now && byFrame.size > 0;
    document.querySelector('.meta-panel').classList.toggle('waiting', waiting);
    $('btn-investigate').classList.toggle('disabled', waiting);
    $('btn-investigate').setAttribute('aria-disabled', String(waiting));
    el.className = `status ${now ? 'observed' : 'unconfirmed'}`;
    el.innerHTML = `<div class="lbl">Status</div>
      <div class="big"><span class="sig"></span>${now ? 'Plume observed' : waiting ? 'No detection' : 'Detection not confirmed'}</div>
      <div class="by">${by}</div>
      ${now ? `<div class="gb">${window.AtelicShell.traceLink('camera_event')}</div>` : ''}`;
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
  // Every skid camera plays at once, so the viewer sees that only CAM-01 raises something.
  // CAM-01 is the annotated replay; the others loop a recorded segment picked as plume-free.
  let wall = null;
  const API = () => window.AtelicShell.API;

  // CAM-01 screen: the pre-leak view during normal operations, the leak (with before/after compare) after the alert.
  function cam1Screen(mode, clipOk, origOk) {
    if (mode === 'before') {
      return `<div class="frame" id="frame-before"><video id="video-before" muted autoplay loop playsinline preload="auto" src="${API()}/ogi/clip/before"></video><span class="tcx"><i></i><span>--:--</span></span></div>`;
    }
    return clipOk ? `<div class="frame${origOk ? ' cmp-on' : ''}" id="frame" style="--cut:50%">${origOk ? `<video id="video-raw" class="raw" muted autoplay loop playsinline preload="auto" src="${API()}/ogi/clip/original"></video>` : ''}<video id="video" crossorigin="anonymous" muted autoplay loop playsinline preload="auto" src="${API()}/ogi/clip"></video><canvas class="heat" id="heat" aria-hidden="true"></canvas><svg class="overlay" id="overlay" preserveAspectRatio="xMidYMin slice" aria-hidden="true"></svg><span class="tcx"><i></i><span>--:--</span></span>${origOk ? CMP_UI : ''}</div>`
      : `<div class="no-feed"><b>Camera clip not loaded</b><span>Licence status: ${esc(meta.license_status || 'unknown')}</span></div>`;
  }

  function renderWall(clipOk, origOk, mode) {
    const cams = wall ? wall.cameras : [{ camera_id: 'CAM-01', area: '', watches: [], feed: 'ogi_clip' }];
    const wallP = wall ? (wall.status === 'sample' ? 'simulated' : wall.provenance) : null;
    $('wall').innerHTML = cams.map((c) => {
      const live = c.feed === 'ogi_clip';
      const loop = c.feed && typeof c.feed === 'object' && c.feed.file;
      const tip = live ? `${c.camera_id} · watches ${c.watches.join(', ')} · ${esc(window.AtelicShell.FACILITY.short)}`
        : loop ? `${c.camera_id} · watches ${c.watches.join(', ')} · recorded, looped`
        : `${c.camera_id} · watches ${c.watches.join(', ')} · no feed`;
      const screen = live
        ? cam1Screen(mode, clipOk, origOk)
        : loop ? `<div class="frame"><video muted autoplay loop playsinline preload="auto" src="${API()}/ogi/cams/${encodeURIComponent(c.camera_id)}"></video><span class="tcx"><i></i><span>--:--</span></span></div>`
        : '<div class="no-feed"><b>No feed</b></div>';
      return `<div class="cell${live ? ' cam1' : ''}" title="${esc(tip)}">
        <div class="cv">${screen}</div>
        <div class="cl"><b>${esc(c.camera_id)}</b><span class="ar">${esc(c.area)}</span>
          <span class="st${loop || live ? ' on' : ''}"><i></i><span class="sv">${loop || live ? 'Watching' : 'No feed'}</span></span>
          ${badge(live ? prov : wallP)}</div>
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

  function setAlert(now) {
    const c = document.querySelector('.cell.cam1');
    if (!c) return;
    c.classList.toggle('alert', !!now);
    c.querySelector('.sv').textContent = now ? 'ALARM · Plume observed' : 'Watching';
  }

  // ----- CAM-01 before/after: original infrared (left) vs Atelic detection output (right) -----
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

  // ----- CAM-01 replay -----
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

  // Heat map: see plumeheat.js (computed from CAM-01's pixels, drawn only inside the drawn region).
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
      $('frame').outerHTML = '<div class="no-feed"><b>Recording could not be played</b><span>Check data/ogi/source_clip.mp4 (H.264 MP4)</span></div>';
    }, { once: true });
  }

  // ----- normal operations -> alert event -----
  function startEvent(d, frames, origOk) {
    wireCompare();
    if (frames.length) window.AtelicShell.setReplayStatus('detect', 'not_started');
    if (d.clip_available) wireVideo(frames);
  }

  // Presenter controls in the camera panel header: trigger the alert (or press T), reset for the next run.
  function renderControls(mode) {
    const box = $('event-ctl');
    if (!box) return;
    box.innerHTML = mode === 'before'
      ? '<button class="ctl" id="btn-trigger" type="button" title="Shortcut: T">Trigger event <kbd>T</kbd></button>'
      : '<button class="ctl" id="btn-reset" type="button" title="Back to normal operations">Reset</button>';
    const t = $('btn-trigger'), r = $('btn-reset');
    if (t) t.addEventListener('click', trigger);
    if (r) r.addEventListener('click', reset);
  }

  let triggering = false;
  async function trigger() {
    if (triggering || !$('btn-trigger')) return;
    triggering = true;
    try {
      await post(`/incidents/${window.AtelicShell.INCIDENT_ID}/trigger`);
      const d = await window.AtelicShell.get(`/incidents/${window.AtelicShell.INCIDENT_ID}/detections`);
      const frames = usableAnnotations(d);
      const origOk = d.clip_available && d.original_available && meta.plume_rendering === 'burned_in';
      // CAM-01 is a PTZ view: on the alert it zooms from the wide shot to the vent, then the leak view takes over.
      const cell = document.querySelector('.cell.cam1'), cv = cell && cell.querySelector('.cv');
      if (cv) {
        cell.classList.add('ptz');
        await new Promise((ok) => setTimeout(ok, 650));
        cv.innerHTML = cam1Screen('event', d.clip_available, origOk) + '<span class="ptz-note">PTZ · zoom to VENT-01</span>';
        const now = window.AtelicShell.replayNow(), stamp = cv.querySelector('.tcx span');
        if (now && stamp) stamp.textContent = window.AtelicShell.fmtUtc(now, true);
        cell.classList.remove('ptz');
        setTimeout(() => { const n = cv.querySelector('.ptz-note'); if (n) n.remove(); }, 2500);
      }
      startEvent(d, frames, origOk);
      renderControls('event');
      window.AtelicShell.refresh();
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

  document.addEventListener('keydown', (e) => {
    if (e.key !== 't' && e.key !== 'T') return;
    if (e.metaKey || e.ctrlKey || e.altKey || /input|textarea|select/i.test(e.target.tagName)) return;
    trigger();
  });

  async function init() {
    try {
      const d = await window.AtelicShell.get(`/incidents/${window.AtelicShell.INCIDENT_ID}/detections`);
      meta = d.metadata; ann = d.annotations; wall = d.camera_wall;
      prov = clipProv(meta);
      annP = ann.status === 'sample' ? 'simulated' : (BADGE[ann.provenance] ? ann.provenance : 'pending');
      fps = typeof meta.frame_rate === 'number' && meta.frame_rate > 0 ? meta.frame_rate : null;
      times = d.frame_times && d.frame_times.frame_start_s && d.frame_times.frame_start_s.length ? d.frame_times.frame_start_s : null;
      if (times && !fps) fps = times.length / d.frame_times.end_s;
      const frames = usableAnnotations(d);
      frames.forEach((a) => byFrame.set(a.frame, a));

      $('clip-badge').innerHTML = wall ? badge(wall.status === 'sample' ? 'simulated' : wall.provenance) : '';
      const origOk = d.clip_available && d.original_available && meta.plume_rendering === 'burned_in';
      // Normal operations (no alert yet) show CAM-01's pre-leak view; without that clip, go straight to the event.
      const before = !d.triggered && d.before_available;
      renderWall(d.clip_available, origOk, before ? 'before' : 'event');
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
