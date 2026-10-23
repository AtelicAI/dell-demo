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

  // Only a verified record carries its stated provenance; a sample is Simulated; anything else is Pending.
  function clipProv(m) {
    if (m.status === 'verified' && BADGE[m.provenance]) return m.provenance;
    return m.status === 'sample' ? 'simulated' : 'pending';
  }
  const badge = (p) => `<span class="badge ${p}">${BADGE[p] || 'Pending'}</span>`;

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
    const used = [...new Set([prov, observed && annP, observed && 'model_estimated'].filter((p) => p && p !== 'pending'))];
    $('meta-badge').innerHTML = (used.length ? used : ['pending']).map(badge).join('');

    $('meta-body').innerHTML = `
      <div id="status-block"></div>
      <div class="origin-line"><b>OGI footage: METEC controlled release</b>${origin}</div>
      <div class="kv">
        <div class="k">Plume region</div><div class="v na" id="v-region"><span>${observed ? '—' : 'Not available'}</span></div>
        ${observed ? row('Heat map', 'Pixel change, computed live', 'model_estimated') : ''}
        ${row('Source', meta.ogi_clip_id ? `${meta.origin || 'METEC'} · ${meta.ogi_clip_id}${meta.source_segment ? ` (${meta.source_segment.start}–${meta.source_segment.end})` : ''}` : null, prov)}
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
      <div class="big"><span class="sig"></span>${now ? 'Plume observed' : 'Detection not confirmed'}</div>
      <div class="by">${by}</div>`;
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
  // CAM-01 is the annotated replay; the others loop a recorded METEC segment picked as plume-free.
  let wall = null;
  const API = () => window.AtelicShell.API;

  function renderWall(clipOk) {
    const cams = wall ? wall.cameras : [{ camera_id: 'CAM-01', area: '', watches: [], feed: 'ogi_clip' }];
    const wallP = wall ? (wall.status === 'sample' ? 'simulated' : wall.provenance) : null;
    $('wall').innerHTML = cams.map((c) => {
      const live = c.feed === 'ogi_clip';
      const loop = c.feed && typeof c.feed === 'object' && c.feed.file;
      const tip = live ? `${c.camera_id} · watches ${c.watches.join(', ')} · METEC clip ${meta.ogi_clip_id}${meta.source_segment ? ` ${meta.source_segment.start}–${meta.source_segment.end}` : ''}`
        : loop ? `${c.camera_id} · watches ${c.watches.join(', ')} · recorded METEC clip ${c.feed.ogi_clip_id} ${c.feed.segment.start}–${c.feed.segment.end}, looped · ${c.feed.picked_by}`
        : `${c.camera_id} · watches ${c.watches.join(', ')} · no feed in this demo`;
      const screen = live
        ? (clipOk ? `<div class="frame" id="frame"><video id="video" crossorigin="anonymous" muted autoplay loop playsinline preload="auto" src="${API()}/ogi/clip"></video><canvas class="heat" id="heat" aria-hidden="true"></canvas><svg class="overlay" id="overlay" preserveAspectRatio="xMidYMin slice" aria-hidden="true"></svg><span class="tcx"><i></i><span>--:--</span></span></div>`
          : `<div class="no-feed"><b>Approved METEC clip not loaded</b><span>Licence status: ${esc(meta.license_status || 'unknown')}</span></div>`)
        : loop ? `<div class="frame"><video muted autoplay loop playsinline preload="auto" src="${API()}/ogi/cams/${encodeURIComponent(c.camera_id)}"></video><span class="tcx"><i></i><span>--:--</span></span></div>`
        : '<div class="no-feed"><b>No feed in this demo</b></div>';
      return `<div class="cell${live ? ' cam1' : ''}" title="${esc(tip)}">
        <div class="cv">${screen}</div>
        <div class="cl"><b>${esc(c.camera_id)}</b><span class="ar">${esc(c.area)}</span>
          <span class="st${loop || live ? ' on' : ''}"><i></i><span class="sv">${loop || live ? 'Watching' : 'No feed'}</span></span>
          ${badge(live ? prov : wallP)}</div>
      </div>`;
    }).join('');
    // Running clock on each feed: shows the footage is moving even when the scene is still.
    $('wall').querySelectorAll('.frame').forEach((fr) => {
      const v = fr.querySelector('video'), out = fr.querySelector('.tcx span');
      v.addEventListener('timeupdate', () => { out.textContent = clock(v.currentTime); });
    });
    $('wall-foot').textContent = 'Recorded replays, looped. CAM-02 to CAM-04 are METEC clips picked as plume-free. Not live feeds from this skid.';
  }

  const clock = (t) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

  function setAlert(now) {
    const c = document.querySelector('.cell.cam1');
    if (!c) return;
    c.classList.toggle('alert', !!now);
    c.querySelector('.sv').textContent = now ? 'Plume observed' : 'Watching';
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
      heatStep = window.PlumeHeat.create(video, $('heat'));
      window.PlumeHeat.eachFrame(video, update);
      update(video.currentTime);
    }, { once: true });
    video.addEventListener('error', () => {
      $('frame').outerHTML = '<div class="no-feed"><b>Recording could not be played</b><span>Check data/ogi/source_clip.mp4 (H.264 MP4)</span></div>';
    }, { once: true });
  }

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
      renderWall(d.clip_available);
      renderMeta(d, frames);
      renderStatus(false);
      if (frames.length) window.AtelicShell.setReplayStatus('detect', 'not_started');
      if (d.clip_available) wireVideo(frames);
    } catch (e) {
      console.error(e);
      $('meta-badge').innerHTML = badge('pending');
      $('meta-body').innerHTML = '<div class="status">Detection details are unavailable: backend is not reachable.</div>';
      $('wall').innerHTML = '<div class="no-feed wide"><b>Cameras unavailable</b><span>Backend is not reachable</span></div>';
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
