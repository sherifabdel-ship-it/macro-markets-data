import * as L from './lib.js';

const $ = (id) => document.getElementById(id);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
const fmt = (v) => (v === null || v === undefined ? '' : Math.abs(v) >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 1 }) : String(+v.toPrecision(5)));
const GROUPS = [['price', 'Market prices (daily)'], ['fred', 'US economy (FRED)'], ['wb', 'World Bank (annual, by country)']];

let manifest = null;
const byId = new Map();
const cache = new Map();          // id -> full series payload
const state = { sel: new Set(), from: '', to: '', freq: 'native', tf: {}, policy: 'common', basis: 'latest', dateBy: 'period', auto: true };
let last = null;                  // last computed result, used by the download buttons

// ---- state <-> URL -------------------------------------------------------------------------
function saveHash() {
  const p = new URLSearchParams({ s: [...state.sel].join(','), from: state.from, to: state.to, f: state.freq,
    t: [...state.sel].map((id) => state.tf[id] || 'level').join(','), p: state.policy, vb: state.basis, db: state.dateBy });
  history.replaceState(null, '', '#' + p.toString());
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (!p.get('s')) return false;
  const ids = p.get('s').split(',').filter((id) => byId.has(id) && byId.get(id).status !== 'failed');
  const tfs = (p.get('t') || '').split(',');
  state.sel = new Set(ids);
  p.get('s').split(',').forEach((id, i) => { if (L.TRANSFORMS[tfs[i]]) state.tf[id] = tfs[i]; });
  const okDate = (x) => /^\d{4}-\d{2}-\d{2}$/.test(x || '');
  if (okDate(p.get('from'))) state.from = p.get('from');
  if (okDate(p.get('to'))) state.to = p.get('to');
  if (['native', 'M', 'Q', 'A'].includes(p.get('f'))) state.freq = p.get('f');
  if (['common', 'full'].includes(p.get('p'))) state.policy = p.get('p');
  if (['latest', 'first'].includes(p.get('vb'))) state.basis = p.get('vb');
  if (['period', 'available'].includes(p.get('db'))) state.dateBy = p.get('db');
  state.auto = false;
  return ids.length > 0;
}

// ---- data ------------------------------------------------------------------------------------
async function getSeries(id) {
  if (!cache.has(id)) {
    const r = await fetch(`data/series/${byId.get(id).slug}.json`, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`could not load ${id}`);
    cache.set(id, await r.json());
  }
  return cache.get(id);
}
const selected = () => [...state.sel].map((id) => byId.get(id)).filter(Boolean);
function applyRecommendation() {
  const r = L.recommend(selected());
  state.freq = r.freq;
  state.tf = { ...state.tf, ...r.transforms };
}

// ---- 1. picker ---------------------------------------------------------------------------------
function revBadge(e) {
  const lab = e.revision?.label || 'unknown';
  return h('span', { class: 'badge', title: revText(e) }, h('span', { class: `dot ${lab}` }), `revised: ${lab}`);
}
function revText(e) {
  const r = e.revision || {};
  if (r.basis === 'measured') return `${Math.round(r.share_revised * 100)}% of values changed after first release, typically by ${r.typical_change} ${r.typical_change_unit} (measured on ${r.n} releases)`;
  if (r.basis === 'market price') return 'market price, never revised';
  return r.label && r.label !== 'unknown' ? `${r.label} (general knowledge of this series, not measured)` : 'not known';
}
function renderPicker() {
  const q = $('search').value.trim().toLowerCase();
  const box = $('picker');
  box.replaceChildren();
  for (const [kind, title] of GROUPS) {
    const rows = manifest.series.filter((e) => e.kind === kind && (!q || `${e.id} ${e.name}`.toLowerCase().includes(q)));
    if (!rows.length) continue;
    box.append(h('h4', {}, title));
    for (const e of rows) {
      const off = e.status === 'failed';
      box.append(h('label', { class: `item${off ? ' off' : ''}` },
        h('input', { type: 'checkbox', checked: state.sel.has(e.id), disabled: off, onchange: (ev) => {
          ev.target.checked ? state.sel.add(e.id) : state.sel.delete(e.id);
          if (state.auto) applyRecommendation(); else if (!state.tf[e.id]) state.tf[e.id] = 'level';
          update();
        } }),
        h('span', { class: 'name' }, e.name, h('span', { class: 'id' }, off ? `${e.id} - unavailable: ${e.issues[0]?.msg || ''}` : `${e.id} | ${e.first} to ${e.last}${e.status === 'stale' ? ' | not refreshed today' : ''}`)),
        h('span', { class: 'badge' }, L.FREQ_NAME[e.frequency] || ''), off ? null : revBadge(e)));
    }
  }
  if (!box.children.length) box.append(h('p', { class: 'muted', style: 'padding:10px' }, 'Nothing matches. To add a ticker or series, see Manage at the bottom.'));
  $('selcount').textContent = `${state.sel.size} selected`;
}

