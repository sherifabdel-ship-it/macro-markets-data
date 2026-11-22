"""Build the online dashboard's data: fetch every series on the watchlist and write static JSON.

Run on GitHub's servers by .github/workflows/update-data.yml:
    python -m mmdata.build --config config/watchlist.json --site site --out _site --previous _previous

Rules
 - one moment in time: every series in a build shares one cut-off date and one timestamp
 - never lose good data: if a source fails today, yesterday's copy is kept and marked "stale"
 - nothing is hidden: every problem is written into the manifest and shown on the dashboard
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import catalog, revisions, timing, validate
from .getter import SOURCES

DEFAULT_LAG = {"D": 2, "W": 7, "M": 30, "Q": 45, "A": 180}


def slug(series_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", series_id).strip("_")


def expand(config: dict) -> list[str]:
    ids = [f"{t.strip().upper()}.{f}" for t in config.get("tickers", []) for f in config.get("price_fields", ["adj_close"])]
    ids += [f"FRED:{i.strip().upper()}" for i in config.get("fred", [])]
    wb = config.get("worldbank", {})
    ids += [f"WB:{c.strip().upper()}:{i}" for c in wb.get("countries", []) for i in wb.get("indicators", [])]
    return list(dict.fromkeys(ids))


def resolve_any(series_id: str) -> catalog.Series:
    """Catalogued series resolve offline; any other FRED id is looked up on FRED itself."""
    try:
        return catalog.resolve(series_id)
    except ValueError:
        if not series_id.startswith("FRED:"):
            raise
    fid = series_id.split(":", 1)[1]
    m = SOURCES["fred_meta"](fid)
    return catalog.Series(series_id, "fred", m["name"], m["unit"], m["frequency"], "unknown",
                          DEFAULT_LAG[m["frequency"]], "fred", "Not in the curated catalog; details read from FRED.",
                          {"fred_id": fid}, "mean")


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 6)


def _day(x):
    return None if pd.isna(x) else pd.Timestamp(x).date().isoformat()


def _payload(spec, df: pd.DataFrame, fetched_at: str, revision: dict, lag: int, lag_basis: str, issues: list) -> dict:
    """df columns: date, value and optionally first_value, available_date."""
    has_first = "first_value" in df and df["first_value"].notna().any()
    p = {
        "id": spec.series_id, "slug": slug(spec.series_id), "name": spec.name, "unit": spec.unit,
        "frequency": spec.frequency, "source": spec.source, "kind": spec.kind, "agg": spec.agg, "suggest": spec.suggest,
        "revision": revision, "release_lag_days": int(lag), "lag_basis": lag_basis, "notes": spec.notes,
        "status": "ok", "fetched_at": fetched_at, "n": int(len(df)),
        "first": _day(df["date"].min()) if len(df) else None, "last": _day(df["date"].max()) if len(df) else None,
        "has_first_release": bool(has_first), "issues": issues,
        "dates": [_day(d) for d in df["date"]], "values": [_num(v) for v in df["value"]],
    }
    if has_first:
        p["first_values"] = [_num(v) for v in df["first_value"]]
        p["available"] = [_day(d) for d in df["available_date"]]
    return p


def _clean(df: pd.DataFrame, start, end) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.dropna(subset=["value"]).drop_duplicates("date", keep="last").sort_values("date")
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def build_prices(ticker: str, specs: list, start, end, fetched_at: str, crosscheck: bool) -> list[dict]:
    raw = SOURCES["price"](ticker, start, end)
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    raw = raw.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    issues = [{"level": lv, "msg": msg} for lv, msg in validate.validate_prices(raw)]
    if crosscheck:
        try:
            lv, msg = validate.crosscheck_close(raw, SOURCES["price_check"](ticker, start, end))
            issues.append({"level": lv, "msg": msg})
        except Exception as e:
            issues.append({"level": "info", "msg": f"second-source cross-check unavailable: {str(e)[:120]}"})
    rev = {"label": "none", "basis": "market price"}
    return [_payload(s, _clean(raw.rename(columns={s.params["field"]: "value"})[["date", "value"]], start, end),
                     fetched_at, rev, 0, "market price", list(issues)) for s in specs]


def build_fred(spec, start, end, fetched_at: str) -> dict:
    fid, issues = spec.params["fred_id"], []
    curated = {"label": spec.revision_risk, "basis": "curated"}
    lag, lag_basis = spec.release_lag_days, "typical"
    try:
        s = revisions.summarise(SOURCES["fred_vintages"](fid, start, end))
        if s.empty:
            raise ValueError("archive returned nothing")
        s["date"] = timing.period_end(s["date"], spec.frequency)
        m = revisions.measure(s, spec.unit)
        rev = m if m.get("label") else {**curated, "note": m.get("basis", "")}
        known = s.dropna(subset=["available_date"])
        if len(known) >= 8:
            lag = max(int((known["available_date"] - known["date"]).dt.days.median()), 0)
            lag_basis = "measured"
        df = s
    except Exception as e:
        issues.append({"level": "info", "msg": f"revision archive unavailable, latest values only: {str(e)[:120]}"})
        df = SOURCES["fred"](fid, start, end)
        df["date"] = timing.period_end(df["date"], spec.frequency)
        rev = curated
    return _payload(spec, _clean(df, start, end), fetched_at, rev, lag, lag_basis, issues)


def build_wb(spec, start, end, fetched_at: str) -> dict:
    df = SOURCES["wb"](spec.params["country"], spec.params["indicator"], start, end)
    df["date"] = timing.period_end(df["date"], spec.frequency)
    return _payload(spec, _clean(df, start, end), fetched_at, {"label": spec.revision_risk, "basis": "curated"},
                    spec.release_lag_days, "typical", [])


def _failed(series_id: str, msg: str, previous: Path | None, spec=None) -> dict:
    """Keep yesterday's copy if there is one; otherwise record the failure."""
    if previous is not None:
        old = previous / "data" / "series" / f"{slug(series_id)}.json"
        if old.exists():
            p = json.loads(old.read_text())
            if p.get("n"):
                p["status"] = "stale"
                p["issues"] = [i for i in p.get("issues", []) if not i["msg"].startswith("today's refresh failed")]
                p["issues"].append({"level": "warn", "msg": f"today's refresh failed, showing data fetched {p.get('fetched_at', '?')}: {msg}"})
                return p
    return {"id": series_id, "slug": slug(series_id), "name": spec.name if spec else series_id,
            "unit": spec.unit if spec else "", "frequency": spec.frequency if spec else "",
            "source": spec.source if spec else "", "kind": spec.kind if spec else "", "agg": spec.agg if spec else "",
            "revision": {"label": "unknown", "basis": ""}, "release_lag_days": 0, "lag_basis": "", "notes": "",
            "status": "failed", "fetched_at": None, "n": 0, "first": None, "last": None, "has_first_release": False,
            "issues": [{"level": "error", "msg": msg}], "dates": [], "values": []}


