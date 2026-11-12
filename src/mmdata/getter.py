"""The getter: ask for series ids, get ONE standard long table cut at ONE moment in time."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from . import catalog, schema, timing, validate
from .sources import fred, stooq, worldbank, yahoo

# Registry so tests (or a future source) can swap implementations.
SOURCES = {"price": yahoo.fetch, "price_check": stooq.fetch, "fred": fred.fetch, "wb": worldbank.fetch}


@dataclass
class FetchResult:
    long: pd.DataFrame
    specs: list
    issues: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(i["level"] == "error" for i in self.issues)


def _issue(series: str, level: str, msg: str) -> dict:
    return {"series": series, "level": level, "msg": msg}


def fetch_series(series_ids, start, end=None, crosscheck: bool = True, now: datetime | None = None) -> FetchResult:
    now = now or datetime.now(timing.NY)
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize() if end is not None else pd.Timestamp(timing.last_complete_session_date(now))
    if start > end:
        raise ValueError("start must be on or before end")
    fetched_at = now.isoformat(timespec="seconds")

    specs, issues, frames = [], [], []
    for sid in dict.fromkeys(series_ids):
        try:
            specs.append(catalog.resolve(sid))
        except ValueError as e:
            issues.append(_issue(sid, "error", str(e)))

    # Prices: one request per ticker, fields cut from the same download.
    by_ticker: dict[str, list] = {}
    for s in specs:
        if s.kind == "price":
            by_ticker.setdefault(s.params["ticker"], []).append(s)
    for ticker, group in by_ticker.items():
        try:
            raw = SOURCES["price"](ticker, start, end)
            raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
            raw = raw.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
            for level, msg in validate.validate_prices(raw):
                issues.append(_issue(ticker, level, msg))
            if crosscheck:
                try:
                    level, msg = validate.crosscheck_close(raw, SOURCES["price_check"](ticker, start, end))
                    issues.append(_issue(ticker, level, msg))
                except Exception as e:
                    issues.append(_issue(ticker, "info", f"cross-check unavailable: {e}"))
            for s in group:
                frames.append(schema.to_long(raw.rename(columns={s.params["field"]: "value"}), s, fetched_at))
        except Exception as e:
            for s in group:
                issues.append(_issue(s.series_id, "error", f"fetch failed: {e}"))

    # Macro series: normalise dates to period END, then cut to the window.
    for s in specs:
        if s.kind == "price":
            continue
        try:
            if s.kind == "fred":
                raw = SOURCES["fred"](s.params["fred_id"], start, end)
            else:
                raw = SOURCES["wb"](s.params["country"], s.params["indicator"], start, end)
            raw = raw.copy()
            raw["date"] = timing.period_end(raw["date"], s.frequency)
            frames.append(schema.to_long(raw, s, fetched_at))
        except Exception as e:
            issues.append(_issue(s.series_id, "error", f"fetch failed: {e}"))

    long = pd.concat(frames, ignore_index=True) if frames else schema.empty()
    long = long[(long.date >= start) & (long.date <= end)].reset_index(drop=True)
    meta = {"as_of_new_york": fetched_at, "window_start": str(start.date()), "window_end": str(end.date()),
            "pandas": pd.__version__}
    return FetchResult(long, specs, issues, meta)
