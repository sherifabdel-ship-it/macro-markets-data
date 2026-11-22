import test from 'node:test';
import assert from 'node:assert/strict';
import * as L from '../../site/lib.js';

const days = (from, to, step = 1) => { const o = []; for (let d = L.toDay(from); d <= L.toDay(to); d += step) { const wd = new Date(d * 864e5).getUTCDay(); if (wd && wd < 6) o.push(L.fromDay(d)); } return o; };
const price = (id, dates, f = (i) => 100 + i) => ({ id, kind: 'price', frequency: 'D', agg: 'last', unit: 'USD', release_lag_days: 0, dates, values: dates.map((_, i) => f(i)), revision: { label: 'none' } });
const qEnds = (y0, y1) => { const o = []; for (let y = y0; y <= y1; y++) for (const m of ['03-31', '06-30', '09-30', '12-31']) o.push(`${y}-${m}`); return o; };
const gdp = (dates, extra = {}) => ({ id: 'FRED:GDPC1', kind: 'fred', frequency: 'Q', agg: 'mean', unit: 'bn USD', release_lag_days: 30, dates, values: dates.map((_, i) => 1000 * 1.01 ** i), revision: { label: 'high' }, ...extra });

test('period helpers', () => {
  assert.equal(L.periodEnd('2024-02-10', 'M'), '2024-02-29');
  assert.equal(L.periodEnd('2024-05-01', 'Q'), '2024-06-30');
  assert.equal(L.periodEnd('2024-05-01', 'A'), '2024-12-31');
  assert.equal(L.periodStart('2024-03-31', 'Q'), '2024-01-01');
  assert.equal(L.periodStart('2024-12-31', 'A'), '2024-01-01');
  assert.equal(L.yearBefore('2024-02-29'), '2023-02-28');
  assert.equal(L.addDays('2024-12-31', 1), '2025-01-01');
});

test('daily prices roll up to quarter-end values; unfinished quarter is dropped', () => {
  const s = price('TQQQ.adj_close', days('2023-01-02', '2023-11-15'));
  const { pts, changed } = L.aggregate(L.basePoints(s), 'D', 'Q', 'last');
  assert.ok(changed);
  assert.deepEqual(pts.map((p) => p.d), ['2023-03-31', '2023-06-30', '2023-09-30']);   // Q4 not finished -> dropped
  assert.equal(pts[0].v, s.values[s.dates.indexOf('2023-03-31')]);
});

test('monthly roll-up needs every month; sum and mean differ; never upsample', () => {
  const m = ['2023-01-31', '2023-02-28', '2023-03-31', '2023-04-30', '2023-05-31'].map((d, i) => ({ d, v: i + 1, a: d }));
  assert.deepEqual(L.aggregate(m, 'M', 'Q', 'sum').pts.map((p) => [p.d, p.v]), [['2023-03-31', 6]]);
  assert.equal(L.aggregate(m, 'M', 'Q', 'mean').pts[0].v, 2);
  assert.equal(L.aggregate(m.slice(1), 'M', 'Q', 'last').pts.length, 1);       // Feb+Mar: quarter-end value exists
  assert.equal(L.aggregate(m.slice(1), 'M', 'Q', 'mean').pts.length, 0);       // but a mean of 2 months is refused
  const up = L.aggregate(m, 'M', 'D', 'mean');
  assert.ok(!up.changed && up.pts === m);
  assert.ok(L.aggregate([{ d: '2023-03-31', v: 1 }], 'Q', 'M', 'mean').notUpsampled);
});

test('transforms: pct, log, diff, yoy, and no change invented across a hole', () => {
  const q = [{ d: '2023-03-31', v: 100 }, { d: '2023-06-30', v: 110 }, { d: '2023-09-30', v: 99 }, { d: '2024-06-30', v: 121 }];
  const pct = L.transform(q, 'pct', 'Q');
  assert.deepEqual(pct.map((p) => [p.d, +p.v.toFixed(4)]), [['2023-06-30', 10], ['2023-09-30', -10]]);   // 2024-06 skipped: gap
  assert.equal(+L.transform(q, 'log', 'Q')[0].v.toFixed(4), 9.531);
  assert.equal(L.transform(q, 'diff', 'Q')[0].v, 10);
  assert.deepEqual(L.transform(q, 'yoy', 'Q').map((p) => [p.d, +p.v.toFixed(2)]), [['2024-06-30', 10]]);
  assert.equal(L.transform([{ d: '2023-03-31', v: -1 }, { d: '2023-06-30', v: 2 }], 'pct', 'Q').length, 0);  // % of a negative: refused
  assert.equal(L.transform(q, 'level', 'Q'), q);
});

test('coverage flags late start, early end, gaps; release lag is not a fault', () => {
  const s = gdp(qEnds(2020, 2023));
  const pts = L.basePoints(s);
  assert.equal(L.coverage(s, pts, '2020-01-01', '2023-12-31').status, 'OK');
  assert.equal(L.coverage(s, pts, '2020-01-01', '2023-12-31').pct, 100);
  const late = L.coverage(s, pts, '2015-01-01', '2023-12-31');
  assert.equal(late.status, 'PARTIAL'); assert.match(late.detail, /starts 2020-03-31/);
  const lag = L.coverage(s, pts, '2020-01-01', '2024-04-20');
  assert.equal(lag.status, 'OK'); assert.match(lag.detail, /not published yet/);
  assert.match(L.coverage(s, pts, '2020-01-01', '2025-06-30').detail, /ends 2023-12-31/);
  const holed = pts.filter((p) => !p.d.startsWith('2021'));
  assert.match(L.coverage(s, holed, '2020-01-01', '2023-12-31').detail, /largest gap/);
  assert.equal(L.coverage(s, pts, '1990-01-01', '1995-01-01').status, 'EMPTY');
});

