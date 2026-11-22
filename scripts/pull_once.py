"""One-off pull: daily close and adjusted close since TQQQ inception for TQQQ, QQQ, ^NDX, ^XNDX."""
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

TICKERS = {"TQQQ": "TQQQ", "QQQ": "QQQ", "NDX": "^NDX", "NDX_TR": "^XNDX"}
out = Path("data_pull"); out.mkdir(exist_ok=True)
cols, log = {}, []
for name, t in TICKERS.items():
    for i in range(5):
        try:
            df = yf.Ticker(t).history(start="2010-01-01", interval="1d", auto_adjust=False, actions=True)
            if df is None or df.empty:
                raise ValueError("empty")
            break
        except Exception as e:
            log.append(f"{t} attempt {i+1}: {e}")
            time.sleep(10 * (i + 1))
    else:
        log.append(f"{t}: FAILED"); continue
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    cols[f"{name}_close"] = df["Close"]
    cols[f"{name}_adj_close"] = df["Adj Close"]
    df[["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"]].to_csv(out / f"{name}.csv", date_format="%Y-%m-%d")
    log.append(f"{t}: {len(df)} rows {df.index.min().date()} to {df.index.max().date()}, dividends {int((df['Dividends'] > 0).sum())}, splits {int((df['Stock Splits'] > 0).sum())}")
wide = pd.DataFrame(cols).sort_index()
wide.index.name = "date"
wide.to_csv(out / "combined.csv", date_format="%Y-%m-%d")
(out / "log.txt").write_text("\n".join(log) + f"\nyfinance {yf.__version__}\n")
print("\n".join(log))
