// Pure data logic for the dashboard (no DOM). Shared by the browser and the Node tests.
// A "point" is {d: 'YYYY-MM-DD' (period end), v: number, a: availability date or null, est: bool}.

export const RANK = { D: 0, W: 1, M: 2, Q: 3, A: 4 };
export const FREQ_NAME = { D: 'daily', W: 'weekly', M: 'monthly', Q: 'quarterly', A: 'annual' };
const PERIOD_DAYS = { D: 7, W: 10, M: 35, Q: 100, A: 370 };   // slack allowed at the window edges
const GAP_DAYS = { D: 7, W: 14, M: 40, Q: 105, A: 380 };      // largest hole allowed inside a series
const NOMINAL = { D: 1, W: 7, M: 31, Q: 92, A: 366 };
const EXPECT = { 'M>Q': 3, 'M>A': 12, 'Q>A': 4 };
export const TRANSFORMS = {
  level: 'As published (level)',
  pct: '% change over the period',
  log: 'Log return over the period (x100)',
  diff: 'Change over the period (difference)',
  yoy: '% change vs a year earlier',
};

// ---- dates -------------------------------------------------------------------------------
export const toDay = (iso) => Math.round(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 864e5);
export const fromDay = (n) => new Date(n * 864e5).toISOString().slice(0, 10);
export const addDays = (iso, n) => fromDay(toDay(iso) + n);
const pad = (n) => String(n).padStart(2, '0');
const lastDom = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();

export function periodEnd(iso, f) {
  const y = +iso.slice(0, 4), m = +iso.slice(5, 7);
  if (f === 'M') return `${y}-${pad(m)}-${pad(lastDom(y, m))}`;
  if (f === 'Q') { const qm = Math.ceil(m / 3) * 3; return `${y}-${pad(qm)}-${pad(lastDom(y, qm))}`; }
  if (f === 'A') return `${y}-12-31`;
  return iso;
}
export function periodStart(endIso, f) {
  const y = +endIso.slice(0, 4), m = +endIso.slice(5, 7);
  if (f === 'M') return `${y}-${pad(m)}-01`;
  if (f === 'Q') return `${y}-${pad(m - 2)}-01`;
  if (f === 'A') return `${y}-01-01`;
  return endIso;
}
export function yearBefore(iso) {
  const y = +iso.slice(0, 4) - 1, m = +iso.slice(5, 7), d = Math.min(+iso.slice(8, 10), lastDom(y, m));
  return `${y}-${pad(m)}-${pad(d)}`;
}

// ---- series -> points ----------------------------------------------------------------------
// basis 'latest' = today's revised values; 'first' = values as first published (rows without one are dropped).
export function basePoints(s, basis = 'latest') {
  const useFirst = basis === 'first' && s.has_first_release;
  const out = [];
  for (let i = 0; i < s.dates.length; i++) {
    const v = useFirst ? s.first_values[i] : s.values[i];
    if (v === null || v === undefined) continue;
    let a = s.available ? s.available[i] : null, est = false;
    if (s.kind === 'price') a = s.dates[i];
    else if (!a) { a = addDays(s.dates[i], s.release_lag_days || 0); est = true; }
    out.push({ d: s.dates[i], v, a, est });
  }
  return out;
}
export const inWindow = (pts, start, end) => pts.filter((p) => p.d >= start && p.d <= end);

// ---- coverage -------------------------------------------------------------------------------
export function coverage(s, pts, start, end) {
  const f = s.frequency, w = inWindow(pts, start, end);
  const base = { id: s.id, status: 'EMPTY', first: null, last: null, n: 0, pct: 0, detail: 'no data inside the chosen timeline' };
  if (!w.length) return base;
  const first = w[0].d, last = w[w.length - 1].d, problems = [], notes = [];
  const lead = toDay(first) - toDay(start);
  if (lead > PERIOD_DAYS[f]) problems.push(`starts ${first}, ${lead} days after the timeline starts`);
  const endGap = toDay(end) - toDay(last);
  if (endGap > PERIOD_DAYS[f] + (s.release_lag_days || 0)) problems.push(`ends ${last}, ${endGap} days before the timeline ends`);
  else if (endGap > PERIOD_DAYS[f]) notes.push('latest period not published yet (normal release lag)');
  let gap = 0;
  for (let i = 1; i < w.length; i++) gap = Math.max(gap, toDay(w[i].d) - toDay(w[i - 1].d));
  if (gap > GAP_DAYS[f]) problems.push(`largest gap inside the data is ${gap} days`);
  const effFirst = Math.max(toDay(periodStart(first, f)), toDay(start));
  const span = Math.max(toDay(end) - toDay(start), 1);
  const pct = Math.min(100, Math.max(0, (Math.min(toDay(last), toDay(end)) - effFirst) / span * 100));
  return { id: s.id, status: problems.length ? 'PARTIAL' : 'OK', first, last, n: w.length,
    pct: Math.round(pct * 10) / 10, detail: [...problems, ...notes].join('; ') };
}

