from datetime import datetime

import numpy as np
import pandas as pd

from mmdata import catalog, coverage, getter, schema, timing
from mmdata.getter import fetch_series

NOW = datetime(2026, 11, 12, 17, 0, tzinfo=timing.NY)
IDS = ["TQQQ.adj_close", "VIXY.close", "FRED:GDPC1", "WB:EGY:NY.GDP.MKTP.CD"]


def test_standard_schema_one_timestamp_period_end_dates(fake_sources):
    r = fetch_series(IDS, "2022-01-01", "2026-11-12", now=NOW)
    assert list(r.long.columns) == schema.COLUMNS
    assert r.long.fetched_at.nunique() == 1                      # same timing for every series
    g = r.long[r.long.series_id == "FRED:GDPC1"].date
    assert all(d == d + pd.offsets.QuarterEnd(0) for d in g)       # quarter ENDS
    assert r.long.date.min() >= pd.Timestamp("2022-01-01") and r.long.date.max() <= pd.Timestamp("2026-11-12")
    assert not r.has_errors


def test_price_fields_share_one_download(fake_sources, monkeypatch):
    calls = []
    orig = getter.SOURCES["price"]
    monkeypatch.setitem(getter.SOURCES, "price", lambda *a: (calls.append(a), orig(*a))[1])
    fetch_series(["TQQQ.close", "TQQQ.adj_close", "TQQQ.volume"], "2024-01-01", "2024-06-30", now=NOW)
    assert len(calls) == 1


def test_failures_are_reported_not_fatal(fake_sources, monkeypatch):
    def boom(*a):
        raise RuntimeError("nope")
    monkeypatch.setitem(getter.SOURCES, "fred", boom)
    r = fetch_series(IDS + ["FRED:NOT_IN_CATALOG"], "2022-01-01", "2026-11-12", now=NOW)
    bad = {i["series"] for i in r.issues if i["level"] == "error"}
    assert bad == {"FRED:GDPC1", "FRED:NOT_IN_CATALOG"}
    assert "TQQQ.adj_close" in set(r.long.series_id)               # the others still arrive


def test_crosscheck_unavailable_is_info_only(fake_sources, monkeypatch):
    def boom(*a):
        raise RuntimeError("blocked")
    monkeypatch.setitem(getter.SOURCES, "price_check", boom)
    r = fetch_series(["TQQQ.close"], "2024-01-01", "2024-06-30", now=NOW)
    assert not r.has_errors and any("cross-check unavailable" in i["msg"] for i in r.issues)


def test_coverage_flags_short_series_and_pending_release(fake_sources, monkeypatch):
    # GDP only starts in 2024; window starts 2022 -> must be flagged PARTIAL
    def late_fred(fid, s, e):
        d = pd.date_range("2024-01-01", e, freq="QS")
        return pd.DataFrame({"date": d, "value": np.arange(len(d), dtype=float)})
    monkeypatch.setitem(getter.SOURCES, "fred", late_fred)
    r = fetch_series(IDS, "2022-01-01", "2026-11-12", now=NOW)
    cov = coverage.coverage_report(r.long, r.specs, "2022-01-01", "2026-11-12").set_index("series_id")
    assert cov.loc["FRED:GDPC1", "status"] == "PARTIAL" and "starts" in cov.loc["FRED:GDPC1", "detail"]
    assert cov.loc["TQQQ.adj_close", "status"] == "OK"
    # World Bank latest year missing is within its release lag -> OK with a note
    assert cov.loc["WB:EGY:NY.GDP.MKTP.CD", "status"] == "OK"
    assert "not yet published" in cov.loc["WB:EGY:NY.GDP.MKTP.CD", "detail"]


