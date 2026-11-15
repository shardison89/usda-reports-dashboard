"""U.S. Drought Monitor silver: every bronze pull (snapshot) → one long table, CONUS only.

One row per (release, map_date): ``none_pct`` is the % of CONUS with no drought, ``d0_pct``
… ``d4_pct`` are the % of CONUS in that category OR WORSE (cumulative, as NDMC publishes
them) — ``none_pct + d0_pct`` is always 100 (± rounding). The Drought Severity and Coverage
Index (DSCI = d0 + d1 + d2 + d3 + d4) and the 5-year comparison are computed in gold
(``fct_us_drought_monitor_weekly``), not here.

A week may be printed by more than one weekly pull (the trailing window overlaps): silver
keeps every vintage (same pattern as Crop Progress); gold picks the latest release per week
and dates first availability from the first.

Envelope semantics:

* ``release_key``: the NDMC/USDA release date (the Thursday after the Tuesday ``map_date``),
  never the API's own metadata.
* ``vintage_quality``: always ``as_published`` (USDM does not revise published weeks).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from usda_reports.bronze.store import file_sha256
from usda_reports.core.contracts import Contract, ContractError
from usda_reports.core.manifest import Manifest, release_ts_utc
from usda_reports.reports.us_drought_monitor.source import (
    REPORT_ID,
    SOURCE,
    SourceConfig,
    UsdmError,
    conus_rows,
    decode_bundle,
)
from usda_reports.silver.envelope import ENVELOPE_COLUMNS

KEY = ["release_key", "map_date"]
SILVER_COLUMNS = [
    *ENVELOPE_COLUMNS,
    "area_of_interest",
    "map_date",
    "none_pct",
    "d0_pct",
    "d1_pct",
    "d2_pct",
    "d3_pct",
    "d4_pct",
    "valid_start",
    "valid_end",
    "source_url",
]
_INT = {"edition"}
_FLOAT = {"none_pct", "d0_pct", "d1_pct", "d2_pct", "d3_pct", "d4_pct"}
_DATES = {"map_date"}
SILVER_SCHEMA = pa.schema(
    [
        (
            c,
            pa.timestamp("ns", tz="UTC")
            if c == "release_ts_utc"
            else pa.date32()
            if c in _DATES
            else pa.float64()
            if c in _FLOAT
            else pa.int32()
            if c in _INT
            else pa.string(),
        )
        for c in SILVER_COLUMNS
    ]
)


def write_silver(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df[SILVER_COLUMNS], schema=SILVER_SCHEMA, preserve_index=False)
    pq.write_table(table, path)
    return path


@dataclass(frozen=True)
class Snapshot:
    key: str
    seq: int
    sha256: str
    edition: int
    bundle: dict[str, Any]


def rows_frame(snap: Snapshot, cfg: SourceConfig) -> pd.DataFrame:
    """CONUS rows of one snapshot (no envelope); raises on a structural problem."""
    rows = conus_rows(snap.bundle.get("rows") or [], cfg)
    if not rows:
        raise UsdmError(f"snapshot {snap.key}: no {cfg.area_of_interest} rows")
    out = []
    for r in rows:
        try:
            out.append(
                {
                    "area_of_interest": str(r["areaOfInterest"]),
                    "map_date": date.fromisoformat(str(r["mapDate"])[:10]),
                    "none_pct": float(r["none"]),
                    "d0_pct": float(r["d0"]),
                    "d1_pct": float(r["d1"]),
                    "d2_pct": float(r["d2"]),
                    "d3_pct": float(r["d3"]),
                    "d4_pct": float(r["d4"]),
                    "valid_start": str(r.get("validStart") or "")[:10] or None,
                    "valid_end": str(r.get("validEnd") or "")[:10] or None,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UsdmError(f"snapshot {snap.key}: unreadable row {r!r}: {exc}") from exc
    return pd.DataFrame(out)


def build_frame(
    snapshots: list[Snapshot], cfg: SourceConfig, release_time_et: str = "08:30"
) -> pd.DataFrame:
    """Silver frame (envelope first) from snapshots in pull order; raises on any problem.

    ``release_ts_utc`` is derived straight from each snapshot's release key (the Thursday
    NDMC/USDA publish at, in the configured ET time) rather than from the bundle: the API
    carries no publication timestamp of its own."""
    if not snapshots:
        return pd.DataFrame(columns=SILVER_COLUMNS)
    frames = []
    for s in snapshots:
        df = rows_frame(s, cfg)
        df.insert(0, "report_id", REPORT_ID)
        df.insert(1, "release_key", s.key)
        df.insert(2, "release_ts_utc", pd.Timestamp(release_ts_utc(s.key, release_time_et)))
        df.insert(3, "edition", s.edition)
        df.insert(4, "source", SOURCE)
        df.insert(5, "bronze_sha256", s.sha256)
        df.insert(6, "vintage_quality", "as_published")
        df["source_url"] = cfg.api_base + cfg.endpoints["stats"]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["release_ts_utc"] = pd.to_datetime(out["release_ts_utc"], utc=True)
    out["edition"] = out["edition"].astype("int32")
    for c in _FLOAT:
        out[c] = out[c].astype("float64")
    return out[SILVER_COLUMNS].sort_values(KEY, kind="stable").reset_index(drop=True)


def load_snapshots(bronze_dir: Path, manifest: Manifest) -> list[Snapshot]:
    """Every ok USDM bronze entry in pull order (release key, then edition), sha256-verified."""
    entries = manifest.report(REPORT_ID).ok()
    out = []
    for seq, (key, e) in enumerate(sorted(entries.items(), key=lambda kv: (kv[0], kv[1].edition))):
        assert e.file is not None
        path = bronze_dir / e.file
        if not path.exists() or file_sha256(path) != e.sha256:
            raise UsdmError(f"bronze {e.file}: missing or sha256 mismatch")
        out.append(
            Snapshot(
                key=key,
                seq=seq + 1,
                sha256=str(e.sha256),
                edition=e.edition,
                bundle=decode_bundle(path.read_bytes()),
            )
        )
    return out


def validate(silver: pd.DataFrame, contract: Contract) -> None:
    contract.enforce(silver)
    if silver.empty:
        return
    # none_pct + d0_pct is always 100: none is the complement of "any drought" (d0 or worse).
    sums = silver["none_pct"] + silver["d0_pct"]
    bad = silver[(sums - 100).abs() > 1.0]
    if len(bad):
        raise ContractError(
            f"none_pct + d0_pct != 100 in {len(bad)} rows, e.g. "
            f"{bad[['release_key', 'map_date']].head(5).to_dict('records')}"
        )
    # Categories are cumulative (D0 or worse >= D1 or worse >= ... >= D4 or worse).
    nonmono = silver[
        (silver.d0_pct < silver.d1_pct - 0.01)
        | (silver.d1_pct < silver.d2_pct - 0.01)
        | (silver.d2_pct < silver.d3_pct - 0.01)
        | (silver.d3_pct < silver.d4_pct - 0.01)
    ]
    if len(nonmono):
        raise ContractError(f"drought categories not monotonic in {len(nonmono)} rows")
