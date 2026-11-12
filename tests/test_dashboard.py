"""Smoke test: drive the real dashboard with fake sources."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "dashboard.py")


def test_dashboard_end_to_end(fake_sources):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    at.multiselect[1].set_value(["FRED:GDPC1"]).run()          # macro series
    at.button[0].click().run()                                   # fetch and check coverage
    assert not at.exception, at.exception
    headers = [h.value for h in at.header]
    assert any("cover the timeline" in h for h in headers) and any("Extract" in h for h in headers)
    assert len(at.dataframe) >= 1                                # coverage table shown


def test_dashboard_blocks_when_nothing_selected(fake_sources):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input[0].set_value("").run()
    assert at.button[0].disabled
