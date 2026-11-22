"""Revisions: turn FRED's archive of published versions into
  - the latest value            (what the source says today)
  - the first-release value     (what was published first)
  - the availability date       (when that first release came out)
  - a MEASURED revision label   (how often, and by how much, values changed after release)

Honesty rule: the archive only starts on some date. Observations already present in the earliest
archived version have no known first release, so their first value / availability date are left blank.
"""
from __future__ import annotations

import pandas as pd

LABELS = ("none", "low", "medium", "high")


def summarise(v: pd.DataFrame) -> pd.DataFrame:
    """v: date, realtime_start, value (one row per published version).
    Returns one row per date: date, value, first_value, available_date, n_versions."""
    if v.empty:
        return pd.DataFrame({"date": [], "value": [], "first_value": [], "available_date": [], "n_versions": []})
    v = v.sort_values(["date", "realtime_start"])
    g = v.groupby("date", sort=True)
    out = pd.DataFrame({
        "value": g["value"].last(), "first_value": g["value"].first(),
        "available_date": g["realtime_start"].first(), "n_versions": g["value"].size(),
    }).reset_index()
    archive_start = v["realtime_start"].min()
    unknown = out["available_date"] <= archive_start
    out.loc[unknown, ["first_value"]] = float("nan")
    out.loc[unknown, ["available_date"]] = pd.NaT
    return out


def measure(summary: pd.DataFrame, unit: str = "") -> dict:
    """How often and how much values were revised, over observations with a known first release.
    The newest observation is skipped when it has only one version (it has had no chance to be revised)."""
    k = summary.dropna(subset=["first_value"])
    if len(k) and k["n_versions"].iloc[-1] == 1:
        k = k.iloc[:-1]
    if len(k) < 8:
        return {"label": None, "basis": "too little archive history", "n": int(len(k))}
    diff = (k["value"] - k["first_value"]).abs()
    is_pct = "%" in unit or "percent" in unit.lower()
    size = diff if is_pct else (diff / k["first_value"].abs().where(k["first_value"] != 0)) * 100
    tol = 1e-9 if is_pct else 1e-7
    revised = (size.fillna(0) > tol)
    share = float(revised.mean())
    typical = float(size[revised].median()) if revised.any() else 0.0
    label = "none" if share < 0.02 else "low" if share < 0.25 else "medium" if share < 0.60 else "high"
    small = typical < (0.05 if is_pct else 0.2)
    if small and label in ("medium", "high"):
        label = "low"  # revised often, but by very little
    return {"label": label, "basis": "measured", "n": int(len(k)), "share_revised": round(share, 3),
            "typical_change": round(typical, 3), "typical_change_unit": "points" if is_pct else "% of value"}
