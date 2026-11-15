"""U.S. Drought Monitor (NDMC) statistics API client: pull the CONUS weekly series and the
current-conditions map image, and pack every stats pull into one deterministic bronze bundle.

* Host ``usdmdataservices.unl.edu`` (pinned in ``config/reports/us_drought_monitor/source.yaml``).
  No API key. ``Accept: application/json`` is required or the API answers CSV instead.
* 429 and 5xx answers are retried with exponential backoff.
* A pull is stored byte-for-byte as gzip JSON with sorted keys and ``mtime=0``, so the same
  upstream content always hashes to the same sha256 (same pattern as Export Sales).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from usda_reports.core.logs import get_logger

log = get_logger(__name__)

REPORT_ID = "us_drought_monitor"
SOURCE = "usdm_api"
IMAGE_SOURCE = "usdm_png"
BUNDLE_SCHEMA = 1


class UsdmError(RuntimeError):
    """The USDM statistics API (or the map image) answered with something we cannot use."""


class SourceConfig(BaseModel):
    api_base: str
    endpoints: dict[str, str]
    image_url: str
    aoi: str = "us"
    area_of_interest: str = "CONUS"
    statistics_type: int = 1
    history_start: str = "2018-01-02"
    weekly_window_days: int = 70

    @classmethod
    def load(cls, path: Path) -> SourceConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError)


@dataclass
class UsdmClient:
    """USDM statistics + map-image access. ``calls`` counts requests."""

    client: httpx.Client
    config: SourceConfig
    calls: int = field(default=0)

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    def get_stats(self, start: date, end: date) -> list[dict[str, Any]]:
        """Every area of interest (CONUS and Total) for ``[start, end]``, as published."""
        self.calls += 1
        params: dict[str, str | int] = {
            "aoi": self.config.aoi,
            "startdate": f"{start.month}/{start.day}/{start.year}",
            "enddate": f"{end.month}/{end.day}/{end.year}",
            "statisticsType": self.config.statistics_type,
        }
        resp = self.client.get(
            self.config.api_base + self.config.endpoints["stats"],
            params=params,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise UsdmError(f"stats endpoint: response is not JSON: {exc}") from exc
        if not isinstance(data, list):
            raise UsdmError("stats endpoint: expected a JSON list")
        log.info("usdm.get_stats", start=str(start), end=str(end), rows=len(data))
        return data

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        reraise=True,
    )
    def get_image(self) -> bytes:
        self.calls += 1
        resp = self.client.get(self.config.image_url)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "image/png" not in ctype:
            raise UsdmError(f"map image: unexpected content-type {ctype!r}")
        return resp.content


def conus_rows(rows: list[dict[str, Any]], cfg: SourceConfig) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("areaOfInterest")) == cfg.area_of_interest]


def newest_map_date(rows: list[dict[str, Any]]) -> date | None:
    dates = [date.fromisoformat(str(r["mapDate"])[:10]) for r in rows]
    return max(dates) if dates else None


def release_key_for(map_date: date) -> str:
    """The Thursday NDMC publishes the map/statistics for a Tuesday ``map_date``."""
    from datetime import timedelta

    return (map_date + timedelta(days=2)).isoformat()


def make_bundle(kind: str, start: date, end: date, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if kind not in ("backfill", "weekly"):
        raise ValueError(f"unknown pull kind {kind!r}")
    return {
        "schema": BUNDLE_SCHEMA,
        "report_id": REPORT_ID,
        "kind": kind,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": sorted(rows, key=lambda r: (str(r.get("mapDate")), str(r.get("areaOfInterest")))),
    }


def encode_bundle(bundle: dict[str, Any]) -> bytes:
    """Deterministic bytes: sorted keys, compact separators, gzip with mtime=0."""
    raw = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def decode_bundle(data: bytes) -> dict[str, Any]:
    raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
    bundle: dict[str, Any] = json.loads(raw.decode("utf-8"))
    if bundle.get("schema") != BUNDLE_SCHEMA or bundle.get("report_id") != REPORT_ID:
        raise UsdmError("not a us_drought_monitor bronze bundle")
    return bundle
