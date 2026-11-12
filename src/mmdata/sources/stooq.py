"""Stooq: independent second source, used ONLY to cross-check Yahoo's close."""
from __future__ import annotations

import pandas as pd

from ..http import SourceError


def fetch(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Columns: date, close (split-adjusted, not dividend-adjusted)."""
    url = f"https://stooq.com/q/d/l/?s={ticker.lower().replace('-', '.')}.us&i=d"
    try:
        raw = pd.read_csv(url)
    except Exception as e:
        raise SourceError(f"Stooq request failed for {ticker}: {e}") from None
    if raw.empty or "Close" not in raw:
        raise SourceError(f"Stooq returned no data for {ticker}")
    df = raw.rename(columns=str.lower)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df[(df["date"] >= start) & (df["date"] <= end)][["date", "close"]]
