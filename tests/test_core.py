from datetime import datetime

import pandas as pd
import pytest
import requests

from mmdata import catalog, http, timing
from mmdata.sources import fred, worldbank

NY = timing.NY


# ---- timing -------------------------------------------------------------------------
def test_period_end_conversion():
    assert list(timing.period_end(["2024-01-01", "2024-04-01"], "Q")) == [pd.Timestamp("2024-03-31"), pd.Timestamp("2024-06-30")]
    assert list(timing.period_end(["2024-02-01"], "M")) == [pd.Timestamp("2024-02-29")]
    assert list(timing.period_end(["2023-01-01"], "A")) == [pd.Timestamp("2023-12-31")]
    assert list(timing.period_end(["2024-03-31"], "Q")) == [pd.Timestamp("2024-03-31")]  # already period end
    assert list(timing.period_end(["2024-03-05"], "D")) == [pd.Timestamp("2024-03-05")]


def test_period_start():
    assert timing.period_start(pd.Timestamp("2024-03-31"), "Q") == pd.Timestamp("2024-01-01")
    assert timing.period_start(pd.Timestamp("2024-02-29"), "M") == pd.Timestamp("2024-02-01")
    assert timing.period_start(pd.Timestamp("2023-12-31"), "A") == pd.Timestamp("2023-01-01")


def test_session_cutoff():
    f = timing.last_complete_session_date
    assert str(f(datetime(2026, 11, 12, 15, 0, tzinfo=NY))) == "2026-11-11"   # Thu, market not done
    assert str(f(datetime(2026, 11, 12, 17, 0, tzinfo=NY))) == "2026-11-12"   # Thu, after 16:30
    assert str(f(datetime(2026, 11, 14, 12, 0, tzinfo=NY))) == "2026-11-13"   # Saturday -> Friday


# ---- catalog ------------------------------------------------------------------------
def test_resolve_ids():
    p = catalog.resolve("tqqq.adj_close")
    assert (p.series_id, p.kind, p.revision_risk, p.frequency) == ("TQQQ.adj_close", "price", "none", "D")
    g = catalog.resolve("FRED:GDPC1")
    assert (g.kind, g.frequency, g.revision_risk) == ("fred", "Q", "high")
    w = catalog.resolve("WB:egy:NY.GDP.MKTP.CD")
    assert w.series_id == "WB:EGY:NY.GDP.MKTP.CD" and w.frequency == "A" and "Egypt" in w.name


@pytest.mark.parametrize("bad", ["FRED:NOPE", "WB:EGY", "WB:EGY:NOPE", "TQQQ", "TQQQ.bogus"])
def test_resolve_rejects(bad):
    with pytest.raises(ValueError):
        catalog.resolve(bad)


def test_catalog_flags_are_valid():
    cat = catalog.load()
    assert len(cat.fred) > 5 and all(s.revision_risk in catalog.RISKS for s in cat.fred)


# ---- http: key must never leak ----------------------------------------------------------
def test_redact():
    assert http.redact("url?api_key=SECRET123&x=1", "SECRET123") == "url?api_key=***&x=1"


def test_network_error_does_not_leak_key(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    def boom(url, params=None, timeout=None):
        raise requests.ConnectionError(f"failed {url}?api_key=SECRET123")

    monkeypatch.setattr(http.requests, "get", boom)
    with pytest.raises(http.SourceError) as e:
        http.get_json("https://x.test", {}, secrets=("SECRET123",))
    assert "SECRET123" not in str(e.value)


class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body or {}

    def json(self):
        return self._body


def test_client_error_not_retried_and_server_error_retried(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: (calls.append(1), _Resp(400, {"error_message": "bad key SECRET123"}))[1])
    with pytest.raises(http.SourceError) as e:
        http.get_json("u", {}, secrets=("SECRET123",))
    assert len(calls) == 1 and "SECRET123" not in str(e.value) and "bad key" in str(e.value)

    calls.clear()
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: (calls.append(1), _Resp(503))[1])
    with pytest.raises(http.SourceError):
        http.get_json("u", {}, tries=3)
    assert len(calls) == 3


# ---- FRED / World Bank parsing --------------------------------------------------------------
def test_fred_parsing_drops_missing(monkeypatch):
    monkeypatch.setattr(fred, "get_json", lambda *a, **k: {"observations": [
        {"date": "2024-01-01", "value": "100.5"}, {"date": "2024-04-01", "value": "."}]})
    df = fred.fetch("GDPC1", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"), key="k")
    assert len(df) == 1 and df.value.iloc[0] == 100.5


def test_fred_missing_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "load_dotenv", lambda *a, **k: None)
    with pytest.raises(http.SourceError, match="FRED_API_KEY"):
        fred.get_key()


def test_fred_error_payload(monkeypatch):
    monkeypatch.setattr(fred, "get_json", lambda *a, **k: {"error_message": "Bad Request. Series does not exist."})
    with pytest.raises(http.SourceError, match="does not exist"):
        fred.fetch("NOPE", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"), key="k")


def test_worldbank_parsing_and_error(monkeypatch):
    ok = [{"page": 1}, [{"date": "2022", "value": 3.1}, {"date": "2023", "value": None}]]
    monkeypatch.setattr(worldbank, "get_json", lambda *a, **k: ok)
    df = worldbank.fetch("EGY", "NY.GDP.MKTP.KD.ZG", pd.Timestamp("2020-01-01"), pd.Timestamp("2024-01-01"))
    assert list(df.date) == [pd.Timestamp("2022-12-31")] and df.value.iloc[0] == 3.1

    err = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    monkeypatch.setattr(worldbank, "get_json", lambda *a, **k: err)
    with pytest.raises(http.SourceError, match="not valid"):
        worldbank.fetch("XXX", "NY.GDP.MKTP.CD", pd.Timestamp("2020-01-01"), pd.Timestamp("2024-01-01"))