def test_coverage_ends_early_gap_and_empty(fake_sources, monkeypatch):
    def stale(fid, s, e):  # stopped publishing in 2023
        d = pd.date_range("2020-01-01", "2023-01-01", freq="QS")
        return pd.DataFrame({"date": d, "value": np.arange(len(d), dtype=float)})
    monkeypatch.setitem(getter.SOURCES, "fred", stale)
    r = fetch_series(["FRED:GDPC1", "TQQQ.close"], "2020-01-01", "2026-11-12", now=NOW)
    cov = coverage.coverage_report(r.long, r.specs, "2020-01-01", "2026-11-12").set_index("series_id")
    assert cov.loc["FRED:GDPC1", "status"] == "PARTIAL" and "ends" in cov.loc["FRED:GDPC1", "detail"]

    gappy = r.long[~((r.long.series_id == "TQQQ.close") & (r.long.date.between("2022-01-01", "2022-03-01")))]
    cov = coverage.coverage_report(gappy, r.specs, "2020-01-01", "2026-11-12").set_index("series_id")
    assert "internal gap" in cov.loc["TQQQ.close", "detail"]

    cov = coverage.coverage_report(r.long.iloc[0:0], r.specs, "2020-01-01", "2026-11-12")
    assert set(cov.status) == {"EMPTY"}


def test_apply_window_common_vs_full(fake_sources, monkeypatch):
    def late_fred(fid, s, e):
        d = pd.date_range("2024-01-01", "2025-07-01", freq="QS")  # covers Jan 2024 - Sep 2025
        return pd.DataFrame({"date": d, "value": np.arange(len(d), dtype=float)})
    monkeypatch.setitem(getter.SOURCES, "fred", late_fred)
    r = fetch_series(["TQQQ.adj_close", "FRED:GDPC1"], "2022-01-01", "2026-11-12", now=NOW)

    full, (fs, fe) = coverage.apply_window(r.long, r.specs, "2022-01-01", "2026-11-12", "full")
    assert fs == pd.Timestamp("2022-01-01") and full.date.min() < pd.Timestamp("2024-01-01")

    common, (cs, ce) = coverage.apply_window(r.long, r.specs, "2022-01-01", "2026-11-12", "common")
    assert cs == pd.Timestamp("2024-01-01") and ce == pd.Timestamp("2025-09-30")
    assert common.date.min() >= cs and common.date.max() <= ce
    assert set(common.series_id) == {"TQQQ.adj_close", "FRED:GDPC1"}


def test_apply_window_ignores_empty_series_and_handles_no_overlap(fake_sources):
    r = fetch_series(["TQQQ.adj_close", "FRED:GDPC1"], "2024-01-01", "2024-12-31", now=NOW)
    only_prices = r.long[r.long.series_id == "TQQQ.adj_close"]          # GDP came back empty
    kept, (cs, ce) = coverage.apply_window(only_prices, r.specs, "2024-01-01", "2024-12-31", "common")
    assert len(kept) == len(only_prices)

    a = r.long[r.long.series_id == "TQQQ.adj_close"].head(5)
    b = r.long[r.long.series_id == "TQQQ.adj_close"].tail(5).assign(series_id="FRED:GDPC1")
    none, _ = coverage.apply_window(pd.concat([a, b]), r.specs, "2024-01-01", "2024-12-31", "common")
    assert none.empty


def test_wide_table_has_blanks_where_short(fake_sources):
    r = fetch_series(IDS, "2022-01-01", "2026-11-12", now=NOW)
    w = schema.to_wide(r.long)
    assert set(w.columns) == set(IDS) and w["FRED:GDPC1"].isna().sum() > 0


def test_limiting_series_names_the_culprit(fake_sources):
    r = fetch_series(IDS, "2022-01-01", "2026-11-12", now=NOW)
    s_id, s_date, e_id, e_date = coverage.limiting_series(r.long, r.specs)
    assert e_id == "WB:EGY:NY.GDP.MKTP.CD" and e_date == pd.Timestamp("2024-12-31")
    assert coverage.limiting_series(r.long.iloc[0:0], r.specs) is None
