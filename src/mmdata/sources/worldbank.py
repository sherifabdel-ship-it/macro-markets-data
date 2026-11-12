"""World Bank open data API (free, no key). Annual data for most countries."""
from __future__ import annotations

import pandas as pd

from ..http import SourceError, get_json


def fetch(country: str, indicator: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Columns: date (31 Dec of the data year), value."""
    data = get_json(f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
                    {"format": "json", "per_page": 1000, "date": f"{start.year - 1}:{end.year}"})
    if not isinstance(data, list) or len(data) < 2 or data[1] is None:
        msg = ""
        if isinstance(data, list) and data and isinstance(data[0], dict):
            msg = "; ".join(m.get("value", "") for m in data[0].get("message", []))
        raise SourceError(f"World Bank returned no data for {country}/{indicator}. {msg}".strip())
    rows = [(pd.Timestamp(int(r["date"]), 12, 31), r["value"]) for r in data[1] if r.get("value") is not None]
    return pd.DataFrame(rows, columns=["date", "value"])
