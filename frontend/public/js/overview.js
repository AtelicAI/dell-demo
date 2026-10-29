// Screen 1 — Overview / Observe. Reads GET /overview/plume; badges come from the record's status.
// Map: Leaflet over Esri World Imagery, with the Carbon Mapper plume image cached by
// scripts/fetch_carbon_mapper.py and, optionally, the provider's observation-day scene tiles.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const MISSING = 'Verified satellite observation details are unavailable. You can still explore the local investigation example.';
  const CM_TILES = 'https://api.carbonmapper.org/api/v1/layers/scene';
  const BASEMAP = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  const BASEMAP_ATTR = 'Imagery © Esri, Maxar, Earthstar Geographics';

  // status → provenance. Only a verified record may carry "Provider reported".
  function provenanceOf(rec) {
    if (rec.status === 'verified' && rec.provenance === 'provider_reported') return ['provider_reported', 'Provider reported', 'var(--accent)'];
    return ['pending', 'Pending', 'var(--ink-dim)'];
  }

  const GAS = { CH4: 'CH₄', CO2: 'CO₂' };
  const gasOf = (rec) => GAS[rec.gas] || esc(rec.gas || 'CH₄');
  const compact = (v) => new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v);
  const fmtTime = (iso) => iso ? iso.replace('T', ' ').replace(/:\d\d(\.\d+)?Z$/, ' UTC') : null;
  const fmtCoord = (c) => (c && c.lat != null && c.lon != null)
    ? `${Math.abs(c.lat).toFixed(4)}°${c.lat >= 0 ? 'N' : 'S'}, ${Math.abs(c.lon).toFixed(4)}°${c.lon >= 0 ? 'E' : 'W'}`
    : null;

  // Rate hero with uncertainty bar. `sub` explains which rate this is.
  function rateBlock(label, r, u, unit, color, sub) {
    if (r == null) return `<div class="rate"><div class="lbl">${label}</div><div class="unc">Not yet quantified by the provider</div></div>`;
    let bar = '';
    if (u != null) {
      const max = (r + u) * 1.25, pct = (v) => `${Math.max(0, v) / max * 100}%`;
      bar = `<div class="unc-bar">
          <div class="band" style="left:${pct(r - u)};width:${(2 * u) / max * 100}%;background:${color}"></div>
          <div class="mid" style="left:${pct(r)};background:${color}"></div>
        </div>
        <div class="unc-ends"><span>0</span><span>${compact(max)}</span></div>`;
    }
    return `<div class="rate">
        <div class="lbl">${label}</div>
        <div class="num"><b>${compact(r)}</b><span>${u != null ? `± ${compact(u)} ` : ''}${unit}</span></div>
        ${sub ? `<div class="sub">${sub}</div>` : ''}
        <div class="unc">Uncertainty ${u != null ? `range ${compact(r - u)}–${compact(r + u)}` : 'not reported'}</div>
        ${bar}
      </div>`;
  }

  // Provider rate history for the source: points with ±uncertainty whiskers, this pass marked.
  function historyBlock(rec, width) {
    const h = (rec.source && rec.source.history) || [];
    if (h.length < 2) return '';
    const W = Math.max(240, width - 36), H = 64, L = 40, R = 6, T = 6, B = 16;
    const ts = h.map((p) => Date.parse(p.observed_at));
    const obs = Date.parse(rec.observed_at);
    const t0 = Math.min(...ts), t1 = Math.max(...ts, obs);
    const vmax = Math.max(...h.map((p) => p.rate_kg_h + (p.uncertainty_kg_h || 0)));
    const x = (t) => L + (t - t0) / (t1 - t0 || 1) * (W - L - R);
    const y = (v) => T + (1 - v / vmax) * (H - T - B);
    const pts = h.map((p, i) => [x(ts[i]), y(p.rate_kg_h), p]);
    const whisk = pts.map(([px, , p]) => p.uncertainty_kg_h != null
      ? `<line class="wk" x1="${px}" x2="${px}" y1="${y(Math.min(vmax, p.rate_kg_h + p.uncertainty_kg_h))}" y2="${y(Math.max(0, p.rate_kg_h - p.uncertainty_kg_h))}"/>` : '').join('');
    const years = [];
    for (let yr = new Date(t0).getUTCFullYear() + 1; Date.UTC(yr, 0) <= t1; yr++) years.push(yr);
    const yTicks = [0, vmax].map((v) => `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 4}" y="${y(v) + 3}" text-anchor="end">${compact(v)}</text>`).join('');
    const xTicks = years.map((yr) => `<text class="ax" x="${x(Date.UTC(yr, 0))}" y="${H - 3}" text-anchor="middle">${yr}</text>`).join('');
    const quantified = h.some((p) => p.plume_id === rec.satellite_observation_id);
    const now = `<line class="now" x1="${x(obs)}" x2="${x(obs)}" y1="${T}" y2="${H - B}"/>`;
    return `<div class="hist">
        <div class="lbl">Source rate history (kg ${gasOf(rec)}/h)<span>${h.length} quantified plumes</span></div>
        <svg viewBox="0 0 ${W} ${H}" style="height:${H}px" role="img" aria-label="Provider-reported emission rates for this source over time">
          ${yTicks}${xTicks}${now}${whisk}
          <polyline class="ln" points="${pts.map(([px, py]) => `${px},${py}`).join(' ')}"/>
          ${pts.map(([px, py, p]) => `<circle class="pt" cx="${px}" cy="${py}" r="2.2"><title>${esc(p.observed_at.slice(0, 10))}: ${compact(p.rate_kg_h)} kg/h</title></circle>`).join('')}
        </svg>
        ${quantified ? '' : `<div class="note">Dashed line: this pass (${esc(rec.observed_at.slice(0, 10))}), not yet quantified</div>`}
      </div>`;
  }

  function renderDetails(rec) {
    const [cls, label, color] = provenanceOf(rec);
    // One badge in the header covers the record.
    $('src-badge').innerHTML = `<span class="badge ${cls}" title="Provenance of every value in this card">${label}</span>`;
    // Optional `sub` puts a second, smaller line under the value (time under date, lon under lat).
    const fact = (k, v, sub) => `<div class="fact"><div class="lbl">${k}</div>${v == null
      ? '<div class="val na">Not available</div>'
      : `<div class="val">${esc(v)}${sub ? `<small>${esc(sub)}</small>` : ''}</div>`}</div>`;
    const [obsDate, obsTime] = (fmtTime(rec.observed_at) || '').split(' ');
    const coord = fmtCoord(rec.coordinates);
    const [latTxt, lonTxt] = coord ? coord.split(', ') : [];
    const gas = gasOf(rec), unit = `kg ${gas}/h`;
    const src = rec.source || {};

    let flag = '';
    if (rec.gas && rec.gas !== 'CH4') flag = `<div class="gas-flag"><b>${gas} plume</b> · Carbon Mapper classifies this observation as ${gas}, not methane.</div>`;

    // This plume's own rate when published; otherwise the provider's source-level rate, labelled as such.
    let rate;
    if (rec.rate_kg_h != null || src.rate_kg_h == null) {
      rate = rateBlock(`Provider-reported rate (${unit})`, rec.rate_kg_h, rec.uncertainty_kg_h, unit, color);
    } else {
      const pers = src.persistence != null ? ` · ${Math.round(src.persistence * 100)}% persistence` : '';
      rate = rateBlock(`Provider-reported source rate (${unit})`, src.rate_kg_h, src.uncertainty_kg_h, unit, color,
        `Average across ${src.plumes ?? '—'} plumes${pers}. This plume is not yet quantified.`);
    }

    const source = rec.source_url
      ? `<a class="r" href="${esc(rec.source_url)}" target="_blank" rel="noopener">Open on Carbon Mapper ↗</a>`
      : '<span class="r na">Not available</span>';

    // Regional context only: a public detection near the (anonymous) demo site, never its history.
    const context = `<div class="ctx-note"><b>Satellites catch super-emitters; the 0.28 kg/h leak in this investigation needs on-site cameras and sensors.</b>
      This is regional context, not this site: a public ${esc((rec.sector_label || 'oil and gas').toLowerCase())} methane detection in ${esc(window.AtelicShell.FACILITY.location)},
      thousands of kg/h, seen by ${esc(rec.instrument || 'Tanager-1')}.</div>`;
    $('src-body').innerHTML = `${flag}${context}${rate}${historyBlock(rec, $('src-body').clientWidth)}
      <div class="facts">
        ${fact('Observed', obsDate || null, obsTime && `${obsTime} UTC`)}
        ${fact('Instrument', rec.instrument, rec.gsd_m != null ? `${Math.round(rec.gsd_m)} m pixels` : null)}
        ${fact('Coordinates', latTxt || null, lonTxt)}
        ${fact('Sector', rec.sector ? `IPCC ${rec.sector}` : null, rec.sector_label)}
        ${fact('Observation ID', rec.satellite_observation_id)}
        ${fact('Source', src.overpasses != null ? `${src.detections} of ${src.overpasses} passes` : null, src.overpasses != null ? 'days with a detection' : null)}
      </div>
      <div class="source-line">Source record ${source}</div>`;
  }

  function renderMissing(msg) {
    $('src-badge').innerHTML = '<span class="badge pending">Pending</span>';
    $('src-body').innerHTML = `<div class="missing">${MISSING}</div>`;
    $('map-empty').textContent = msg || 'No verified observation to display.';
    ['toggle-footprint', 'toggle-scene', 'btn-recenter'].forEach((id) => { $(id).disabled = true; });
  }

  function renderMap(rec) {
    if (!window.L) return renderMissing('Map library failed to load.');
    $('map-empty').remove();
    const { lat, lon } = rec.coordinates;

    const map = L.map('map', { zoomSnap: 0.25, attributionControl: true }).setView([lat, lon], 14);
    map.attributionControl.setPrefix(false);
    L.tileLayer(BASEMAP, { maxZoom: 19, className: 'basemap', attribution: BASEMAP_ATTR }).addTo(map);

    // Observation-day true colour from the same Tanager pass. Off by default: coarse (~34 m) pixels.
    const scene = rec.scene_id ? L.tileLayer(`${CM_TILES}/${encodeURIComponent(rec.scene_id)}/rgb/{z}/{x}/{y}.png`,
      { maxNativeZoom: 15, maxZoom: 19, opacity: 0.9, attribution: 'Scene © Carbon Mapper / Planet' }) : null;
    if (!scene) $('toggle-scene').disabled = true;

    let plume = null, focus = null;
    if (rec.plume_bounds && rec.plume_image) {
      const [w, s, e, n] = rec.plume_bounds;
      focus = L.latLngBounds([[s, w], [n, e]]);
      plume = L.imageOverlay(`${window.AtelicShell.API}/overview/plume/image`, focus,
        { opacity: 0.85, className: 'plume-img', attribution: 'Plume © Carbon Mapper' }).addTo(map);
      map.fitBounds(focus.extend([lat, lon]), { padding: [40, 40], maxZoom: 15 });
      const legend = L.control({ position: 'bottomleft' });
      legend.onAdd = () => {
        const d = L.DomUtil.create('div', 'legend');
        d.innerHTML = `<div class="t">${gasOf(rec)} enhancement</div><div class="bar"></div><div class="ends"><span>Low</span><span>High</span></div>`;
        return d;
      };
      legend.addTo(map);
    } else {
      $('toggle-footprint').disabled = true;
    }

    let selected = false;
    const pinIcon = () => L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div class="origin-pin${selected ? ' selected' : ''}" style="width:30px;height:30px"><span class="ring"></span><span class="core"></span><span class="dot"></span></div>` });
    const pin = L.marker([lat, lon], { icon: pinIcon(), keyboard: true, title: 'Select observation', zIndexOffset: 1000 })
      .bindTooltip(esc(rec.satellite_observation_id), { permanent: true, direction: 'bottom', offset: [0, 14], className: 'origin-tag' })
      .addTo(map);
    pin.on('click', () => {
      selected = !selected;
      pin.setIcon(pinIcon());
      $('src-panel').classList.toggle('selected', selected);
    });

    $('toggle-footprint').addEventListener('change', (e) => {
      if (plume) e.target.checked ? plume.addTo(map) : plume.remove();
    });
    $('toggle-scene').addEventListener('change', (e) => {
      if (!scene) return;
      e.target.checked ? scene.addTo(map) : scene.remove();
      if (plume && map.hasLayer(plume)) plume.bringToFront();
    });
    $('btn-recenter').addEventListener('click', () => {
      focus ? map.flyToBounds(focus.extend([lat, lon]), { padding: [40, 40], maxZoom: 15, duration: 0.6 }) : map.flyTo([lat, lon], 14);
    });
    // The panel is sized by CSS grid after first paint; keep tiles in step with it.
    new ResizeObserver(() => map.invalidateSize()).observe($('map'));
  }

  // "Explore on-site investigation": the local incident is a separate example, so say so before leaving.
  function wireModal() {
    $('btn-explore').addEventListener('click', () => window.AtelicShell.modal({
      title: 'Separate local example',
      html: `<p>This local investigation follows a camera alert at a ${window.AtelicShell.FACILITY.name.toLowerCase()} in ${window.AtelicShell.FACILITY.location}. It is not the satellite-observed facility.</p>`,
      cancelLabel: 'Stay on overview', okLabel: 'Continue to camera observation', okHref: 'detection.html',
    }));
  }

  async function init() {
    // Site incidents at a glance, linking to the Incidents page.
    window.AtelicShell.get('/incidents').then((d) => {
      const open = d.live.filter((l) => l.triggered).length, closed = d.history ? d.history.incidents.length : 0;
      const el = document.getElementById('inc-count');
      el.textContent = `${open} open · ${closed} closed incidents →`;
      el.hidden = false;
    }, () => {});
    wireModal();
    try {
      const rec = await window.AtelicShell.get('/overview/plume');
      const usable = rec.status !== 'pending' && rec.satellite_observation_id
        && rec.coordinates && rec.coordinates.lat != null && rec.coordinates.lon != null;
      if (!usable) return renderMissing();
      // PRD title assumes methane; don't call a CO₂ plume a methane observation.
      if (rec.gas && rec.gas !== 'CH4') {
        $('page-title').textContent = 'Satellite emissions observations';
        document.title = 'Satellite emissions observations — Atelic';
      }
      renderDetails(rec);
      renderMap(rec);
    } catch (e) {
      console.error(e);
      renderMissing();
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
