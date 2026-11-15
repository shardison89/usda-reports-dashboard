"""Site JSON for the U.S. Drought Monitor page: ``web/public/data/reports/us_drought_monitor/``.

``index.json``: latest release and map date, next release, the current snapshot, the DSCI
timeseries (with its own 5-year mean/min/max band) and the D0-D4 category timeseries since
2018-01-02. ``image`` names the current-conditions map PNG that ``publish_all`` copies into
the site next to this JSON (``build_image_bronze`` fills ``captured_at`` once that is done).
Every number comes from gold; the browser only formats.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pandas as pd

from usda_reports.core.logs import get_logger
from usda_reports.publish.web_json import _dump

if TYPE_CHECKING:
    from usda_reports.core.manifest import Manifest

log = get_logger(__name__)

SCHEMA_VERSION = 1
REPORT_ID = "us_drought_monitor"
MART = "fct_us_drought_monitor_weekly"


def _exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "select count(*) from information_schema.tables where table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def _num(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _day(v: Any) -> str | None:
    return None if v is None or pd.isna(v) else str(pd.Timestamp(v).date())


def _ts(v: Any) -> str | None:
    if v is None or pd.isna(v):
        return None
    return pd.Timestamp(v).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(v: Any, nd: int) -> float | None:
    x = _num(v)
    return None if x is None else round(x, nd)


def build_us_drought_monitor(
    con: duckdb.DuckDBPyConnection, out_dir: Path, next_release: str | None
) -> dict[str, Any] | None:
    """Write the U.S. Drought Monitor page JSON; returns the index (None = no data yet)."""
    if not _exists(con, MART):
        return None
    df = con.execute(f"select * from {MART} order by map_date").df()
    if df.empty:
        return None
    cur = df.iloc[-1]
    folder = out_dir / "reports" / REPORT_ID
    index = {
        "schema_version": SCHEMA_VERSION,
        "report_id": REPORT_ID,
        "latest": {
            "release_key": cur.first_release_key,
            "release_ts_utc": _ts(cur.first_release_ts_utc),
            "map_date": _day(cur.map_date),
            "vintage_quality": "as_published",
        },
        "next_release": next_release,
        "history_start": _day(df.map_date.iloc[0]),
        "n_weeks": len(df),
        "current": {
            "map_date": _day(cur.map_date),
            "none_pct": _r(cur.none_pct, 2),
            "d0_pct": _r(cur.d0_pct, 2),
            "d1_pct": _r(cur.d1_pct, 2),
            "d2_pct": _r(cur.d2_pct, 2),
            "d3_pct": _r(cur.d3_pct, 2),
            "d4_pct": _r(cur.d4_pct, 2),
            "dsci": _r(cur.dsci, 1),
            "dsci_5yr_mean": _r(cur.dsci_5yr_mean, 1),
            "dsci_5yr_min": _num(cur.dsci_5yr_min),
            "dsci_5yr_max": _num(cur.dsci_5yr_max),
            "n_5yr": int(cur.n_5yr),
        },
        # [map_date, dsci, 5yr mean, min, max, n]
        "dsci": {
            "basis": "own_5yr_same_week_by_day_of_year",
            "series": [
                [
                    _day(r.map_date),
                    _r(r.dsci, 1),
                    _r(r.dsci_5yr_mean, 1),
                    _num(r.dsci_5yr_min),
                    _num(r.dsci_5yr_max),
                    int(r.n_5yr),
                ]
                for r in df.itertuples()
            ],
        },
        # [map_date, none_pct, d0_pct, d1_pct, d2_pct, d3_pct, d4_pct]
        "categories": {
            "series": [
                [
                    _day(r.map_date),
                    _r(r.none_pct, 2),
                    _r(r.d0_pct, 2),
                    _r(r.d1_pct, 2),
                    _r(r.d2_pct, 2),
                    _r(r.d3_pct, 2),
                    _r(r.d4_pct, 2),
                ]
                for r in df.itertuples()
            ],
        },
        "image": {"url": f"reports/{REPORT_ID}/current_usdm.png", "captured_at": None},
    }
    _dump(folder / "index.json", index)
    log.info("publish.us_drought_monitor", weeks=len(df), release=cur.first_release_key)
    return index


def publish_image(
    bronze_dir: Path, manifest: Manifest, out_dir: Path, index: dict[str, Any]
) -> None:
    """Copy the newest bronze map PNG next to ``index.json`` and record when it was fetched.

    The live site never hot-links ``droughtmonitor.unl.edu`` directly: every asset the page
    shows is a snapshot the pipeline took, same as every number coming from gold."""
    entries = manifest.report(f"{REPORT_ID}_image").ok()
    if not entries:
        return
    key = max(entries)
    entry = entries[key]
    if entry.file is None:
        return
    src = bronze_dir / entry.file
    if not src.exists():
        return
    folder = out_dir / "reports" / REPORT_ID
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, folder / "current_usdm.png")
    index["image"]["captured_at"] = entry.release_ts_utc or entry.fetched_at
    _dump(folder / "index.json", index)