// ---- compute -----------------------------------------------------------------------------------------
async function compute() {
  const series = await Promise.all([...state.sel].map(getSeries));
  const items = series.map((s) => ({ s, pts: L.basePoints(s, state.basis) }));
  const cov = items.map(({ s, pts }) => L.coverage(s, pts, state.from, state.to));
  const cw = L.commonWindow(items, state.from, state.to);
  const win = state.policy === 'common' ? cw : { start: state.from, end: state.to, empty: false };
  const cols = items.map(({ s }) => {
    const transform = state.tf[s.id] || 'level';
    const r = L.prepare(s, { basis: state.basis, freq: state.freq, transform, dateBy: state.dateBy });
    const usedFirst = state.basis === 'first' && s.has_first_release;
    return { s, ...r, transform, basis: usedFirst ? 'first_release' : 'latest',
      pts: win.empty ? [] : L.inWindow(r.pts, win.start, win.end),
      label: s.id + (transform === 'level' ? '' : ` [${transform}]`) + (usedFirst ? ' [first]' : '') };
  });
  return { items, cov, cw, win, cols };
}

// ---- 3. coverage ---------------------------------------------------------------------------------------
function table(head, rows) {
  return h('div', { class: 'scroll' }, h('table', {},
    h('thead', {}, h('tr', {}, head.map((c) => h('th', { class: c.startsWith('#') ? 'num' : '' }, c.replace('#', ''))))),
    h('tbody', {}, rows)));
}
function renderCoverage(res) {
  const box = $('coverage');
  box.replaceChildren();
  if (!res.items.length) { box.append(h('p', { class: 'muted' }, 'Pick at least one series above.')); return; }
  const words = { OK: 'covers it', PARTIAL: 'partly', EMPTY: 'no data' };
  box.append(table(['Series', 'Status', 'First', 'Last', '#Points', '#Covered', 'Revisions', 'Notes'], res.cov.map((c, i) => {
    const e = res.items[i].s;
    return h('tr', {}, h('td', {}, e.id), h('td', {}, h('span', { class: 'badge' }, h('span', { class: `dot ${c.status}` }), words[c.status])),
      h('td', {}, c.first || ''), h('td', {}, c.last || ''), h('td', { class: 'num' }, c.n), h('td', { class: 'num' }, `${c.pct}%`),
      h('td', {}, revText(e)), h('td', {}, [c.detail, ...e.issues.filter((x) => x.level !== 'info').map((x) => x.msg)].filter(Boolean).join('; ')));
  })));
  const short = res.cov.filter((c) => c.status !== 'OK');
  box.append(short.length
    ? h('div', { class: 'note warn' }, `${short.length} of ${res.cov.length} series do not cover the whole timeline: ${short.map((c) => c.id).join(', ')}.`)
    : h('div', { class: 'note info' }, 'Every series covers the whole timeline.'));
  if (state.basis === 'first') {
    const none = res.items.filter(({ s }) => s.kind !== 'price' && !s.has_first_release).map(({ s }) => s.id);
    if (none.length) box.append(h('div', { class: 'note warn' }, `No first-release history for: ${none.join(', ')}. Latest values are used for these.`));
  }
}

