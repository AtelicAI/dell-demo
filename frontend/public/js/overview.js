// Screen 1 — Overview / Observe. Reads GET /overview/plume; badges come from the record's status.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const MISSING = 'Verified satellite observation details are unavailable. You can still explore the local investigation example.';

  // status → provenance. Only a verified record may carry "Provider reported".
  function provenanceOf(rec) {
    if (rec.status === 'verified' && rec.provenance === 'provider_reported') return ['provider_reported', 'Provider reported', 'var(--accent)'];
    if (rec.status === 'sample') return ['simulated', 'Simulated', 'var(--warn)'];
    return ['pending', 'Pending', 'var(--ink-dim)'];
  }

  const fmtTime = (iso) => iso ? iso.replace('T', ' ').replace(/:\d\dZ$/, ' UTC') : null;
  const fmtCoord = (c) => (c && c.lat != null && c.lon != null)
    ? `${Math.abs(c.lat).toFixed(4)}°${c.lat >= 0 ? 'N' : 'S'}, ${Math.abs(c.lon).toFixed(4)}°${c.lon >= 0 ? 'E' : 'W'}`
    : null;

  function renderDetails(rec) {
    const [cls, label, color] = provenanceOf(rec);
    // One badge in the header covers the record; every value carries a matching dot.
    $('src-badge').innerHTML = `<span class="badge ${cls}" title="Provenance of every value in this card">${label}</span>`;
    // Optional `sub` puts a second, smaller line under the value (time under date, lon under lat).
    const fact = (k, v, sub) => `<div class="fact"><div class="lbl">${k}</div>${v == null
      ? '<div class="val na">Not available</div>'
      : `<div class="val">${esc(v)}${sub ? `<small>${esc(sub)}</small>` : ''}</div>`}</div>`;
    const [obsDate, obsTime] = (fmtTime(rec.observed_at) || '').split(' ');
    const coord = fmtCoord(rec.coordinates);
    const [latTxt, lonTxt] = coord ? coord.split(', ') : [];

    const flag = rec.status === 'sample'
      ? '<div class="sample-flag"><b>Sample values</b> · not a real Carbon Mapper record</div>'
      : '';

    let rate = '';
    if (rec.rate_kg_ch4_h != null) {
      const r = rec.rate_kg_ch4_h, u = rec.uncertainty_kg_ch4_h;
      let bar = '';
      if (u != null) {
        const max = Math.ceil((r + u) * 1.2 / 50) * 50, pct = (v) => `${Math.max(0, v) / max * 100}%`;
        bar = `<div class="unc-bar">
            <div class="band" style="left:${pct(r - u)};width:${(2 * u) / max * 100}%;background:${color}"></div>
            <div class="mid" style="left:${pct(r)};background:${color}"></div>
          </div>
          <div class="unc-ends"><span>0</span><span>${Math.round(max)}</span></div>`;
      }
      rate = `<div class="rate">
          <div class="lbl">Provider-reported rate (kg CH₄/h)</div>
          <div class="num"><b>${esc(r)}</b><span>kg CH₄/h</span></div>
          <div class="unc">Uncertainty ${u != null ? `± ${esc(u)} kg CH₄/h · range ${r - u}–${r + u}` : 'not reported'}</div>
          ${bar}
        </div>`;
    } else {
      rate = `<div class="rate"><div class="lbl">Provider-reported rate (kg CH₄/h)</div><div class="unc">Not available</div></div>`;
    }

    const source = rec.source_url
      ? `<a class="r" href="${esc(rec.source_url)}" target="_blank" rel="noopener">Open on Carbon Mapper ↗</a>`
      : '<span class="r na">Not available</span>';

    $('src-body').innerHTML = `${flag}${rate}
      <div class="facts">
        ${fact('Observed', obsDate || null, obsTime && `${obsTime} UTC`)}
        ${fact('Instrument', rec.instrument)}
        ${fact('Coordinates', latTxt || null, lonTxt)}
        ${fact('Observation ID', rec.satellite_observation_id)}
      </div>
      <div class="source-line">Source record ${source}</div>`;
  }

  function renderMissing() {
    $('src-badge').innerHTML = '<span class="badge pending">Pending</span>';
    $('src-body').innerHTML = `<div class="missing">${MISSING}</div>`;
    $('map').innerHTML = '<div class="map-empty">No verified observation to display.</div>';
  }

  // Schematic map drawn at the container's real pixel size, so it never crops or stretches.
  // Equal-distance lat/lon grid around the observation; no basemap is licensed yet.
  let selected = false;

  function drawMap(rec) {
    const box = $('map');
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !H) return;
    const { lat, lon } = rec.coordinates;
    const cosLat = Math.cos(lat * Math.PI / 180);
    const pxLon = W / 1.2;                 // 1.2° of longitude across the box
    const pxLat = pxLon / cosLat;          // same km scale on both axes
    const x = (lo) => W / 2 + (lo - lon) * pxLon;
    const y = (la) => H / 2 - (la - lat) * pxLat;

    const step = 0.1, eps = 1e-6;
    const isMajor = (v) => Math.abs(v * 2 - Math.round(v * 2)) < eps;
    let grid = '', ticks = '';
    for (let lo = Math.ceil((lon - 0.6) / step) * step; lo <= lon + 0.6 + eps; lo += step) {
      grid += `<line class="${isMajor(lo) ? 'major' : ''}" x1="${x(lo)}" y1="0" x2="${x(lo)}" y2="${H}"/>`;
      if (x(lo) > 70 && x(lo) < W - 50) ticks += `<text class="tick" x="${x(lo) + 4}" y="14">${lo.toFixed(1)}°E</text>`;
    }
    const latSpan = H / 2 / pxLat;
    for (let la = Math.ceil((lat - latSpan) / step) * step; la <= lat + latSpan + eps; la += step) {
      grid += `<line class="${isMajor(la) ? 'major' : ''}" x1="0" y1="${y(la)}" x2="${W}" y2="${y(la)}"/>`;
      if (y(la) > 34 && y(la) < H - 44) ticks += `<text class="tick" x="6" y="${y(la) - 4}">${la.toFixed(1)}°N</text>`;
    }

    const cx = x(lon), cy = y(lat), k = W / 800;
    // Illustrative footprint: concentric rings elongated downwind (shape is not provider data).
    const contours = [[150, 46, .07], [110, 34, .1], [72, 24, .14], [38, 14, .2]]
      .map(([rx, ry, op]) => `<ellipse class="contour" cx="${cx + rx * k * .55}" cy="${cy}" rx="${rx * k}" ry="${ry * k}" fill-opacity="${op}" stroke-opacity="${op * 2.2}" transform="rotate(-18 ${cx} ${cy})"/>`)
      .join('');

    const km = (111.32 * cosLat * step).toFixed(1);
    const sx2 = W - 24, sx1 = sx2 - step * pxLon;

    box.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Schematic map of the observation location">
        <g class="grid">${grid}</g>
        ${ticks}
        <g class="footprint" id="footprint">${contours}</g>
        <g class="origin${selected ? ' selected' : ''}" id="origin" tabindex="0" role="button" aria-label="Select observation">
          <circle class="hit" cx="${cx}" cy="${cy}" r="26"/>
          <circle class="ring" cx="${cx}" cy="${cy}" r="14"/>
          <line class="cross" x1="${cx - 22}" y1="${cy}" x2="${cx - 9}" y2="${cy}"/><line class="cross" x1="${cx + 9}" y1="${cy}" x2="${cx + 22}" y2="${cy}"/>
          <line class="cross" x1="${cx}" y1="${cy - 22}" x2="${cx}" y2="${cy - 9}"/><line class="cross" x1="${cx}" y1="${cy + 9}" x2="${cx}" y2="${cy + 22}"/>
          <circle class="core" cx="${cx}" cy="${cy}" r="5"/>
          <text class="tag" x="${cx + 12}" y="${cy + 30}">${esc(rec.satellite_observation_id)}</text>
        </g>
        <g class="scale"><line x1="${sx1}" y1="${H - 22}" x2="${sx2}" y2="${H - 22}"/>
          <line x1="${sx1}" y1="${H - 27}" x2="${sx1}" y2="${H - 17}"/><line x1="${sx2}" y1="${H - 27}" x2="${sx2}" y2="${H - 17}"/>
          <text x="${sx1}" y="${H - 30}">≈ ${km} km</text></g>
      </svg>`;

    const origin = $('origin');
    const toggle = () => {
      selected = !selected;
      origin.classList.toggle('selected', selected);
      $('src-panel').classList.toggle('selected', selected);
    };
    origin.addEventListener('click', toggle);
    origin.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  }

  function renderMap(rec) {
    new ResizeObserver(() => drawMap(rec)).observe($('map'));
  }

  function wireModal() {
    const modal = $('modal');
    const open = () => { modal.classList.add('open'); $('modal-continue').focus(); };
    const close = () => { modal.classList.remove('open'); $('btn-explore').focus(); };
    $('btn-explore').addEventListener('click', open);
    $('modal-cancel').addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal.classList.contains('open')) close(); });
  }

  async function init() {
    wireModal();
    try {
      const rec = await window.AtelicShell.get('/overview/plume');
      const usable = rec.status !== 'pending' && rec.satellite_observation_id
        && rec.coordinates && rec.coordinates.lat != null && rec.coordinates.lon != null;
      if (!usable) return renderMissing();
      renderDetails(rec);
      renderMap(rec);
    } catch (e) {
      console.error(e);
      renderMissing();
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