test('common window names the limiting series', () => {
  const a = price('TQQQ.adj_close', days('2019-01-01', '2024-06-28'));
  const b = gdp(qEnds(2021, 2023));
  const cw = L.commonWindow([{ s: a, pts: L.basePoints(a) }, { s: b, pts: L.basePoints(b) }], '2019-01-01', '2024-06-28');
  assert.deepEqual([cw.start, cw.end, cw.startBy, cw.endBy], ['2021-01-01', '2023-12-31', 'FRED:GDPC1', 'FRED:GDPC1']);
  assert.ok(L.commonWindow([{ s: a, pts: [{ d: '2019-01-02', v: 1 }] }, { s: b, pts: L.basePoints(b) }], '2019-01-01', '2024-06-28').empty);
});

test('first-release basis and dating by availability', () => {
  const dates = qEnds(2023, 2023);
  const s = gdp(dates, { has_first_release: true, first_values: [null, 990, 1000, 1010], available: [null, '2023-07-27', '2023-10-26', '2024-01-25'] });
  const first = L.basePoints(s, 'first');
  assert.deepEqual(first.map((p) => p.v), [990, 1000, 1010]);                                  // unknown first release dropped
  const latest = L.basePoints(s, 'latest');
  assert.deepEqual([latest[0].a, latest[0].est], ['2023-04-30', true]);                         // estimated from release lag
  const r = L.prepare(s, { basis: 'first', freq: 'native', transform: 'level', dateBy: 'available' });
  assert.deepEqual(r.pts.map((p) => [p.d, p.v, p.from]), [['2023-09-30', 990, '2023-06-30'], ['2023-12-31', 1000, '2023-09-30'], ['2024-03-31', 1010, '2023-12-31']]);
  const px = price('SPY.adj_close', days('2023-01-02', '2023-12-29'));
  const rp = L.prepare(px, { basis: 'first', freq: 'Q', transform: 'pct', dateBy: 'available' });
  assert.deepEqual(rp.pts.map((p) => p.d), ['2023-06-30', '2023-09-30', '2023-12-31']);       // prices never shift
});

test('recommendation: slowest frequency, returns for prices, growth for levels, rates as published', () => {
  const tq = price('TQQQ.adj_close', []), cl = price('VIXY.close', []), g = gdp([]);
  const rate = { id: 'FRED:UNRATE', kind: 'fred', frequency: 'M', unit: '%', revision: { label: 'low' } };
  const vix = { id: 'FRED:VIXCLS', kind: 'fred', frequency: 'D', unit: 'index', suggest: 'level', revision: { label: 'none' } };
  const r = L.recommend([tq, cl, g, rate, vix]);
  assert.equal(r.freq, 'Q');
  assert.deepEqual(r.transforms, { 'TQQQ.adj_close': 'pct', 'VIXY.close': 'pct', 'FRED:GDPC1': 'pct', 'FRED:UNRATE': 'level', 'FRED:VIXCLS': 'level' });
  assert.ok(r.warnings.some((w) => /VIXY.close ignores dividends/.test(w)) && r.warnings.some((w) => /GDPC1 is revised/.test(w)));
  assert.equal(L.recommend([tq, cl]).freq, 'native');
  assert.ok(L.recommend([tq, { ...g, frequency: 'A' }]).warnings.some((w) => /annual/.test(w)));
});

test('full pipeline + CSV export', () => {
  const tq = price('TQQQ.adj_close', days('2022-10-03', '2023-12-29'), (i) => 100 * 1.001 ** i);
  const g = gdp(qEnds(2022, 2023));
  const a = L.prepare(tq, { freq: 'Q', transform: 'pct' }), b = L.prepare(g, { freq: 'Q', transform: 'pct' });
  assert.equal(a.freq, 'Q'); assert.ok(a.rolledUp && !b.rolledUp);
  assert.ok(Math.abs(b.pts[0].v - 1) < 1e-9);
  const w = L.wideCsv([{ label: 'TQQQ.adj_close', pts: a.pts }, { label: 'FRED:GDPC1', pts: b.pts }]);
  const lines = w.csv.trim().split('\n');
  assert.equal(lines[0], 'date,TQQQ.adj_close,FRED:GDPC1');
  assert.ok(lines.find((l) => l.startsWith('2023-03-31,')).split(',').every((x) => x !== ''));
  assert.ok(lines.find((l) => l.startsWith('2022-06-30,')).split(',')[1] === '');             // price not yet available: blank, not zero
  const long = L.longCsv([{ s: g, pts: b.pts, freq: 'Q', transform: 'pct', basis: 'latest' }]).split('\n');
  assert.equal(long[0].split(',').length, 12);
  assert.match(long[1], /^2022-06-30,FRED:GDPC1,1,% change over the period,Q,pct,latest,,high,2022-07-30,estimated,/);
});
