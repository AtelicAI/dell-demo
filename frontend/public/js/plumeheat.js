// Plume heat map, shared by Camera and Locate. Computed in the browser from the clip's own pixels:
// how far each pixel departs from a slowly updated background of the scene. Drawn only inside the
// hand-drawn plume region, and only when the caller passes one, so nothing is invented.
// The video must be same-origin or loaded with crossorigin="anonymous".
(function () {
  const HEAT = { bgRate: 0.02, keep: 0.85, lo: 1.5, hi: 8, edge: 10 };

  function create(video, canvas) {
    let hctx = null, sctx = null, hout = null, bg = null, heat = null, W = 0, H = 0;
    // Call once per presented frame; box is [x, y, w, h] normalized, or null to draw nothing.
    return function step(box) {
      W = video.videoWidth; H = video.videoHeight;
      if (!W) return;
      if (!sctx) {
        canvas.width = W; canvas.height = H; hctx = canvas.getContext('2d');
        const s = document.createElement('canvas'); s.width = W; s.height = H;
        sctx = s.getContext('2d', { willReadFrequently: true });
        hout = hctx.createImageData(W, H);
      }
      let px;
      try { sctx.drawImage(video, 0, 0, W, H); px = sctx.getImageData(0, 0, W, H).data; } catch (e) { return; }
      if (!bg) { bg = new Float32Array(W * H); heat = new Float32Array(W * H); for (let i = 0; i < W * H; i++) bg[i] = (px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2]) / 3; }
      for (let i = 0; i < W * H; i++) {
        const g = (px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2]) / 3;
        heat[i] = heat[i] * HEAT.keep + Math.abs(g - bg[i]) * (1 - HEAT.keep);
        bg[i] += (g - bg[i]) * HEAT.bgRate;
      }
      const o = hout.data;
      o.fill(0);
      if (box) {
        const x0 = Math.round(box[0] * W), y0 = Math.round(box[1] * H), x1 = Math.round((box[0] + box[2]) * W), y1 = Math.round((box[1] + box[3]) * H);
        for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
          const i = y * W + x;
          let t = (heat[i] - HEAT.lo) / (HEAT.hi - HEAT.lo);
          if (t <= 0) continue;
          t = Math.min(1, t) * Math.min(1, (Math.min(x - x0, x1 - 1 - x, y - y0, y1 - 1 - y) + 1) / HEAT.edge);
          // yellow → orange → red, more opaque where hotter
          o[i * 4] = 255; o[i * 4 + 1] = Math.round(210 - 170 * t); o[i * 4 + 2] = Math.round(40 * (1 - t));
          o[i * 4 + 3] = Math.round(255 * Math.min(1, 0.25 + t) * Math.min(1, t * 3));
        }
      }
      hctx.putImageData(hout, 0, 0);
    };
  }

  // Runs fn(mediaTime) once per presented video frame.
  function eachFrame(video, fn) {
    if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
      const cb = (_, m) => { fn(m.mediaTime); video.requestVideoFrameCallback(cb); };
      video.requestVideoFrameCallback(cb);
    } else {
      const loop = () => { if (!video.paused) fn(video.currentTime); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }
  }

  window.PlumeHeat = { create, eachFrame };
})();