// ---- 4. prepare --------------------------------------------------------------------------------------------
function renderPrepare(res) {
  $('freq').value = state.freq; $('basis').value = state.basis; $('dateBy').value = state.dateBy;
  document.querySelectorAll('input[name=policy]').forEach((r) => { r.checked = r.value === state.policy; });
  const rec = L.recommend(selected());
  const matches = rec.freq === state.freq && selected().every((s) => (state.tf[s.id] || 'level') === rec.transforms[s.id]);
  const adv = $('advice');
  adv.replaceChildren();
  if (selected().length) {
    adv.append(h('div', { class: 'note info' },
      h('strong', {}, matches ? 'You are using the recommended setup.' : 'Recommended setup for this selection: '),
      matches ? null : h('button', { onclick: () => { state.auto = true; applyRecommendation(); update(); } }, 'Apply it'),
      h('ul', {}, rec.why.map((w) => h('li', {}, w)))));
    if (rec.warnings.length) adv.append(h('div', { class: 'note warn' }, h('strong', {}, 'Worth knowing'), h('ul', {}, rec.warnings.map((w) => h('li', {}, w)))));
  }
  const box = $('transforms');
  box.replaceChildren();
  if (!res.cols.length) return;
  box.append(table(['Series', 'Published', 'Delivered', 'Roll-up', 'Show as'], res.cols.map((c) => h('tr', {},
    h('td', {}, c.s.id), h('td', {}, L.FREQ_NAME[c.s.frequency]),
    h('td', {}, L.FREQ_NAME[c.freq] + (c.notUpsampled ? ' (not stretched)' : '')),
    h('td', {}, c.rolledUp ? ({ last: 'period-end value', mean: 'average', sum: 'total' })[c.s.agg || 'mean'] : '-'),
    h('td', {}, h('select', { 'aria-label': `Transform for ${c.s.id}`, onchange: (ev) => { state.tf[c.s.id] = ev.target.value; state.auto = false; update(); } },
      Object.entries(L.TRANSFORMS).map(([k, v]) => h('option', { value: k, selected: k === c.transform }, v))))))));
  const notes = [];
  if (res.cols.some((c) => c.notUpsampled)) notes.push('Slower series are left at their own frequency. Stretching them would invent data.');
  if (state.basis === 'first') notes.push('First-release values: a % change between two first releases is close to, but not exactly, the growth first published. For GDP growth use the growth-rate series, which is exact. First-release levels of chained-dollar GDP are not comparable across dates.');
  if (res.cols.some((c) => c.estimatedDates)) notes.push('Some publication dates are estimated from the typical release lag, because the exact date is not on record.');
  if (state.dateBy === 'available') notes.push('Each economic value now sits in the period when it was published, so a row only holds what was known by then. Prices are not moved.');
  for (const n of notes) box.append(h('div', { class: 'note info' }, n));
}

