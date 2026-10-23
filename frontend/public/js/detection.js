// Screen 2 — Detect / Camera. Reads GET /incidents/:id/detections and plays GET /ogi/clip.
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
  const layers = { mask: true, bbox: true, origin: true };

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
    const used = [...new Set([prov, observed && annP].filter((p) => p && p !== 'pending'))];
    $('meta-badge').innerHTML = (used.length ? used : ['pending']).map(badge).join('');

    $('meta-body').innerHTML = `
      <div id="status-block"></div>
      <div class="origin-line"><b>OGI footage: METEC controlled release</b>${origin}</div>
      <div class="kv">
        <div class="k">Plume region</div><div class="v na" id="v-region"><span>${observed ? '—' : 'Not available'}</span></div>
        <div class="k">Confidence (if supplied)</div><div class="v na" id="v-conf"><span>Not supplied</span></div>
        ${row('Recording time', meta.recording_date, prov)}
        ${row('Source', meta.ogi_clip_id ? `${meta.origin || 'METEC'} · ${meta.ogi_clip_id}${meta.source_segment ? ` (${meta.source_segment.start}–${meta.source_segment.end})` : ''}` : null, prov)}
        ${row('Camera type', meta.camera_type, prov)}
        ${row('Resolution', meta.resolution, prov)}
        ${row('Frame rate', meta.frame_rate != null ? `${meta.frame_rate} fps${meta.variable_frame_rate ? ' (variable)' : ''}` : null, prov)}
        ${row('Detection method', MODES[meta.detection_mode] || null, prov)}
        ${row('Model/version', meta.model_version, prov)}
        ${row('Licence', meta.license_status, meta.license_status === 'verified' ? prov : 'pending')}
      </div>`;
  }

  // Status at the current replay moment: observed only once the replay reaches the first
  // annotated frame. Before that the detection has not happened yet in this replay.
  let noDetectionNote = '';
  function renderStatus(now) {
    const el = $('status-block');
    if (!el) return;
    let by;
    if (now) {
      by = annP === 'manual_annotation'
        ? `<span class="drawn"><svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8.5 1.5l2 2-6.5 6.5H2V8z"/></svg>Manually drawn</span>${ann.reviewed_by ? ` · reviewed by ${esc(ann.reviewed_by)}` : ''}`
        : (meta.model_version ? `Output of ${esc(meta.model_version)}` : 'Model output');
    } else if (firstFrame != null) {
      by = `Plume first marked at ${fmt(times ? times[firstFrame] : firstFrame / fps)} in this recording.`;
    } else {
      by = noDetectionNote;
    }
    el.className = `status ${now ? 'observed' : 'unconfirmed'}`;
    el.innerHTML = `<div class="lbl">Status</div>
      <div class="big"><span class="sig"></span>${now ? 'Plume observed' : 'Detection not confirmed'}</div>
      <div class="by">${by}</div>`;
  }

  function applyReplay(f) {
    if (firstFrame == null) return;
    const now = f >= firstFrame;
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

  // ----- player -----
  const fmt = (t) => {
    if (!isFinite(t)) return '--:--.--';
    const m = Math.floor(t / 60), s = t - m * 60;
    return `${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`;
  };
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
      if (layers.mask && poly.length > 2) g += `<polygon class="mask" points="${poly.map(([x, y]) => `${x * W},${y * H}`).join(' ')}"/>`;
      if (layers.bbox && a.bbox_normalized) {
        const [x, y, w, h] = a.bbox_normalized;
        g += `<rect class="bbox" x="${x * W}" y="${y * H}" width="${w * W}" height="${h * H}"/>`;
      }
      if (layers.origin && a.origin_normalized) {
        const [x, y] = [a.origin_normalized[0] * W, a.origin_normalized[1] * H], r = W * 0.012;
        g += `<g class="origin"><circle cx="${x}" cy="${y}" r="${r}"/><line x1="${x - r * 2.2}" y1="${y}" x2="${x - r}" y2="${y}"/><line x1="${x + r}" y1="${y}" x2="${x + r * 2.2}" y2="${y}"/><line x1="${x}" y1="${y - r * 2.2}" x2="${x}" y2="${y - r}"/><line x1="${x}" y1="${y + r}" x2="${x}" y2="${y + r * 2.2}"/></g>`;
      }
    }
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = g;
    $('hud-ann').hidden = !a;
    return a;
  }

  function update(t) {
    const dur = video.duration;
    const pct = isFinite(dur) && dur > 0 ? (t / dur) * 100 : 0;
    $('played').style.width = `${pct}%`;
    $('head').style.left = `${pct}%`;
    $('track').setAttribute('aria-valuenow', t.toFixed(2));
    $('track').setAttribute('aria-valuetext', fmt(t));
    if (fps) {
      const f = frameAt(t);
      const label = `frame ${String(f).padStart(String(total).length, '0')} / ${total - 1}`;
      $('timecode').innerHTML = `${fmt(t)} <small>${label}</small>`;
      $('hud-tc').textContent = `${fmt(t)} · F${f}`;
      if (f !== frame) {
        frame = f;
        applyReplay(f);
        renderFrameFields(drawOverlay(f));
      }
    } else {
      $('timecode').innerHTML = `${fmt(t)} <small>frame rate unknown</small>`;
      $('hud-tc').textContent = fmt(t);
    }
  }

  // Frame-accurate updates: requestVideoFrameCallback reports the presented frame's media time.
  function watchFrames() {
    if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
      const cb = (_, m) => { update(m.mediaTime); video.requestVideoFrameCallback(cb); };
      video.requestVideoFrameCallback(cb);
    } else {
      const loop = () => { if (!video.paused) update(video.currentTime); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }
    video.addEventListener('seeked', () => update(video.currentTime));
  }

  // Seek to the middle of a frame so rounding never lands on its neighbour.
  function seekFrame(f) {
    if (!fps) return;
    video.pause();
    f = Math.min(total - 1, Math.max(0, f));
    video.currentTime = times ? (times[f] + (times[f + 1] ?? video.duration)) / 2 : (f + 0.5) / fps;
  }
  function seekFraction(x) {
    const t = Math.min(1, Math.max(0, x)) * video.duration;
    if (fps) seekFrame(frameAt(t)); else video.currentTime = t;
  }
  function togglePlay() { video.paused ? video.play() : video.pause(); }

  function drawSegments(frames) {
    if (!fps || !total) return;
    const idx = frames.map((a) => a.frame).filter(Number.isInteger).sort((a, b) => a - b);
    const segs = [];
    for (const f of idx) {
      const last = segs[segs.length - 1];
      if (last && f === last[1] + 1) last[1] = f; else segs.push([f, f]);
    }
    $('segs').innerHTML = segs.map(([a, b]) =>
      `<div class="seg" style="left:${a / total * 100}%;width:${(b - a + 1) / total * 100}%" title="Frames ${a}–${b}"></div>`).join('');
  }

  function wirePlayer(frames) {
    $('btn-play').disabled = false;
    $('btn-prev').disabled = $('btn-next').disabled = !fps;
    if (!fps) $('tl-note').textContent = 'Frame stepping needs frame_rate in the clip metadata.';
    // A layer button is only usable if some annotation actually has that layer.
    const has = {
      mask: frames.some((a) => (a.mask_polygon_normalized || []).length > 2),
      bbox: frames.some((a) => a.bbox_normalized),
      origin: frames.some((a) => a.origin_normalized),
    };
    document.querySelectorAll('.tg').forEach((b) => {
      b.disabled = !has[b.dataset.k] || !fps;
      if (b.disabled) b.setAttribute('aria-pressed', 'false');
    });

    const track = $('track');
    track.setAttribute('aria-disabled', 'false');
    track.setAttribute('aria-valuemax', video.duration.toFixed(2));
    $('tl-dur').textContent = `${fmt(video.duration)}${fps ? ` · ${total} frames` : ''}`;
    drawSegments(frames);

    const setIcon = () => {
      // toggleAttribute, not .hidden: SVG elements have no .hidden property in Safari.
      $('ic-play').toggleAttribute('hidden', !video.paused);
      $('ic-pause').toggleAttribute('hidden', video.paused);
      $('btn-play').setAttribute('aria-label', video.paused ? 'Play' : 'Pause');
    };
    video.addEventListener('play', setIcon);
    video.addEventListener('pause', setIcon);
    $('btn-play').addEventListener('click', togglePlay);
    $('btn-prev').addEventListener('click', () => seekFrame(frame - 1));
    $('btn-next').addEventListener('click', () => seekFrame(frame + 1));

    const pos = (e) => (e.clientX - track.getBoundingClientRect().left) / track.clientWidth;
    track.addEventListener('pointerdown', (e) => {
      track.setPointerCapture(e.pointerId);
      video.pause();
      seekFraction(pos(e));
    });
    track.addEventListener('pointermove', (e) => { if (track.hasPointerCapture(e.pointerId)) seekFraction(pos(e)); });

    document.querySelectorAll('.tg').forEach((b) => b.addEventListener('click', () => toggleLayer(b.dataset.k)));

    document.addEventListener('keydown', (e) => {
      const el = e.target instanceof Element ? e.target : document.body;
      if (el.closest('input, textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key;
      if (k === ' ' && !el.closest('button')) { e.preventDefault(); togglePlay(); }
      else if (k === 'ArrowLeft' && fps) { e.preventDefault(); seekFrame(frame - 1); }
      else if (k === 'ArrowRight' && fps) { e.preventDefault(); seekFrame(frame + 1); }
      else if (frames.length && fps && ({ m: 'mask', b: 'bbox', o: 'origin' })[k.toLowerCase()]) toggleLayer({ m: 'mask', b: 'bbox', o: 'origin' }[k.toLowerCase()]);
    });

    watchFrames();
    // Open on the observation that started the investigation: the first annotated frame.
    firstFrame = frames.length && fps ? Math.min(...frames.map((a) => a.frame)) : null;
    if (firstFrame != null) seekFrame(firstFrame); else update(0);
  }

  function toggleLayer(k) {
    layers[k] = !layers[k];
    document.querySelector(`.tg[data-k="${k}"]`).setAttribute('aria-pressed', String(layers[k]));
    drawOverlay(frame);
  }

  function stageMessage(title, sub) {
    $('stage-empty').innerHTML = `<b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ''}`;
  }

  function mountVideo(frames) {
    const fr = $('frame');
    fr.innerHTML = `
      <video id="video" preload="auto" playsinline muted src="${window.AtelicShell.API}/ogi/clip"></video>
      <svg class="overlay" id="overlay" aria-hidden="true"></svg>
      <div class="hud" aria-hidden="true">
        <span class="br tl"></span><span class="br tr"></span><span class="br bl"></span><span class="br brr"></span>
        <div class="row top"><span class="chip"><span class="rec"></span>CAM-01 · Recorded replay</span><span class="chip r" id="hud-tc">--:--.--</span></div>
        <div class="row bot"><span class="chip">OGI footage: METEC controlled release</span>
          <span class="chip r ann" id="hud-ann" hidden><i style="background:var(--${annP === 'manual_annotation' ? 'pink' : annP === 'model_estimated' ? 'violet' : 'warn'})"></i>Overlay · ${esc(BADGE[annP] || 'Pending')}</span></div>
      </div>
      <div class="stage-empty" id="stage-empty" hidden></div>`;
    video = $('video');
    video.addEventListener('loadedmetadata', () => {
      fr.style.setProperty('--ar', (video.videoWidth / video.videoHeight) || 1.7778);
      if (times) total = times.length;
      else if (fps) total = Math.max(1, Math.round(video.duration * fps));
      wirePlayer(frames);
    }, { once: true });
    video.addEventListener('error', () => {
      $('stage-empty').hidden = false;
      stageMessage('Recording could not be played', 'Check data/ogi/source_clip.mp4 (H.264 MP4)');
    }, { once: true });
  }

  async function init() {
    try {
      const d = await window.AtelicShell.get(`/incidents/${window.AtelicShell.INCIDENT_ID}/detections`);
      meta = d.metadata; ann = d.annotations;
      prov = clipProv(meta);
      annP = ann.status === 'sample' ? 'simulated' : (BADGE[ann.provenance] ? ann.provenance : 'pending');
      fps = typeof meta.frame_rate === 'number' && meta.frame_rate > 0 ? meta.frame_rate : null;
      times = d.frame_times && d.frame_times.frame_start_s && d.frame_times.frame_start_s.length ? d.frame_times.frame_start_s : null;
      if (times && !fps) fps = times.length / d.frame_times.end_s;
      const frames = usableAnnotations(d);
      frames.forEach((a) => byFrame.set(a.frame, a));

      $('clip-badge').innerHTML = badge(prov);
      $('clip-id').textContent = meta.ogi_clip_id || 'Clip ID pending';
      renderMeta(d, frames);
      renderStatus(frames.length > 0);

      if (d.clip_available) mountVideo(frames);
      else stageMessage('Approved METEC clip not loaded', `Licence status: ${meta.license_status || 'unknown'} · data/ogi/source_clip.mp4`);
    } catch (e) {
      console.error(e);
      $('meta-badge').innerHTML = badge('pending');
      $('meta-body').innerHTML = '<div class="status">Detection details are unavailable: backend is not reachable.</div>';
      stageMessage('Recording unavailable', 'Backend is not reachable');
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
