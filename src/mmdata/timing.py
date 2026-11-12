"""Shared timing rules so every series is cut at the same moment and dated the same way."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")

_END_OFFSETS = {"M": pd.offsets.MonthEnd(0), "Q": pd.offsets.QuarterEnd(0), "A": pd.offsets.YearEnd(0)}
_MONTHS = {"M": 1, "Q": 3, "A": 12}


def last_complete_session_date(now: datetime | None = None) -> date:
    """Latest date whose daily bar is finished: today only after 16:30 New York time on a weekday."""
    now = (now or datetime.now(NY)).astimezone(NY)
    done_today = now.weekday() < 5 and (now.hour, now.minute) >= (16, 30)
    return now.date() if done_today else now.date() - timedelta(days=1)


def period_end(dates, freq: str) -> pd.DatetimeIndex:
    """Convert period-start dates (FRED style: 2024-01-01 for Q1) to period-END dates (2024-03-31).
    Daily and weekly series are left alone. Already-period-end dates are unchanged."""
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    return idx + _END_OFFSETS[freq] if freq in _END_OFFSETS else idx


def period_start(end_date: pd.Timestamp, freq: str) -> pd.Timestamp:
    """First day covered by an observation dated at a period end."""
    if freq not in _MONTHS:
        return pd.Timestamp(end_date)
    return (pd.Timestamp(end_date) + pd.Timedelta(days=1)) - pd.DateOffset(months=_MONTHS[freq])
