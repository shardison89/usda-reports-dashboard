"""U.S. Drought Monitor report spec: wires the USDM ingest and silver parser into the registry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from usda_reports.core.logs import get_logger
from usda_reports.reports.base import ReportSpec

if TYPE_CHECKING:
    from usda_reports.bronze.store import BronzeStore
    from usda_reports.core.manifest import Manifest
    from usda_reports.reports.us_drought_monitor.ingest import IngestResult
    from usda_reports.reports.us_drought_monitor.parse import Snapshot
    from usda_reports.reports.us_drought_monitor.source import SourceConfig

log = get_logger(__name__)


class USDroughtMonitorSpec(ReportSpec):
    """U.S. Drought Monitor (weekly, Thursday ~08:30 ET, NDMC statistics API + map image)."""

    # A USDM problem must never block WASDE or the other reports.
    isolated = True

    @property
    def source_path(self) -> Path:
        return self.config_dir / "source.yaml"

    def source_config(self) -> SourceConfig:
        from usda_reports.reports.us_drought_monitor.source import SourceConfig

        return SourceConfig.load(self.source_path)

    def source_errors(self) -> tuple[type[Exception], ...]:
        from usda_reports.reports.us_drought_monitor.source import UsdmError

        return (UsdmError, *super().source_errors())

    def usdm_client(self, client: httpx.Client) -> object:
        from usda_reports.reports.us_drought_monitor.source import UsdmClient

        return UsdmClient(client, self.source_config())

    def image_store(self, store: BronzeStore) -> BronzeStore:
        """A second bronze store (its own manifest slot) for the map PNG of each release."""
        from usda_reports.bronze.store import BronzeStore as _BronzeStore

        return _BronzeStore(
            store.root, store.manifest, f"{self.report_id}_image", self.config.release_time_et
        )

    def ingest(
        self,
        client: httpx.Client,
        store: BronzeStore,
        now: datetime | None = None,
        force: bool = False,
    ) -> IngestResult:
        from usda_reports.reports.us_drought_monitor.ingest import ingest
        from usda_reports.reports.us_drought_monitor.source import UsdmClient

        usdm = self.usdm_client(client)
        assert isinstance(usdm, UsdmClient)
        return ingest(
            usdm,
            store,
            self.image_store(store),
            now or datetime.now(UTC),
            force=force,
            check=self.check_bundle,
        )

    def check_bundle(self, bundle: dict[str, Any]) -> None:
        """Parse one pulled bundle on its own and run the silver contract on it.

        Raises :class:`UsdmError` before the pull is stored (bronze keeps only good pulls)."""
        from datetime import date

        from usda_reports.core.contracts import ContractError
        from usda_reports.reports.us_drought_monitor.parse import Snapshot
        from usda_reports.reports.us_drought_monitor.source import (
            UsdmError,
            conus_rows,
            newest_map_date,
            release_key_for,
        )

        conus = conus_rows(bundle.get("rows") or [], self.source_config())
        newest = newest_map_date(conus)
        key = release_key_for(newest) if newest else str(bundle.get("start") or date.today())
        snap = Snapshot(key=key, seq=1, sha256="0" * 64, edition=1, bundle=bundle)
        try:
            self._silver_from([snap])
        except ContractError as exc:
            raise UsdmError(f"release {snap.key} rejected before bronze: {exc}") from exc

    def backfill(
        self, client: httpx.Client, store: BronzeStore, end: str, **kwargs: object
    ) -> list[str]:
        """Backfill when bronze is empty, otherwise pull the trailing window if it is new."""
        return self.ingest(client, store).ingested

    def _silver_from(self, snaps: list[Snapshot]) -> pd.DataFrame:
        from usda_reports.reports.us_drought_monitor.parse import build_frame, validate

        cfg = self.source_config()
        silver = build_frame(snaps, cfg, self.config.release_time_et)
        validate(silver, self.contract())
        return silver

    def _write(self, silver: pd.DataFrame) -> pd.DataFrame:
        from usda_reports.reports.us_drought_monitor.parse import write_silver

        self.settings.silver_dir.mkdir(parents=True, exist_ok=True)
        write_silver(silver, self.silver_path)
        log.info(
            "silver.us_drought_monitor",
            rows=len(silver),
            releases=int(silver["release_key"].nunique()) if len(silver) else 0,
        )
        return silver

    def write_empty_silver(self) -> None:
        from usda_reports.reports.us_drought_monitor.parse import SILVER_COLUMNS

        self._write(pd.DataFrame(columns=SILVER_COLUMNS))

    def build_silver(self, manifest: Manifest) -> pd.DataFrame:
        from usda_reports.reports.us_drought_monitor.parse import load_snapshots

        snaps = load_snapshots(self.settings.bronze_dir, manifest)
        return self._write(self._silver_from(snaps))

    def build_silver_last_good(self, manifest: Manifest) -> pd.DataFrame:
        """Fallback after :meth:`build_silver` failed: drop the newest snapshot(s) until the
        rest builds (the last good state); with nothing left, an empty silver with the full
        schema, so WASDE gold and publish still run."""
        from usda_reports.reports.us_drought_monitor.parse import SILVER_COLUMNS, load_snapshots

        try:
            snaps = load_snapshots(self.settings.bronze_dir, manifest)
        except Exception as exc:
            log.error("silver.us_drought_monitor_unreadable", error=str(exc))
            snaps = []
        for keep in range(len(snaps) - 1, 0, -1):
            try:
                silver = self._silver_from(snaps[:keep])
            except Exception as exc:
                log.warning("silver.us_drought_monitor_fallback", kept=keep, error=str(exc))
                continue
            log.warning(
                "silver.us_drought_monitor_last_good",
                kept=keep,
                dropped=[s.key for s in snaps[keep:]],
            )
            return self._write(silver)
        return self._write(pd.DataFrame(columns=SILVER_COLUMNS))