def build(config: dict, out: Path, site: Path | None = None, previous: Path | None = None,
          now: datetime | None = None, crosscheck: bool = True) -> dict:
    now = now or datetime.now(timing.NY)
    start = pd.Timestamp(config.get("start", "2005-01-01")).normalize()
    end = pd.Timestamp(timing.last_complete_session_date(now))
    fetched_at = now.isoformat(timespec="seconds")
    payloads: dict[str, dict] = {}
    by_ticker: dict[str, list] = {}
    others = []
    for sid in expand(config):
        try:
            spec = resolve_any(sid)
        except Exception as e:
            payloads[sid] = _failed(sid, str(e)[:200], previous)
            continue
        if spec.kind == "price":
            by_ticker.setdefault(spec.params["ticker"], []).append(spec)
        else:
            others.append(spec)
        payloads[spec.series_id] = None  # keep watchlist order
    for ticker, specs in by_ticker.items():
        try:
            for p in build_prices(ticker, specs, start, end, fetched_at, crosscheck):
                payloads[p["id"]] = p if p["n"] else _failed(p["id"], "source returned no rows", previous, specs[0])
        except Exception as e:
            for s in specs:
                payloads[s.series_id] = _failed(s.series_id, str(e)[:200], previous, s)
    for spec in others:
        try:
            p = build_fred(spec, start, end, fetched_at) if spec.kind == "fred" else build_wb(spec, start, end, fetched_at)
            payloads[spec.series_id] = p if p["n"] else _failed(spec.series_id, "source returned no rows", previous, spec)
        except Exception as e:
            payloads[spec.series_id] = _failed(spec.series_id, str(e)[:200], previous, spec)

    if site is not None:
        shutil.copytree(site, out, dirs_exist_ok=True)
    (out / "data" / "series").mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    entries = []
    for p in payloads.values():
        (out / "data" / "series" / f"{p['slug']}.json").write_text(json.dumps(p, separators=(",", ":")))
        entries.append({k: v for k, v in p.items() if k not in ("dates", "values", "first_values", "available")})
    counts = {s: sum(e["status"] == s for e in entries) for s in ("ok", "stale", "failed")}
    manifest = {
        "generated_at": fetched_at, "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_start": str(start.date()), "window_end": str(end.date()), "counts": counts,
        "repo": os.getenv("GITHUB_REPOSITORY", ""), "config": config, "series": entries,
    }
    (out / "data" / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def summary_markdown(m: dict) -> str:
    lines = [f"### Data build {m['generated_at']} (New York)",
             f"Window {m['window_start']} to {m['window_end']} | ok {m['counts']['ok']} | stale {m['counts']['stale']} | failed {m['counts']['failed']}",
             "", "| series | status | rows | first | last | revisions | notes |", "|---|---|---|---|---|---|---|"]
    for e in m["series"]:
        notes = "; ".join(f"[{i['level']}] {i['msg']}" for i in e["issues"])[:300].replace("|", "/")
        lines.append(f"| {e['id']} | {e['status']} | {e['n']} | {e['first']} | {e['last']} | {e['revision'].get('label')} ({e['revision'].get('basis')}) | {notes} |")
    return "\n".join(lines)


def download_previous(url: str, dest: Path) -> int:
    """Copy the currently published data so a failed source can fall back to it. Never fatal."""
    import requests
    url = url.rstrip("/")
    try:
        r = requests.get(f"{url}/data/manifest.json", timeout=30)
        if r.status_code != 200:
            print(f"no previous site at {url} (HTTP {r.status_code}); first build")
            return 0
        n = 0
        (dest / "data" / "series").mkdir(parents=True, exist_ok=True)
        for e in r.json().get("series", []):
            if not re.fullmatch(r"[A-Za-z0-9_]+", e.get("slug", "")):
                continue
            s = requests.get(f"{url}/data/series/{e['slug']}.json", timeout=30)
            if s.status_code == 200:
                (dest / "data" / "series" / f"{e['slug']}.json").write_text(s.text)
                n += 1
        print(f"downloaded {n} previous series from {url}")
        return n
    except Exception as e:
        print(f"could not download previous site: {e}")
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the dashboard's data files.")
    ap.add_argument("--config", default="config/watchlist.json")
    ap.add_argument("--site", default="site")
    ap.add_argument("--out", default="_site")
    ap.add_argument("--previous", default=None, help="folder holding the previously published site (for stale fallback)")
    ap.add_argument("--previous-url", default=None, help="URL of the live site; its data is downloaded for stale fallback")
    ap.add_argument("--no-crosscheck", action="store_true")
    a = ap.parse_args(argv)
    config = json.loads(Path(a.config).read_text())
    if a.previous_url and a.previous:
        download_previous(a.previous_url, Path(a.previous))
    prev = Path(a.previous) if a.previous and Path(a.previous).exists() else None
    m = build(config, Path(a.out), Path(a.site) if Path(a.site).exists() else None, prev, crosscheck=not a.no_crosscheck)
    md = summary_markdown(m)
    print(md)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")
    if m["counts"]["ok"] + m["counts"]["stale"] == 0:
        print("ERROR: no series could be fetched; not publishing.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
