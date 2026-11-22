import json
from datetime import datetime

import pandas as pd
import pytest

from mmdata import build, getter, revisions, timing
from mmdata.sources import fred

NOW = datetime(2026, 11, 12, 17, 0, tzinfo=timing.NY)
CFG = {"start": "2012-01-01", "tickers": ["TQQQ", "VIXY"], "price_fields": ["adj_close"],
       "fred": ["GDPC1", "XYZ123", "NOPE"], "worldbank": {"countries": ["EGY"], "indicators": ["NY.GDP.MKTP.CD"]}}


def _series(out, sid):
    return json.loads((out / "data" / "series" / f"{build.slug(sid)}.json").read_text())


def test_revision_summary_first_release_and_honest_blanks():
    from conftest import fake_vintages
    s = revisions.summarise(fake_vintages("X", pd.Timestamp("2010-01-01"), pd.Timestamp("2026-11-12")))
    old = s[s.date < "2014-10-01"]
    assert old.first_value.isna().all() and old.available_date.isna().all()      # pre-archive: unknown, not guessed
    new = s[s.date >= "2015-01-01"].iloc[:-2]
    assert (new.value / new.first_value).round(4).eq(1.01).all()
    assert ((new.available_date - (new.date + pd.offsets.QuarterEnd(0))).dt.days == 30).all()
    m = revisions.measure(s, "bn USD")
    assert m["label"] == "high" and m["basis"] == "measured" and m["typical_change"] == pytest.approx(1.0, abs=0.01)


def test_measure_labels():
    def mk(first, latest):
        n = len(first)
        return pd.DataFrame({"date": pd.date_range("2020-01-31", periods=n, freq="ME"), "value": latest,
                             "first_value": first, "available_date": pd.Timestamp("2021-01-01"), "n_versions": 2})
    assert revisions.measure(mk([100.0] * 20, [100.0] * 20))["label"] == "none"
    assert revisions.measure(mk([100.0] * 20, [100.05] * 20))["label"] == "low"        # often, but tiny
    assert revisions.measure(mk([100.0] * 20, [100.0] * 12 + [103.0] * 8))["label"] == "medium"
    assert revisions.measure(mk([2.0] * 20, [2.5] * 20), "%")["typical_change_unit"] == "points"
    assert revisions.measure(mk([1.0] * 3, [2.0] * 3))["label"] is None                 # too little history


def test_build_writes_site_with_first_release_and_measured_lag(fake_build_sources, tmp_path):
    site = tmp_path / "site"; site.mkdir(); (site / "index.html").write_text("<html></html>")
    m = build.build(CFG, tmp_path / "out", site, now=NOW)
    out = tmp_path / "out"
    assert (out / "index.html").exists() and (out / ".nojekyll").exists()
    by = {e["id"]: e for e in m["series"]}
    assert by["TQQQ.adj_close"]["status"] == "ok" and by["TQQQ.adj_close"]["revision"]["label"] == "none"
    g = _series(out, "FRED:GDPC1")
    assert g["has_first_release"] and g["revision"]["basis"] == "measured" and g["release_lag_days"] == 30
    assert g["dates"][0] == "2012-03-31" and len(g["dates"]) == len(g["values"]) == len(g["first_values"]) == len(g["available"])
    assert g["first_values"][0] is None and g["available"][-1] is not None
    assert by["FRED:XYZ123"]["name"] == "Fake XYZ123" and by["FRED:XYZ123"]["frequency"] == "M"   # uncatalogued id works
    assert by["FRED:NOPE"]["status"] == "failed" and "NOPE" in by["FRED:NOPE"]["issues"][0]["msg"]
    assert m["counts"] == {"ok": 5, "stale": 0, "failed": 1}
    assert "dates" not in by["FRED:GDPC1"]                                                      # manifest stays small
    assert "| FRED:NOPE | failed" in build.summary_markdown(m)


def test_build_keeps_last_good_data_when_a_source_fails(fake_build_sources, tmp_path, monkeypatch):
    build.build(CFG, tmp_path / "day1", now=NOW)

    def down(*a):
        raise RuntimeError("HTTP 429 rate limited")
    monkeypatch.setitem(getter.SOURCES, "price", down)
    m = build.build(CFG, tmp_path / "day2", previous=tmp_path / "day1", now=NOW)
    by = {e["id"]: e for e in m["series"]}
    assert by["TQQQ.adj_close"]["status"] == "stale" and "429" in by["TQQQ.adj_close"]["issues"][-1]["msg"]
    assert _series(tmp_path / "day2", "TQQQ.adj_close")["n"] > 1000
    m3 = build.build(CFG, tmp_path / "day3", previous=tmp_path / "day2", now=NOW)                # no pile-up of warnings
    assert sum("refresh failed" in i["msg"] for i in {e["id"]: e for e in m3["series"]}["TQQQ.adj_close"]["issues"]) == 1
    m4 = build.build(CFG, tmp_path / "day4", now=NOW)                                             # no previous copy
    assert {e["id"]: e for e in m4["series"]}["TQQQ.adj_close"]["status"] == "failed"


def test_build_falls_back_to_latest_values_without_archive(fake_build_sources, tmp_path, monkeypatch):
    def down(*a):
        raise RuntimeError("archive down")
    monkeypatch.setitem(getter.SOURCES, "fred_vintages", down)
    m = build.build(CFG, tmp_path / "o", now=NOW)
    g = {e["id"]: e for e in m["series"]}["FRED:GDPC1"]
    assert g["status"] == "ok" and not g["has_first_release"] and g["revision"] == {"label": "high", "basis": "curated"}


def test_cli_fails_only_when_nothing_fetched(fake_build_sources, tmp_path, monkeypatch):
    cfg = tmp_path / "w.json"; cfg.write_text(json.dumps(CFG))
    assert build.main(["--config", str(cfg), "--site", str(tmp_path / "none"), "--out", str(tmp_path / "o")]) == 0
    def down(*a, **k):
        raise RuntimeError("x")
    for k in list(getter.SOURCES):
        monkeypatch.setitem(getter.SOURCES, k, down)
    assert build.main(["--config", str(cfg), "--site", str(tmp_path / "none"), "--out", str(tmp_path / "o2")]) == 1


def test_fred_meta_and_vintage_parsing(monkeypatch):
    monkeypatch.setattr(fred, "get_json", lambda *a, **k: {"seriess": [{"title": "Thing", "frequency_short": "M", "units_short": "Index", "seasonal_adjustment_short": "SA"}]})
    assert fred.fetch_meta("X", key="k") == {"name": "Thing (SA)", "unit": "Index", "frequency": "M"}
    monkeypatch.setattr(fred, "get_json", lambda *a, **k: {"observations": [
        {"date": "2024-01-01", "realtime_start": "2024-04-25", "realtime_end": "2024-05-29", "value": "1.6"},
        {"date": "2024-01-01", "realtime_start": "2024-05-30", "realtime_end": "9999-12-31", "value": "1.3"},
        {"date": "2024-04-01", "realtime_start": "2024-07-25", "realtime_end": "9999-12-31", "value": "."}]})
    v = fred.fetch_vintages("X", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"), key="k")
    assert len(v) == 2 and v.realtime_start.iloc[0] == pd.Timestamp("2024-04-25")
