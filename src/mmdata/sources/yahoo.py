"""Yahoo Finance via yfinance (primary price source, free)."""
from __future__ import annotations

import time

import pandas as pd

from ..http import SourceError


def fetch(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Columns: date, open, high, low, close, adj_close, volume. end is INCLUSIVE.
    close = split-adjusted; adj_close = split + dividend adjusted."""
    import yfinance as yf

    last: Exception | None = None
    for i in range(4):
        try:
            raw = yf.Ticker(ticker.replace(".", "-")).history(
                start=start.date().isoformat(),
                end=(end + pd.Timedelta(days=1)).date().isoformat(),  # yfinance end is exclusive
                interval="1d", auto_adjust=False, actions=False)
            break
        except Exception as e:
            last = e
            time.sleep(2 ** (i + 1))
    else:
        raise SourceError(f"Yahoo request failed for {ticker}: {last}")
    if raw is None or raw.empty:
        raise SourceError(f"Yahoo returned no data for {ticker} (wrong ticker, or none in this window)")
    df = raw.rename(columns=str.lower).rename(columns={"adj close": "adj_close"})
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return df[["open", "high", "low", "close", "adj_close", "volume"]].reset_index()