// ---- 5. preview -----------------------------------------------------------------------------------------------
function chart(c) {
  const W = 600, H = 190, m = { l: 46, r: 10, t: 8, b: 22 };
  const pts = c.pts, box = h('div', { class: 'chart' });
  const unit = c.transform === 'level' ? c.s.unit : L.TRANSFORMS[c.transform];
  box.append(h('div', { class: 't', title: c.s.name }, c.s.name), h('div', { class: 'u' }, `${c.s.id} | ${unit} | ${L.FREQ_NAME[c.freq]} | ${pts.length} points`));
  if (pts.length < 2) { box.append(h('p', { class: 'muted' }, 'Not enough points in this window to draw.')); return box; }
  const xs = pts.map((p) => L.toDay(p.d)), ys = pts.map((p) => p.v);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const padY = (y1 - y0) * 0.06; y0 -= padY; y1 += padY;
  const X = (d) => m.l + (d - xs[0]) / Math.max(xs[xs.length - 1] - xs[0], 1) * (W - m.l - m.r);
  const Y = (v) => m.t + (y1 - v) / (y1 - y0) * (H - m.t - m.b);
  const ns = 'http://www.w3.org/2000/svg';
  const s = (tag, a) => { const e = document.createElementNS(ns, tag); for (const k in a) e.setAttribute(k, a[k]); return e; };
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${c.s.name}, ${unit}` });
  for (let i = 0; i <= 3; i++) {
    const v = y0 + (y1 - y0) * i / 3, y = Y(v);
    svg.append(s('line', { class: 'grid', x1: m.l, x2: W - m.r, y1: y, y2: y }));
    const t = s('text', { x: m.l - 6, y: y + 4, 'text-anchor': 'end' }); t.textContent = fmt(+v.toPrecision(3)); svg.append(t);
  }
  if (y0 < 0 && y1 > 0) svg.append(s('line', { class: 'zero', x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }));
  const yA = +pts[0].d.slice(0, 4), yB = +pts[pts.length - 1].d.slice(0, 4), stepY = Math.max(1, Math.ceil((yB - yA + 1) / 6));
  for (let y = yA + 1; y <= yB; y += stepY) {
    const d = L.toDay(`${y}-01-01`);
    if (d < xs[0]) continue;
    const t = s('text', { x: X(d), y: H - 6, 'text-anchor': 'middle' }); t.textContent = y; svg.append(t);
  }
  svg.append(s('polyline', { class: 'line', points: pts.map((p, i) => `${X(xs[i]).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ') }));
  const cross = s('line', { class: 'cross', y1: m.t, y2: H - m.b, visibility: 'hidden' });
  const mark = s('circle', { class: 'mark', r: 4, visibility: 'hidden' });
  svg.append(cross, mark);
  const tip = $('tip');
  const move = (ev) => {
    const r = svg.getBoundingClientRect(), cx = (ev.touches ? ev.touches[0].clientX : ev.clientX);
    const d = xs[0] + ((cx - r.left) / r.width * W - m.l) / (W - m.l - m.r) * (xs[xs.length - 1] - xs[0]);
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < d) lo = mid; else hi = mid; }
    const i = Math.abs(xs[lo] - d) <= Math.abs(xs[hi] - d) ? lo : hi, p = pts[i];
    cross.setAttribute('x1', X(xs[i])); cross.setAttribute('x2', X(xs[i])); cross.setAttribute('visibility', 'visible');
    mark.setAttribute('cx', X(xs[i])); mark.setAttribute('cy', Y(p.v)); mark.setAttribute('visibility', 'visible');
    tip.textContent = `${p.d}: ${fmt(p.v)}${p.from ? ` (period ${p.from})` : ''}`;
    tip.hidden = false;
    const tx = Math.min(cx + 12, window.innerWidth - tip.offsetWidth - 6);
    tip.style.left = `${tx}px`; tip.style.top = `${r.top + 6}px`;
  };
  const leave = () => { cross.setAttribute('visibility', 'hidden'); mark.setAttribute('visibility', 'hidden'); tip.hidden = true; };
  svg.addEventListener('mousemove', move); svg.addEventListener('mouseleave', leave);
  svg.addEventListener('touchstart', move, { passive: true }); svg.addEventListener('touchmove', move, { passive: true }); svg.addEventListener('touchend', leave);
  box.append(svg);
  return box;
}
function renderPreview(res) {
  const d = $('delivered'), charts = $('charts'), prev = $('preview');
  d.replaceChildren(); charts.replaceChildren(); prev.replaceChildren();
  const ok = res.cols.length > 0 && !res.win.empty && res.cols.some((c) => c.pts.length);
  for (const id of ['dlWide', 'dlLong', 'dlReport']) $(id).disabled = !ok;
  if (!res.cols.length) { d.append(h('p', { class: 'muted' }, 'Nothing selected yet.')); return; }
  if (res.win.empty) { d.append(h('div', { class: 'note bad' }, 'No dates are covered by all series at once. Choose "Keep my full timeline" or drop the series that does not overlap.')); return; }
  const wide = L.wideCsv(res.cols);
  d.append(h('p', {}, h('strong', {}, 'Delivered: '), `${res.win.start} to ${res.win.end} | ${res.cols.length} series | ${wide.dates.length} dates`));
  if (state.policy === 'common' && (res.cw.startBy || res.cw.endBy)) {
    d.append(h('p', { class: 'muted' }, [res.cw.startBy && `Start is set by ${res.cw.startBy}.`, res.cw.endBy && `End is set by ${res.cw.endBy}.`, 'Drop that series or keep the full timeline for a longer span.'].filter(Boolean).join(' ')));
  }
  const thin = res.cols.filter((c) => c.pts.length && c.pts.length < 12).map((c) => `${c.s.id} (${c.pts.length})`);
  if (thin.length) d.append(h('div', { class: 'note warn' }, `Very few data points: ${thin.join(', ')}. Any relationship measured on this will be weak.`));
  res.cols.slice(0, 12).forEach((c) => charts.append(chart(c)));
  if (res.cols.length > 12) charts.append(h('p', { class: 'muted' }, `Charts shown for the first 12 of ${res.cols.length} series. All are in the download.`));
  const tail = wide.dates.slice(-10).reverse();
  prev.append(table(['Date', ...res.cols.map((c) => '#' + c.label)], tail.map((dt) => h('tr', {}, h('td', {}, dt), wide.maps.map((mp) => h('td', { class: 'num' }, fmt(mp.get(dt))))))));
  last = { res, wide };
}

