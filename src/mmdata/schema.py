"""The one standard format every series is delivered in (long table).

date          period END date (exchange date for prices; quarter/month/year end for macro)
series_id     e.g. TQQQ.adj_close | FRED:GDPC1 | WB:EGY:NY.GDP.MKTP.CD
value         float
unit          human-readable unit
frequency     D, W, M, Q or A
source        yahoo | fred | worldbank
revision_risk none | low | medium | high  (how often published values get revised)
fetched_at    ONE timestamp (New York time) shared by every row in a run
"""
from __future__ import annotations

import pandas as pd

COLUMNS = ["date", "series_id", "value", "unit", "frequency", "source", "revision_risk", "fetched_at"]


def empty() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.Series(dtype="datetime64[ns]"),
        "series_id": pd.Series(dtype="object"),
        "value": pd.Series(dtype="float64"),
        "unit": pd.Series(dtype="object"),
        "frequency": pd.Series(dtype="object"),
        "source": pd.Series(dtype="object"),
        "revision_risk": pd.Series(dtype="object"),
        "fetched_at": pd.Series(dtype="object"),
    })


def to_long(df: pd.DataFrame, spec, fetched_at: str) -> pd.DataFrame:
    """df has columns date, value. Returns the standard long frame for one series."""
    out = df[["date", "value"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna().drop_duplicates("date", keep="last").sort_values("date")
    out["series_id"] = spec.series_id
    out["unit"] = spec.unit
    out["frequency"] = spec.frequency
    out["source"] = spec.source
    out["revision_risk"] = spec.revision_risk
    out["fetched_at"] = fetched_at
    return out[COLUMNS].reset_index(drop=True)


def to_wide(long: pd.DataFrame) -> pd.DataFrame:
    """One column per series. Dates are the union; blanks where a series has no value."""
    if long.empty:
        return pd.DataFrame()
    return long.pivot(index="date", columns="series_id", values="value").sort_index()
