-- U.S. Drought Monitor silver (CONUS only): one row per release x map_date. A week may be
-- printed by more than one weekly pull (the trailing window overlaps); the mart picks the
-- latest release per week and dates first availability from the first.
select
    report_id,
    release_key,
    cast(release_ts_utc as timestamptz) as release_ts_utc,
    cast(edition as integer)            as edition,
    source,
    bronze_sha256,
    vintage_quality,
    area_of_interest,
    cast(map_date as date)              as map_date,
    cast(none_pct as double)            as none_pct,
    cast(d0_pct as double)              as d0_pct,
    cast(d1_pct as double)              as d1_pct,
    cast(d2_pct as double)              as d2_pct,
    cast(d3_pct as double)              as d3_pct,
    cast(d4_pct as double)              as d4_pct,
    valid_start,
    valid_end,
    source_url
from read_parquet('{{ var("silver_dir") }}/us_drought_monitor.parquet')
