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
