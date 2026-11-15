"""U.S. Drought Monitor: API client (mocked, no network), bundles and the silver parser.

Fixture in tests/fixtures/us_drought_monitor/ is a trimmed real API response (2026-11-15
spike): ``stats_2026-11-10.json`` is what ``aoi=us`` returns for three weeks, CONUS and Total.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from usda_reports.bronze.store import BronzeStore
from usda_reports.core.contracts import Contract, ContractError
from usda_reports.core.manifest import Manifest
from usda_reports.reports.us_drought_monitor import ingest as usdm_ingest
from usda_reports.reports.us_drought_monitor.parse import (
    Snapshot,
    build_frame,
    validate,
)
from usda_reports.reports.us_drought_monitor.source import (
    SourceConfig,
    UsdmClient,
    UsdmError,
    conus_rows,
    decode_bundle,
    encode_bundle,
    make_bundle,
    newest_map_date,
    release_key_for,
)

# A minimal valid 1x1 transparent PNG (67 bytes).
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture
def cfg(config_dir) -> SourceConfig:
    return SourceConfig.load(config_dir / "reports" / "us_drought_monitor" / "source.yaml")


@pytest.fixture
def usdm_fx(fixtures):
    return fixtures / "us_drought_monitor"


@pytest.fixture
def rows(usdm_fx) -> list[dict]:
    return json.loads((usdm_fx / "stats_2026-11-10.json").read_text(encoding="utf-8"))


# ── config, bundles ──────────────────────────────────────────────────────────


def test_source_config_loads(cfg):
    assert cfg.area_of_interest == "CONUS"
    assert cfg.endpoints["stats"].startswith("/api/")
    assert cfg.image_url.endswith(".png")


def test_conus_rows_drops_total(rows, cfg):
    conus = conus_rows(rows, cfg)
    assert len(conus) == 3
    assert {r["areaOfInterest"] for r in conus} == {"CONUS"}


def test_newest_map_date_and_release_key(rows, cfg):
    conus = conus_rows(rows, cfg)
    newest = newest_map_date(conus)
    assert newest == date(2026, 11, 10)
    # NDMC publishes the Thursday after the Tuesday map date.
    assert release_key_for(newest) == "2026-11-12"


def test_bundle_roundtrip_is_deterministic(rows):
    b1 = make_bundle("backfill", date(2018, 1, 2), date(2026, 11, 17), rows)
    b2 = make_bundle("backfill", date(2018, 1, 2), date(2026, 11, 17), list(reversed(rows)))
    assert encode_bundle(b1) == encode_bundle(b2)
    assert decode_bundle(encode_bundle(b1)) == b1


def test_decode_bundle_rejects_other_reports(rows):
    bundle = make_bundle("weekly", date(2026, 1, 1), date(2026, 1, 8), rows)
    bundle["report_id"] = "wasde"
    with pytest.raises(UsdmError):
        decode_bundle(encode_bundle(bundle))


# ── silver ────────────────────────────────────────────────────────────────────


def test_build_frame_keeps_only_conus_with_envelope(rows, cfg):
    bundle = make_bundle("backfill", date(2018, 1, 2), date(2026, 11, 17), rows)
    snap = Snapshot(key="2026-11-12", seq=1, sha256="a" * 64, edition=1, bundle=bundle)
    df = build_frame([snap], cfg, "08:30")
    assert len(df) == 3
    assert set(df.area_of_interest) == {"CONUS"}
    assert df.release_key.unique().tolist() == ["2026-11-12"]
    assert df.release_ts_utc.iloc[0].isoformat() == "2026-11-12T13:30:00+00:00"
    assert df.vintage_quality.unique().tolist() == ["as_published"]
    newest = df[df.map_date == date(2026, 11, 10)].iloc[0]
    assert newest.d0_pct == pytest.approx(66.98)
    assert newest.none_pct + newest.d0_pct == pytest.approx(100.0)


def test_validate_passes_on_good_frame(rows, cfg, config_dir):
    bundle = make_bundle("backfill", date(2018, 1, 2), date(2026, 11, 17), rows)
    snap = Snapshot(key="2026-11-12", seq=1, sha256="a" * 64, edition=1, bundle=bundle)
    df = build_frame([snap], cfg, "08:30")
    contract = Contract.load(config_dir / "contracts" / "us_drought_monitor.yaml")
    validate(df, contract)  # must not raise


def test_validate_rejects_non_monotonic_categories(rows, cfg, config_dir):
    bad = [dict(r) for r in rows]
    bad[0]["d1"] = 90.0  # d1 ("D1 or worse") cannot exceed d0 ("D0 or worse")
    bundle = make_bundle("backfill", date(2018, 1, 2), date(2026, 11, 17), bad)
    snap = Snapshot(key="2026-11-12", seq=1, sha256="a" * 64, edition=1, bundle=bundle)
    df = build_frame([snap], cfg, "08:30")
    contract = Contract.load(config_dir / "contracts" / "us_drought_monitor.yaml")
    with pytest.raises(ContractError):
        validate(df, contract)


# ── ingest (mocked HTTP, no network) ─────────────────────────────────────────


@respx.mock
def test_ingest_backfill_stores_stats_and_image(cfg, rows, tmp_path):
    respx.get(cfg.api_base + cfg.endpoints["stats"]).mock(
        return_value=httpx.Response(200, json=rows)
    )
    respx.get(cfg.image_url).mock(
        return_value=httpx.Response(200, content=PNG_1X1, headers={"content-type": "image/png"})
    )
    manifest = Manifest()
    store = BronzeStore(tmp_path, manifest, "us_drought_monitor", "08:30")
    image_store = BronzeStore(tmp_path, manifest, "us_drought_monitor_image", "08:30")
    with httpx.Client() as c:
        usdm = UsdmClient(c, cfg)
        res = usdm_ingest.ingest(usdm, store, image_store, datetime(2026, 11, 15, tzinfo=UTC))
    assert res.ingested == ["2026-11-12"]
    assert res.image_updated is True
    entry = manifest.releases("us_drought_monitor")["2026-11-12"]
    assert entry.file == "us_drought_monitor/2026-11-12.json.gz"
    img_entry = manifest.releases("us_drought_monitor_image")["2026-11-12"]
    assert img_entry.file == "us_drought_monitor_image/2026-11-12.png"
    assert (tmp_path / img_entry.file).read_bytes() == PNG_1X1


@respx.mock
def test_ingest_weekly_up_to_date_is_a_noop(cfg, rows, tmp_path):
    stats_route = respx.get(cfg.api_base + cfg.endpoints["stats"]).mock(
        return_value=httpx.Response(200, json=rows)
    )
    image_route = respx.get(cfg.image_url).mock(
        return_value=httpx.Response(200, content=PNG_1X1, headers={"content-type": "image/png"})
    )
    manifest = Manifest()
    store = BronzeStore(tmp_path, manifest, "us_drought_monitor", "08:30")
    image_store = BronzeStore(tmp_path, manifest, "us_drought_monitor_image", "08:30")
    bundle = make_bundle("backfill", date(2018, 1, 2), date(2026, 11, 17), rows)
    store.put("2026-11-12", encode_bundle(bundle), source="usdm_api", url=cfg.api_base)
    with httpx.Client() as c:
        usdm = UsdmClient(c, cfg)
        res = usdm_ingest.ingest(usdm, store, image_store, datetime(2026, 11, 15, tzinfo=UTC))
    assert res.ingested == []
    assert res.note == "release already ingested"
    assert stats_route.call_count == 1  # discovery still happens...
    assert image_route.call_count == 0  # ...but the image is never re-pulled for a stale week
