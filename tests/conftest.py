"""Fake sources so tests (and the dashboard smoke test) never touch the network."""
import numpy as np
import pandas as pd
import pytest

from mmdata import getter


def fake_prices(ticker, start, end):
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(abs(hash(ticker)) % 2**32)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    return pd.DataFrame({"date": days, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "adj_close": c * 0.98, "volume": 1000})


def fake_close_check(ticker, start, end):
    return fake_prices(ticker, start, end)[["date", "close"]]


def fake_fred(fred_id, start, end):
    # FRED style: quarterly obs dated at period START
    d = pd.date_range(start - pd.DateOffset(years=1), end, freq="QS")
    return pd.DataFrame({"date": d, "value": np.arange(len(d), dtype=float) + 100})


def fake_wb(country, indicator, start, end):
    d = [pd.Timestamp(y, 12, 31) for y in range(start.year - 1, end.year - 1)]  # last full year not yet published
    return pd.DataFrame({"date": d, "value": np.arange(len(d), dtype=float) + 1})


@pytest.fixture
def fake_sources(monkeypatch):
    monkeypatch.setitem(getter.SOURCES, "price", fake_prices)
    monkeypatch.setitem(getter.SOURCES, "price_check", fake_close_check)
    monkeypatch.setitem(getter.SOURCES, "fred", fake_fred)
    monkeypatch.setitem(getter.SOURCES, "wb", fake_wb)


def fake_vintages(fred_id, start, end):
    """Quarterly series: archive starts 2015-01-15. Each later quarter is first published ~30 days
    after quarter end, then revised +1% sixty days later."""
    rows = []
    archive0 = pd.Timestamp("2015-01-15")
    for i, d in enumerate(pd.date_range("2010-01-01", end, freq="QS")):
        qend = d + pd.offsets.QuarterEnd(0)
        first_pub = qend + pd.Timedelta(days=30)
        if first_pub <= archive0:
            rows.append((d, archive0, 100.0 + i))
        elif first_pub <= end:
            rows.append((d, first_pub, 100.0 + i))
            if first_pub + pd.Timedelta(days=60) <= end:
                rows.append((d, first_pub + pd.Timedelta(days=60), (100.0 + i) * 1.01))
    return pd.DataFrame(rows, columns=["date", "realtime_start", "value"])


def fake_meta(fred_id):
    if fred_id == "NOPE":
        raise ValueError("FRED has no series called NOPE")
    return {"name": f"Fake {fred_id}", "unit": "Index", "frequency": "M"}


@pytest.fixture
def fake_build_sources(fake_sources, monkeypatch):
    monkeypatch.setitem(getter.SOURCES, "fred_vintages", fake_vintages)
    monkeypatch.setitem(getter.SOURCES, "fred_meta", fake_meta)
