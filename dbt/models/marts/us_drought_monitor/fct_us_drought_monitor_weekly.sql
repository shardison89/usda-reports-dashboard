-- U.S. Drought Monitor, CONUS, one row per map_date (the latest release wins; availability is
-- dated from the first release that printed the week, same pattern as Crop Progress).
--   dsci = d0_pct + d1_pct + d2_pct + d3_pct + d4_pct (0-500: 0 = no drought anywhere in
--          CONUS, 500 = all of CONUS in D4).
-- The 5-year comparison (own, NDMC publishes none) is the same week of year across the 5
-- previous calendar years, within ±3 days by day of year (at most one week matches per year,
-- same convention as fct_crop_condition's good-to-excellent band).
-- Point in time: every history week in the 5-year comparison was released before this week
-- (tested).
with v as (
    select * from {{ ref('stg_us_drought_monitor') }}
),

ranked as (
    select
        *,
        row_number() over w_desc       as rn_latest,
        min(release_ts_utc) over w_all as first_release_ts_utc,
        cast(count(*) over w_all as integer) as n_vintages
    from v
    window
        w_desc as (partition by map_date order by release_ts_utc desc, edition desc),
        w_all as (partition by map_date)
),

firsts as (
    select map_date, arg_min(release_key, release_ts_utc) as first_release_key
    from v
    group by all
),

idx as (
    select
        r.map_date,
        r.area_of_interest,
        r.none_pct,
        r.d0_pct,
        r.d1_pct,
        r.d2_pct,
        r.d3_pct,
        r.d4_pct,
        r.d0_pct + r.d1_pct + r.d2_pct + r.d3_pct + r.d4_pct as dsci,
        r.release_key,
        r.release_ts_utc,
        f.first_release_key,
        r.first_release_ts_utc,
        r.n_vintages,
        r.source_url
    from ranked as r
    inner join firsts as f using (map_date)
    where r.rn_latest = 1
),

hist as (
    select
        cur.map_date,
        cast(count(h.dsci) as integer) as n_5yr,
        avg(h.dsci)                    as dsci_5yr_mean,
        min(h.dsci)                    as dsci_5yr_min,
        max(h.dsci)                    as dsci_5yr_max,
        max(h.first_release_ts_utc)    as hist_max_release_ts_utc
    from idx as cur
    left join idx as h
        on h.map_date between cur.map_date - interval 5 year and cur.map_date - interval 1 day
       and abs(dayofyear(h.map_date) - dayofyear(cur.map_date)) <= 3
       and h.dsci is not null
    group by cur.map_date
)

select
    i.map_date,
    i.area_of_interest,
    i.none_pct,
    i.d0_pct,
    i.d1_pct,
    i.d2_pct,
    i.d3_pct,
    i.d4_pct,
    i.dsci,
    i.release_key,
    i.release_ts_utc,
    i.first_release_key,
    i.first_release_ts_utc,
    i.n_vintages,
    h.n_5yr,
    h.dsci_5yr_mean,
    h.dsci_5yr_min,
    h.dsci_5yr_max,
    h.hist_max_release_ts_utc,
    i.source_url,
    i.map_date = max(i.map_date) over () as is_latest_week
from idx as i
inner join hist as h using (map_date)
order by i.map_date