// The span every non-empty series covers. Also says which series sets each end.
export function commonWindow(items, start, end) {
  let cs = start, ce = end, startBy = null, endBy = null;
  for (const { s, pts } of items) {
    const w = inWindow(pts, start, end);
    if (!w.length) continue;
    const ps = periodStart(w[0].d, s.frequency), pe = w[w.length - 1].d;
    if (ps > cs) { cs = ps; startBy = s.id; }
    if (pe < ce) { ce = pe; endBy = s.id; }
  }
  return { start: cs, end: ce, startBy, endBy, empty: cs > ce };
}

// ---- frequency conversion (downwards only) ------------------------------------------------------
// Never upsamples: a quarterly series asked for monthly stays quarterly (and is reported).
export function aggregate(pts, native, target, agg) {
  if (!target || target === 'native' || RANK[native] >= RANK[target]) {
    return { pts, changed: false, notUpsampled: !!target && target !== 'native' && RANK[native] > RANK[target] };
  }
  const groups = new Map();
  for (const p of pts) {
    const k = periodEnd(p.d, target);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(p);
  }
  const out = [];
  for (const [k, g] of groups) {
    const lastP = g[g.length - 1];
    let complete;
    const exp = EXPECT[`${native}>${target}`];
    if (exp) complete = agg === 'last' ? periodEnd(lastP.d, target) === k && lastP.d === k : g.length === exp;
    else {
      const tol = native === 'D' ? 5 : 9;
      complete = toDay(k) - toDay(lastP.d) <= tol;
      if (complete && agg !== 'last') complete = toDay(g[0].d) - toDay(periodStart(k, target)) <= tol;
    }
    if (!complete) continue;   // partial periods are dropped, never estimated
    let v;
    if (agg === 'last') v = lastP.v;
    else { v = g.reduce((t, p) => t + p.v, 0); if (agg !== 'sum') v /= g.length; }
    out.push({ d: k, v, a: lastP.a, est: lastP.est });
  }
  return { pts: out, changed: true, notUpsampled: false };
}

// ---- transforms ---------------------------------------------------------------------------------------
export function transform(pts, kind, freq) {
  if (kind === 'level') return pts;
  const out = [];
  if (kind === 'yoy') {
    const tol = freq === 'D' ? 6 : freq === 'W' ? 9 : 3;
    let j = 0;
    for (const p of pts) {
      const want = toDay(yearBefore(p.d));
      while (j + 1 < pts.length && toDay(pts[j + 1].d) <= want) j++;
      const q = pts[j];
      if (toDay(q.d) <= want && want - toDay(q.d) <= tol && q.v > 0) out.push({ ...p, v: (p.v / q.v - 1) * 100 });
    }
    return out;
  }
  const maxGap = NOMINAL[freq] * 1.6 + (freq === 'D' ? 5 : 0);
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i], q = pts[i - 1];
    if (toDay(p.d) - toDay(q.d) > maxGap) continue;         // a hole: no change is invented across it
    let v = null;
    if (kind === 'diff') v = p.v - q.v;
    else if (q.v > 0 && p.v > 0) v = kind === 'log' ? Math.log(p.v / q.v) * 100 : (p.v / q.v - 1) * 100;
    if (v !== null && Number.isFinite(v)) out.push({ ...p, v });
  }
  return out;
}

// ---- date by availability ("what was known when") ----------------------------------------------------------
// Moves each value to the period in which it was PUBLISHED. Prices do not move.
export function shiftToAvailability(pts, freq) {
  const m = new Map();
  for (const p of pts) m.set(periodEnd(p.a || p.d, freq), { ...p, d: periodEnd(p.a || p.d, freq), from: p.d });
  return [...m.values()].sort((x, y) => (x.d < y.d ? -1 : 1));
}

