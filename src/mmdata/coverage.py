"""Coverage: does each series cover the window you chose? And apply a window policy.

Tolerances are one period of the series' own frequency, because macro dates are period ENDS
(a quarterly value dated 31 Mar covers all of Q1, so a window starting 1 Jan is fully covered).
"""
from __future__ import annotations

import pandas as pd

from .timing import period_start

PERIOD_DAYS = {"D": 7, "W": 10, "M": 35, "Q": 100, "A": 370}  # slack at window edges
GAP_DAYS = {"D": 7, "W": 14, "M": 40, "Q": 105, "A": 380}      # largest allowed hole inside the data


def coverage_report(long: pd.DataFrame, specs, start, end) -> pd.DataFrame:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    window_days = max((end - start).days, 1)
    rows = []
    for s in specs:
        d = long.loc[long.series_id == s.series_id, "date"].sort_values()
        base = {"series_id": s.series_id, "name": s.name, "frequency": s.frequency,
                "revision_risk": s.revision_risk}
        if d.empty:
            rows.append({**base, "status": "EMPTY", "first": None, "last": None, "n_obs": 0,
                         "window_covered_pct": 0.0, "detail": "no data inside the chosen window"})
            continue
        f = s.frequency
        first, last = d.iloc[0], d.iloc[-1]
        problems, notes = [], []
        lead = (first - start).days
        if lead > PERIOD_DAYS[f]:
            problems.append(f"starts {first.date()}, {lead} days after window start")
        end_gap = (end - last).days
        if end_gap > PERIOD_DAYS[f] + s.release_lag_days:
            problems.append(f"ends {last.date()}, {end_gap} days before window end")
        elif end_gap > PERIOD_DAYS[f]:
            notes.append("latest period not yet published (normal release lag)")
        if len(d) > 1:
            gap = int(d.diff().dt.days.max())
            if gap > GAP_DAYS[f]:
                problems.append(f"largest internal gap {gap} days")
        eff_first = max(period_start(first, f), start)
        covered = max((min(last, end) - eff_first).days, 0) / window_days * 100
        rows.append({**base, "status": "PARTIAL" if problems else "OK", "first": first.date(),
                     "last": last.date(), "n_obs": len(d), "window_covered_pct": round(min(covered, 100), 1),
                     "detail": "; ".join(problems + notes)})
    return pd.DataFrame(rows)


def limiting_series(long: pd.DataFrame, specs):
    """Which series sets the common start and the common end? Returns (start_id, start_date, end_id, end_date)
    or None when there is no data. Lets the UI explain WHY a 'common' window is as short as it is."""
    freq = {s.series_id: s.frequency for s in specs}
    firsts = {sid: period_start(d.min(), freq[sid]) for sid, d in long.groupby("series_id")["date"]}
    lasts = {sid: d.max() for sid, d in long.groupby("series_id")["date"]}
    if not firsts:
        return None
    s_id, e_id = max(firsts, key=firsts.get), min(lasts, key=lasts.get)
    return s_id, firsts[s_id], e_id, lasts[e_id]


def apply_window(long: pd.DataFrame, specs, start, end, policy: str = "common"):
    """policy 'full'   : keep the chosen window as is (series that are short just have blanks).
       policy 'common' : trim every series to the span ALL non-empty series cover.
    Returns (frame, (window_start, window_end)). EMPTY series are ignored when finding the common span."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if policy == "full":
        return long.copy(), (start, end)
    if policy != "common":
        raise ValueError("policy must be 'common' or 'full'")
    freq = {s.series_id: s.frequency for s in specs}
    starts, ends = [], []
    for sid, d in long.groupby("series_id")["date"]:
        starts.append(period_start(d.min(), freq[sid]))
        ends.append(d.max())
    if not starts:
        return long.copy(), (start, end)
    cs, ce = max(max(starts), start), min(min(ends), end)
    if cs > ce:
        return long.iloc[0:0].copy(), (cs, ce)
    return long[(long.date >= cs) & (long.date <= ce)].copy(), (cs, ce)
