"""Streamlit dashboard: pick series -> pick timeline -> check coverage -> choose policy -> download.

Run:  streamlit run app/dashboard.py
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # works without pip install

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from mmdata import catalog, coverage, getter, schema, timing  # noqa: E402

RISK_LABEL = {"none": "none", "low": "low", "medium": "MEDIUM", "high": "HIGH - often revised"}
POLICY_LABEL = {
    "common": "Trim every series to the span ALL of them cover (recommended for comparison)",
    "full": "Keep my full window (short series will have blanks)",
}

st.set_page_config(page_title="macro-markets-data", layout="wide")
st.title("Macro & markets data getter")
st.caption("Data only. Every series arrives in one format, dated at period end, cut at one moment in time.")
cat = catalog.load()

# ---- 1. choose series ------------------------------------------------------------------------
st.header("1. Choose data")
c1, c2 = st.columns(2)
with c1:
    tickers = st.text_input("Tickers (comma-separated)", "VIXY, TQQQ")
    price_fields = st.multiselect("Price fields", catalog.PRICE_FIELDS, default=["adj_close"])
    fred_ids = st.multiselect(
        "Macro series (FRED)", [s.series_id for s in cat.fred],
        format_func=lambda i: next(f"{s.name}  [{s.frequency}, revision: {RISK_LABEL[s.revision_risk]}]"
                                   for s in cat.fred if s.series_id == i))
with c2:
    countries = st.multiselect("World Bank: countries", list(cat.countries), format_func=lambda c: f"{cat.countries[c]} ({c})")
    extra = st.text_input("Other World Bank country codes (e.g. KWT, QAT)", "")
    indicators = st.multiselect(
        "World Bank: indicators", [s.params["indicator"] for s in cat.wb_indicators],
        format_func=lambda i: next(s.name for s in cat.wb_indicators if s.params["indicator"] == i))

series_ids = [f"{t.strip().upper()}.{f}" for t in tickers.split(",") if t.strip() for f in price_fields]
series_ids += fred_ids
codes = countries + [c.strip().upper() for c in extra.split(",") if c.strip()]
series_ids += [f"WB:{c}:{i}" for c in codes for i in indicators]

# ---- 2. choose timeline ---------------------------------------------------------------------------
st.header("2. Choose timeline")
default_end = timing.last_complete_session_date()
d1, d2 = st.columns(2)
start = d1.date_input("From", default_end - timedelta(days=5 * 365))
end = d2.date_input("To", default_end)
use_check = st.checkbox("Cross-check prices against a second source (Stooq)", value=True)

if st.button("Fetch and check coverage", type="primary", disabled=not series_ids or start > end):
    with st.spinner("Fetching..."):
        st.session_state["result"] = getter.fetch_series(series_ids, start, end, crosscheck=use_check)

res = st.session_state.get("result")
if res is None:
    st.info("Pick at least one series and press the button. Nothing is downloaded to your computer yet.")
    st.stop()

# ---- 3. coverage -----------------------------------------------------------------------------------
st.header("3. Does every series cover the timeline?")
w_start, w_end = pd.Timestamp(res.meta["window_start"]), pd.Timestamp(res.meta["window_end"])
cov = coverage.coverage_report(res.long, res.specs, w_start, w_end)
if not cov.empty:
    show = cov.assign(revision_risk=cov.revision_risk.map(RISK_LABEL))
    st.dataframe(
        show.style.map(lambda v: {"OK": "background-color:#d4edda", "PARTIAL": "background-color:#fff3cd",
                                  "EMPTY": "background-color:#f8d7da"}.get(v, ""), subset=["status"]),
        hide_index=True, width="stretch")
    short = cov[cov.status != "OK"]
    if len(short):
        st.warning(f"{len(short)} of {len(cov)} series do not cover the whole timeline: "
                   + ", ".join(short.series_id) + ". See the detail column.")
    else:
        st.success("All series cover the whole timeline.")
    high = [s.series_id for s in res.specs if s.revision_risk in ("high", "medium")]
    if high:
        st.info("Revised often (values can change after publication): " + ", ".join(high))
for s in res.specs:
    if s.kind == "wb" and "fiscal year" in s.notes:
        st.info("World Bank note: " + s.notes.split(". ", 1)[-1])
        break

issues = pd.DataFrame(res.issues)
if not issues.empty:
    with st.expander(f"Data quality notes ({len(issues)})", expanded=res.has_errors):
        st.dataframe(issues, hide_index=True, width="stretch")

# ---- 4. policy + download -------------------------------------------------------------------------------
st.header("4. Extract")
policy = st.radio("Timeline policy", list(POLICY_LABEL), format_func=POLICY_LABEL.get)
out, (ws, we) = coverage.apply_window(res.long, res.specs, w_start, w_end, policy)
if out.empty:
    st.error("No dates are covered by all series at once. Choose 'Keep my full window' or drop the short series.")
    st.stop()
st.write(f"**Window delivered:** {ws.date()} to {we.date()} | {out.series_id.nunique()} series | {len(out):,} rows")
lim = coverage.limiting_series(res.long, res.specs)
if policy == "common" and lim:
    st.caption(f"Window start is set by {lim[0]} (first covers {lim[1].date()}); "
               f"window end is set by {lim[2]} (last value {lim[3].date()}). "
               "Drop those series, or choose 'Keep my full window', for a longer span.")

wide = schema.to_wide(out)
report = {"meta": {**res.meta, "policy": policy, "delivered_window": [str(ws.date()), str(we.date())]},
          "coverage": json.loads(cov.to_json(orient="records", date_format="iso")), "issues": res.issues}
b1, b2, b3 = st.columns(3)
b1.download_button("Long table (CSV)", out.to_csv(index=False, date_format="%Y-%m-%d"), "prices_long.csv", "text/csv")
b2.download_button("Wide table (CSV)", wide.to_csv(date_format="%Y-%m-%d"), "panel_wide.csv", "text/csv")
b3.download_button("Report (JSON)", json.dumps(report, indent=2, default=str), "report.json", "application/json")
st.caption("Wide table: one column per series, blank where a series has no value on that date "
           "(for example a quarterly series on a daily date). Use the long table to keep every row.")