// ---- recommendation -------------------------------------------------------------------------------------------
const isRate = (s) => /%|percent/i.test(s.unit || '');
export function suggestTransform(s) {
  if (s.suggest) return s.suggest;
  if (s.kind === 'price') return /volume$/.test(s.id) ? 'level' : 'pct';
  return isRate(s) ? 'level' : 'pct';
}
export function recommend(series) {
  if (!series.length) return { freq: 'native', transforms: {}, why: [], warnings: [] };
  const ranks = series.map((s) => RANK[s.frequency]);
  const low = Math.max(...ranks), mixed = new Set(ranks).size > 1;
  const freq = mixed ? Object.keys(RANK).find((k) => RANK[k] === low) : 'native';
  const eff = mixed ? freq : series[0].frequency;
  const transforms = {}, why = [], warnings = [];
  if (mixed) why.push(`Everything is brought to ${FREQ_NAME[eff]} because that is the slowest series you picked. Faster series are rolled up; slower ones are never stretched.`);
  for (const s of series) {
    const t = suggestTransform(s);
    transforms[s.id] = t;
    if (s.kind === 'price' && t === 'pct') why.push(`${s.id}: ${FREQ_NAME[eff]} % return. Prices trend, so compare returns, not price levels.`);
    else if (t === 'pct') why.push(`${s.id}: ${FREQ_NAME[eff]} % change. It is a level that trends, so growth is the comparable number.`);
    else why.push(`${s.id}: as published. It is already a rate or a growth figure.`);
    if (s.kind === 'price' && /\.close$/.test(s.id)) warnings.push(`${s.id} ignores dividends. Use adj_close for returns.`);
    if (['medium', 'high'].includes(s.revision?.label)) warnings.push(`${s.id} is revised after release (${s.revision.label}). For "what did the market know" questions use first-release values dated by release.`);
  }
  if (eff === 'A') warnings.push('An annual series forces an annual comparison: very few data points. Drop it to keep quarterly or monthly detail.');
  return { freq, transforms, why, warnings };
}

// ---- full pipeline for one series ----------------------------------------------------------------------------
export function prepare(s, opt) {
  const pts0 = basePoints(s, opt.basis);
  const agg = aggregate(pts0, s.frequency, opt.freq, s.agg || 'mean');
  const eff = agg.changed ? opt.freq : s.frequency;
  let pts = transform(agg.pts, opt.transform || 'level', eff);
  if (opt.dateBy === 'available' && s.kind !== 'price') pts = shiftToAvailability(pts, eff);
  return { pts, freq: eff, notUpsampled: agg.notUpsampled, rolledUp: agg.changed, estimatedDates: pts.some((p) => p.est) && opt.dateBy === 'available' };
}

// ---- export -------------------------------------------------------------------------------------------------------
const cell = (x) => (x === null || x === undefined ? '' : /[",\n]/.test(String(x)) ? `"${String(x).replace(/"/g, '""')}"` : String(x));
const num = (v) => (v === null || v === undefined ? '' : String(+v.toPrecision(10)));
export function wideCsv(cols) {   // cols: [{label, pts}]
  const dates = [...new Set(cols.flatMap((c) => c.pts.map((p) => p.d)))].sort();
  const maps = cols.map((c) => new Map(c.pts.map((p) => [p.d, p.v])));
  const rows = [['date', ...cols.map((c) => c.label)].map(cell).join(',')];
  for (const d of dates) rows.push([d, ...maps.map((m) => num(m.get(d)))].join(','));
  return { csv: rows.join('\n') + '\n', dates, maps };
}
export function longCsv(cols) {   // cols: [{s, pts, freq, transform, basis}]
  const head = ['date', 'series_id', 'value', 'unit', 'frequency', 'transform', 'value_basis', 'source', 'revision_risk', 'available_date', 'available_date_basis', 'fetched_at'];
  const rows = [head.join(',')];
  for (const c of cols) for (const p of c.pts) {
    rows.push([p.d, c.s.id, num(p.v), c.transform === 'level' ? c.s.unit : TRANSFORMS[c.transform], c.freq, c.transform, c.basis,
      c.s.source, c.s.revision?.label || '', p.a || '', c.s.kind === 'price' ? 'market' : p.est ? 'estimated' : 'actual', c.s.fetched_at || ''].map(cell).join(','));
  }
  return rows.join('\n') + '\n';
}