// ---- downloads ----------------------------------------------------------------------------------------------------
function download(name, text, type) {
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function report() {
  const { res } = last;
  return { generated_at: manifest.generated_at, data_through: manifest.window_end, timeline_chosen: [state.from, state.to],
    timeline_delivered: [res.win.start, res.win.end], policy: state.policy, frequency: state.freq, value_basis: state.basis,
    dated_by: state.dateBy, link: location.href,
    series: res.cols.map((c, i) => ({ id: c.s.id, name: c.s.name, unit: c.s.unit, published_frequency: c.s.frequency, delivered_frequency: c.freq,
      transform: c.transform, roll_up: c.rolledUp ? c.s.agg : null, value_basis: c.basis, points: c.pts.length, coverage: res.cov[i],
      revision: c.s.revision, release_lag_days: c.s.release_lag_days, release_lag_basis: c.s.lag_basis, status: c.s.status,
      fetched_at: c.s.fetched_at, source: c.s.source, notes: c.s.notes, issues: c.s.issues })) };
}

// ---- header + manage -------------------------------------------------------------------------------------------------
function renderHeader() {
  const c = manifest.counts;
  $('asof').textContent = `Data through ${manifest.window_end}. Refreshed ${manifest.generated_at.replace('T', ' ').slice(0, 16)} New York time. ${c.ok} series up to date${c.stale ? `, ${c.stale} not refreshed today` : ''}${c.failed ? `, ${c.failed} unavailable` : ''}.`;
  const b = $('banner');
  b.replaceChildren();
  const failed = manifest.series.filter((e) => e.status === 'failed');
  if (failed.some((e) => /FRED_API_KEY/.test(e.issues[0]?.msg || ''))) {
    b.append(h('div', { class: 'note bad' }, 'US economic data is off: the FRED key is missing. In your GitHub repo open Settings, Secrets and variables, Actions, and add a repository secret named FRED_API_KEY. Then run the update again (see Manage).'));
  } else if (failed.length) b.append(h('div', { class: 'note warn' }, `Unavailable right now: ${failed.map((e) => e.id).join(', ')}. Details are in the list below.`));
  const repo = manifest.repo, m = $('manage');
  m.replaceChildren(h('p', {}, 'The data refreshes by itself every weekday evening (New York time). Nothing runs on your computer.'));
  if (repo) {
    m.append(h('ul', {},
      h('li', {}, h('a', { href: `https://github.com/${repo}/edit/main/config/watchlist.json`, target: '_blank', rel: 'noopener' }, 'Add or remove tickers and series'),
        ' - edit the list, press "Commit changes", and the dashboard updates in a few minutes. Any Yahoo ticker and any FRED series id works.'),
      h('li', {}, h('a', { href: `https://github.com/${repo}/actions/workflows/update-data.yml`, target: '_blank', rel: 'noopener' }, 'Refresh now or see the update log'),
        ' - press "Run workflow" to refresh on demand.')));
  }
}

// ---- main loop -----------------------------------------------------------------------------------------------------------
let ticket = 0;
async function update() {
  const my = ++ticket;
  renderPicker();
  saveHash();
  try {
    const res = await compute();
    if (my !== ticket) return;
    renderCoverage(res); renderPrepare(res); renderPreview(res);
  } catch (err) {
    $('coverage').replaceChildren(h('div', { class: 'note bad' }, `Could not load data: ${err.message}. Reload the page.`));
  }
}
function setRange(years) {
  state.to = manifest.window_end;
  state.from = years ? L.addDays(L.yearBefore(L.addDays(state.to, 1)), -365 * (years - 1)) : manifest.window_start;
  if (state.from < manifest.window_start) state.from = manifest.window_start;
  $('from').value = state.from; $('to').value = state.to;
}
async function init() {
  try {
    const r = await fetch('data/manifest.json', { cache: 'no-cache' });
    if (!r.ok) throw new Error(r.status);
    manifest = await r.json();
  } catch (e) {
    $('asof').textContent = 'No data has been published yet. Run the "update-data" workflow in the GitHub repo once, then reload.';
    return;
  }
  manifest.series.forEach((e) => byId.set(e.id, e));
  renderHeader();
  setRange(10);
  if (!loadHash()) {
    for (const id of ['TQQQ.adj_close', 'VIXY.adj_close', 'FRED:A191RL1Q225SBEA']) if (byId.get(id) && byId.get(id).status !== 'failed') state.sel.add(id);
    applyRecommendation();
  }
  for (const id of ['from', 'to']) { $(id).min = manifest.window_start; $(id).max = manifest.window_end; $(id).value = state[id]; }
  $('quick').append(...[[1, '1Y'], [3, '3Y'], [5, '5Y'], [10, '10Y'], [0, 'Max']].map(([y, t]) => h('button', { onclick: () => { setRange(y); update(); } }, t)));
  $('search').addEventListener('input', renderPicker);
  for (const id of ['from', 'to']) $(id).addEventListener('change', (ev) => {
    if (!ev.target.value) return;
    state[id] = ev.target.value;
    if (state.from > state.to) { state[id === 'from' ? 'to' : 'from'] = state[id]; $('from').value = state.from; $('to').value = state.to; }
    update();
  });
  $('freq').addEventListener('change', (ev) => { state.freq = ev.target.value; state.auto = false; update(); });
  $('basis').addEventListener('change', (ev) => { state.basis = ev.target.value; update(); });
  $('dateBy').addEventListener('change', (ev) => { state.dateBy = ev.target.value; update(); });
  document.querySelectorAll('input[name=policy]').forEach((r) => r.addEventListener('change', () => { state.policy = r.value; update(); }));
  $('dlWide').addEventListener('click', () => download('comparison_table.csv', last.wide.csv, 'text/csv'));
  $('dlLong').addEventListener('click', () => download('comparison_long.csv', L.longCsv(last.res.cols), 'text/csv'));
  $('dlReport').addEventListener('click', () => download('report.json', JSON.stringify(report(), null, 2), 'application/json'));
  $('share').addEventListener('click', async (ev) => {
    try { await navigator.clipboard.writeText(location.href); ev.target.textContent = 'Link copied'; }
    catch { ev.target.textContent = 'Copy the address bar'; }
    setTimeout(() => { ev.target.textContent = 'Copy link to this setup'; }, 2000);
  });
  update();
}
init();
