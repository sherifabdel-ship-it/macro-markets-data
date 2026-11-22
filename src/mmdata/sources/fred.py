"""FRED (St. Louis Fed). Free API key: https://fred.stlouisfed.org -> My Account -> API Keys."""
from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv

from ..http import SourceError, get_json

URL = "https://api.stlouisfed.org/fred/series/observations"


def get_key() -> str:
    load_dotenv()
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key or key == "paste_your_key_here":
        raise SourceError("FRED_API_KEY is not set. Put it in a local .env file (see .env.example).")
    return key


def fetch(fred_id: str, start: pd.Timestamp, end: pd.Timestamp, key: str | None = None) -> pd.DataFrame:
    """Columns: date (FRED's period-START date), value. Latest published values (not first release)."""
    key = key or get_key()
    data = get_json(URL, {
        "series_id": fred_id, "api_key": key, "file_type": "json",
        "observation_start": (start - pd.DateOffset(years=1)).date().isoformat(),  # catch the period overlapping start
        "observation_end": end.date().isoformat(),
    }, secrets=(key,))
    obs = data.get("observations") if isinstance(data, dict) else None
    if obs is None:
        raise SourceError(f"FRED returned no observations for {fred_id}: {data.get('error_message', 'unknown error')}")
    if not obs:
        return pd.DataFrame({"date": [], "value": []})
    df = pd.DataFrame(obs)[["date", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # FRED marks missing as "."
    df["date"] = pd.to_datetime(df["date"])
    return df.dropna().reset_index(drop=True)


META_URL = "https://api.stlouisfed.org/fred/series"
FREQ_MAP = {"D": "D", "W": "W", "BW": "W", "M": "M", "Q": "Q", "A": "A"}


def fetch_meta(fred_id: str, key: str | None = None) -> dict:
    """Title, unit and frequency for ANY FRED series id, so ids need not be hand-catalogued."""
    key = key or get_key()
    data = get_json(META_URL, {"series_id": fred_id, "api_key": key, "file_type": "json"}, secrets=(key,))
    rows = data.get("seriess") if isinstance(data, dict) else None
    if not rows:
        raise SourceError(f"FRED has no series called {fred_id}")
    m = rows[0]
    freq = FREQ_MAP.get(m.get("frequency_short", ""))
    if not freq:
        raise SourceError(f"{fred_id}: frequency '{m.get('frequency')}' is not supported")
    sa = m.get("seasonal_adjustment_short", "")
    return {"name": m.get("title", fred_id) + (f" ({sa})" if sa and sa != "NSA" else ""),
            "unit": m.get("units_short") or m.get("units") or "", "frequency": freq}


def fetch_vintages(fred_id: str, start: pd.Timestamp, end: pd.Timestamp, key: str | None = None) -> pd.DataFrame:
    """Every published version of every observation (FRED's ALFRED archive).
    Columns: date (period START), realtime_start (date this version was published), value."""
    key = key or get_key()
    rows, offset, limit = [], 0, 100000
    while True:
        data = get_json(URL, {
            "series_id": fred_id, "api_key": key, "file_type": "json",
            "observation_start": (start - pd.DateOffset(years=1)).date().isoformat(),
            "observation_end": end.date().isoformat(),
            "realtime_start": "1776-07-04", "realtime_end": "9999-12-31",
            "limit": limit, "offset": offset,
        }, secrets=(key,))
        obs = data.get("observations") if isinstance(data, dict) else None
        if obs is None:
            raise SourceError(f"FRED returned no vintages for {fred_id}: {data.get('error_message', 'unknown error')}")
        rows.extend(obs)
        if len(obs) < limit or offset > 2_000_000:
            break
        offset += limit
    if not rows:
        return pd.DataFrame({"date": [], "realtime_start": [], "value": []})
    df = pd.DataFrame(rows)[["date", "realtime_start", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df["realtime_start"] = pd.to_datetime(df["realtime_start"])
    return df.dropna().reset_index(drop=True)
