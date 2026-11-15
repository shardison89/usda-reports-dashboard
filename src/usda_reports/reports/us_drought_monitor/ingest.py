"""U.S. Drought Monitor ingest: one-time backfill and the weekly pull, both into bronze.

* **Backfill** (no USDM snapshot in bronze yet): one pull from ``history_start`` to today
  (+ a week), stored as one bundle under ``kind: backfill``.
* **Weekly**: one pull of the trailing ``weekly_window_days`` (about 10 weeks) so a late
  correction to a recent week is caught without re-fetching the whole series every run.
  Re-pulling the same release only stores a new edition when the bytes changed.

Every pull also fetches the current-conditions map image (updated on the same Thursday
cadence) and stores it as its own bronze entry, under the same release key, in a second
``BronzeStore`` (``report_id = "us_drought_monitor_image"``) so each artifact keeps its own
one-file-per-key manifest slot.

A pulled bundle is parsed and contract-checked (``check``) *before* it reaches bronze: a
contract break fails the ingest with :class:`UsdmError` and leaves bronze as it was, so the
last good USDM state keeps building.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from usda_reports.bronze.store import BronzeStore, sha256
from usda_reports.core.logs import get_logger
from usda_reports.core.manifest import release_ts_utc
from usda_reports.reports.us_drought_monitor.source import (
    IMAGE_SOURCE,
    SOURCE,
    UsdmClient,
    UsdmError,
    conus_rows,
    encode_bundle,
    make_bundle,
    newest_map_date,
    release_key_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger(__name__)


@dataclass
class IngestResult:
    ingested: list[str] = field(default_factory=list)  # release keys new or changed in bronze
    release_key: str | None = None
    map_date: date | None = None
    image_updated: bool = False
    calls: int = 0
    note: str = ""


def _store_bundle(
    store: BronzeStore, release_key: str, bundle: dict[str, Any], url: str
) -> tuple[bool, str]:
    """Write the bundle unless the same release already holds identical bytes."""
    data = encode_bundle(bundle)
    old = store.entries.get(release_key)
    if old is not None and old.status == "ok" and old.sha256 == sha256(data):
        return False, release_key
    edition = (old.edition + 1) if old is not None and old.status == "ok" else 1
    store.put(release_key, data, source=SOURCE, url=url, release_date=release_key, edition=edition)
    return True, release_key


def _store_image(
    image_store: BronzeStore, release_key: str, data: bytes, url: str
) -> tuple[bool, str]:
    old = image_store.entries.get(release_key)
    if old is not None and old.status == "ok" and old.sha256 == sha256(data):
        return False, release_key
    edition = (old.edition + 1) if old is not None and old.status == "ok" else 1
    image_store.put(
        release_key, data, source=IMAGE_SOURCE, url=url, release_date=release_key, edition=edition
    )
    return True, release_key


def ingest(
    usdm: UsdmClient,
    store: BronzeStore,
    image_store: BronzeStore,
    now: datetime | None = None,
    force: bool = False,
    check: Callable[[dict[str, Any]], None] | None = None,
) -> IngestResult:
    """Backfill when bronze has no USDM snapshot yet, else pull the trailing window if new.

    ``check(bundle)`` validates the pull before it is stored (raises :class:`UsdmError`)."""
    now = now or datetime.now(UTC)
    today = now.date()
    result = IngestResult()
    have = {k for k, e in store.entries.items() if e.status == "ok"}
    kind = "weekly" if have else "backfill"
    start = (
        date.fromisoformat(usdm.config.history_start)
        if kind == "backfill"
        else today - timedelta(days=usdm.config.weekly_window_days)
    )
    end = today + timedelta(days=7)
    rows = usdm.get_stats(start, end)
    conus = conus_rows(rows, usdm.config)
    if not conus:
        raise UsdmError(f"stats pull {start}..{end} has no {usdm.config.area_of_interest} rows")
    newest = newest_map_date(conus)
    assert newest is not None
    release_key = release_key_for(newest)
    result.release_key = release_key
    result.map_date = newest
    if kind == "weekly" and release_key in have and not force:
        result.note = "release already ingested"
        result.calls = usdm.calls
        log.info("usdm.up_to_date", release=release_key)
        return result
    bundle = make_bundle(kind, start, end, rows)
    if check is not None:
        check(bundle)  # raises UsdmError: nothing is written to bronze
    changed, key = _store_bundle(store, release_key, bundle, usdm.config.api_base)
    if changed:
        result.ingested.append(key)
    image = usdm.get_image()
    result.image_updated, _ = _store_image(image_store, release_key, image, usdm.config.image_url)
    if result.image_updated:
        entry = image_store.entries[release_key]
        entry.release_ts_utc = release_ts_utc(release_key, "08:30")
    result.calls = usdm.calls
    log.info(
        "usdm.ingested" if changed else "usdm.unchanged",
        kind=kind,
        release=key,
        map_date=str(newest),
        image_updated=result.image_updated,
        calls=usdm.calls,
    )
    return result
