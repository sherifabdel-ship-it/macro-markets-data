"""Quality checks for price data. Each check returns (level, message); levels: error, warn, info."""
from __future__ import annotations

import pandas as pd

PRICE_COLS = ["open", "high", "low", "close", "adj_close"]


def validate_prices(df: pd.DataFrame, max_move: float = 0.40, stale_run: int = 5) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if df.empty:
        return [("error", "no rows")]
    if df["date"].duplicated().any():
        out.append(("error", "duplicate dates"))
    if df[PRICE_COLS].isna().any().any():
        out.append(("error", "missing price values"))
    if (df[PRICE_COLS] <= 0).any().any():
        out.append(("error", "non-positive prices present"))
    bad = df[(df.high < df[["open", "close"]].max(axis=1) - 1e-6) | (df.low > df[["open", "close"]].min(axis=1) + 1e-6)]
    if len(bad):
        out.append(("warn", f"{len(bad)} rows where high/low do not bracket open/close, e.g. {bad.date.iloc[0].date()}"))
    big = df[df.adj_close.pct_change().abs() > max_move]
    if len(big):
        days = ", ".join(str(d.date()) for d in big.date.head(5))
        out.append(("warn", f"{len(big)} daily moves beyond +/-{max_move:.0%} (real event or split issue?): {days}"))
    flat = (df.close.diff() == 0).astype(int)
    longest = int(flat.groupby((flat == 0).cumsum()).cumsum().max())
    if longest >= stale_run:
        out.append(("warn", f"stale price: close unchanged for {longest}+ consecutive days"))
    if (df.volume == 0).mean() > 0.02:
        out.append(("warn", f"{(df.volume == 0).mean():.1%} of days have zero volume"))
    return out


def crosscheck_close(df: pd.DataFrame, other: pd.DataFrame, tol: float = 0.005) -> tuple[str, str]:
    m = df[["date", "close"]].merge(other, on="date", suffixes=("", "_2"))
    if m.empty:
        return ("warn", "cross-check: no overlapping dates with second source")
    diff = (m.close / m.close_2 - 1).abs()
    n_bad = int((diff > tol).sum())
    worst = m.loc[diff.idxmax(), "date"].date()
    if n_bad:
        return ("warn", f"cross-check vs Stooq: {n_bad}/{len(m)} days differ by more than {tol:.1%} (worst {worst}, {diff.max():.2%})")
    return ("info", f"cross-check vs Stooq: {len(m)} days agree within {tol:.1%} (max {diff.max():.3%})")
