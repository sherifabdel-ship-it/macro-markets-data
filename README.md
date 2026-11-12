# macro-markets-data

Fetches **market prices and economic data in one standard format**, cut at one moment in time, so different series can be compared. **Data only** - analysis happens elsewhere.

## Quick start

```bash
git clone https://github.com/sherifabdel-ship-it/macro-markets-data
cd macro-markets-data
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then paste your free FRED key into .env
streamlit run app/dashboard.py
```

Free FRED key: <https://fred.stlouisfed.org> -> My Account -> API Keys. The `.env` file is git-ignored; never commit it.
Prices (Yahoo) and World Bank data need no key.

## How the dashboard works

1. **Choose data** - tickers and price fields, FRED macro series, World Bank indicators for any countries.
2. **Choose timeline** - one From/To window applies to everything.
3. **Check coverage** - a table shows, for each series, first/last date, % of the window covered, gaps, and its **revision risk**. Series that do not cover the window are flagged.
4. **Extract** - choose a policy, then download CSV (long and wide) and a JSON report.
   - *Trim to common span* (recommended): every series is cut to the span all of them cover. The dashboard names the series that limits the span.
   - *Keep full window*: nothing is trimmed; short series have blanks.

## Conventions (what makes series comparable)

| Rule | Detail |
|---|---|
| One format | `date, series_id, value, unit, frequency, source, revision_risk, fetched_at` |
| Period-END dates | A quarterly value is dated 31 Mar for Q1, monthly values at month end, annual at 31 Dec. FRED's period-start dates are converted. |
| One timestamp | Every row in a run shares `fetched_at` (New York time). Today's price bar is dropped until 16:30 New York time. |
| Revision flag | `none / low / medium / high` per series, shown next to the data. GDP is `high`. Latest published values are delivered, **not** first releases. |
| Price adjusting | `close` is split-adjusted. `adj_close` is split + dividend adjusted. |
| Quality checks | Duplicates, non-positive prices, high/low sanity, stale prices, extreme moves, and a cross-check of close against Stooq. |
| Keys never leak | API keys are redacted from every error message. |

Series ids: `TQQQ.adj_close`, `FRED:GDPC1`, `WB:EGY:NY.GDP.MKTP.CD`.
Add macro series by editing `src/mmdata/series.json` (set `frequency`, `revision_risk`, `release_lag_days`).

## Use from Python

```python
from mmdata import fetch_series, coverage_report, apply_window
res = fetch_series(["TQQQ.adj_close", "VIXY.close", "FRED:GDPC1"], "2020-01-01")
print(coverage_report(res.long, res.specs, res.meta["window_start"], res.meta["window_end"]))
```

## Project layout

```
macro-markets-data/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example               # template; copy to .env (git-ignored)
├── .gitignore
├── .github/workflows/tests.yml
├── app/
│   └── dashboard.py           # Streamlit UI
├── src/mmdata/
│   ├── __init__.py
│   ├── catalog.py             # series ids -> frequency, revision risk, lag
│   ├── series.json            # FRED + World Bank catalog (edit to add series)
│   ├── schema.py              # the standard long format
│   ├── timing.py              # period-end dates, session cutoff
│   ├── getter.py              # fetch_series(): the main entry point
│   ├── coverage.py            # coverage report, window policies
│   ├── validate.py            # price quality checks
│   ├── http.py                # retries, key redaction
│   └── sources/
│       ├── yahoo.py
│       ├── stooq.py           # cross-check only
│       ├── fred.py
│       └── worldbank.py
└── tests/                     # run: pytest
```

## Status and known limits

- Tests use fake data. **Run the dashboard once with your key to confirm the live sources**; some catalog ids (for example the FRED delinquency series) should be confirmed on that first run.
- Revision risk labels in `series.json` are initial judgements - review them.
- First-release values and revision history (FRED's ALFRED) are not stored yet.
- No frequency conversion: a quarterly series stays quarterly. Choosing how to align them is a separate step.
