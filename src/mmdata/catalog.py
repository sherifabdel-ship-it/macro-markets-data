"""Series catalog: turns an id string into a full description (source, frequency, revision risk...).

Id formats
    TQQQ.adj_close        price field (any Yahoo ticker; fields below)
    FRED:GDPC1            FRED series that is listed in series.json
    WB:EGY:NY.GDP.MKTP.CD World Bank indicator (listed in series.json) for any country code
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PRICE_FIELDS = ["adj_close", "close", "open", "high", "low", "volume"]
RISKS = ("none", "low", "medium", "high")


@dataclass
class Series:
    series_id: str
    kind: str  # price | fred | wb
    name: str
    unit: str
    frequency: str
    revision_risk: str
    release_lag_days: int
    source: str
    notes: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class Catalog:
    fred: list[Series]
    wb_indicators: list[Series]
    countries: dict
    wb_notes: str


@lru_cache(maxsize=1)
def load() -> Catalog:
    raw = json.loads((Path(__file__).parent / "series.json").read_text())
    fred = [Series(f"FRED:{r['id']}", "fred", r["name"], r["unit"], r["frequency"], r["revision_risk"],
                   r["release_lag_days"], "fred", r.get("notes", ""), {"fred_id": r["id"]}) for r in raw["fred"]]
    wb = [Series(f"WB:{{country}}:{r['id']}", "wb", r["name"], r["unit"], r["frequency"], r["revision_risk"],
                 r["release_lag_days"], "worldbank", r.get("notes", ""), {"indicator": r["id"]})
          for r in raw["worldbank"]["indicators"]]
    for s in fred + wb:
        assert s.revision_risk in RISKS, s
    return Catalog(fred, wb, raw["worldbank"]["countries"], raw["worldbank"].get("notes", ""))


def resolve(series_id: str) -> Series:
    cat = load()
    if series_id.startswith("FRED:"):
        for s in cat.fred:
            if s.series_id == series_id:
                return s
        raise ValueError(f"{series_id}: not in catalog (add it to src/mmdata/series.json)")
    if series_id.startswith("WB:"):
        parts = series_id.split(":")
        if len(parts) != 3:
            raise ValueError(f"{series_id}: expected WB:<COUNTRY>:<INDICATOR>")
        _, country, indicator = parts
        for s in cat.wb_indicators:
            if s.params["indicator"] == indicator:
                label = cat.countries.get(country.upper(), country.upper())
                return Series(f"WB:{country.upper()}:{indicator}", "wb", f"{label} - {s.name}", s.unit,
                              s.frequency, s.revision_risk, s.release_lag_days, s.source,
                              s.notes + (" " + cat.wb_notes if cat.wb_notes else ""),
                              {"country": country.upper(), "indicator": indicator})
        raise ValueError(f"{series_id}: indicator not in catalog")
    if "." in series_id:
        ticker, fld = series_id.rsplit(".", 1)
        if ticker and fld in PRICE_FIELDS:
            return Series(f"{ticker.upper()}.{fld}", "price", f"{ticker.upper()} {fld.replace('_', ' ')}",
                          "shares" if fld == "volume" else "USD", "D", "none", 5, "yahoo",
                          "Market price. Split-adjusted; adj_close also dividend-adjusted.",
                          {"ticker": ticker.upper(), "field": fld})
    raise ValueError(f"{series_id}: unrecognised id. Use TICKER.field, FRED:<id> or WB:<COUNTRY>:<INDICATOR>")
