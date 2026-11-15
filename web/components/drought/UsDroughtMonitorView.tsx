"use client";

import CategoryArea, { type AreaLayer } from "@/components/charts/CategoryArea";
import { ChartTable } from "@/components/charts/common";
import PaceChart from "@/components/charts/PaceChart";
import { useLang, useT } from "@/components/chrome/I18n";
import TopBar, { type ReleaseInfo } from "@/components/chrome/TopBar";
import { Chapter, Stat, Swatch, TakeSection } from "@/components/report/Parts";
import type { TakeFile, UsDroughtMonitorIndex } from "@/lib/data-contract";
import { num, pct, shortDate } from "@/lib/format";

const RED = "var(--r-drought)";
// Fixed severity-scale colours (not theme tokens): the same meaning in light and dark mode,
// chosen for contrast against both card backgrounds rather than copying NDMC's own palette.
const CATEGORY_COLORS = {
  none: "#94a3b8",
  d0: "#fde68a",
  d1: "#fbbf24",
  d2: "#f59e0b",
  d3: "#dc2626",
  d4: "#7f1d1d",
} as const;
// Fewer than this many of the 5 prior years had a comparable week: no band is drawn.
const MIN_N = 3;

/** Week index (1 …) with a date label spread across the series, ending on the newest week. */
function weekTicks(weeks: string[], lang: "en" | "pt", count = 8) {
  const every = Math.max(1, Math.ceil(weeks.length / count));
  return weeks
    .map((w, i) => ({ x: i + 1, label: shortDate(w, true, lang) ?? w }))
    .filter((_, i) => (weeks.length - 1 - i) % every === 0);
}

export default function UsDroughtMonitorView({
  index,
  take,
  release,
}: {
  index: UsDroughtMonitorIndex;
  take: TakeFile[];
  release: ReleaseInfo;
}) {
  const t = useT();
  const { lang } = useLang();
  const cur = index.current;

  // ---- chapter 1: DSCI vs its own 5-year band
  const dsci = index.dsci.series;
  const weeks = dsci.map((r) => r[0]);
  const dsciLines = [{ id: "dsci", current: true, points: dsci.map((r, i) => [i + 1, r[1]] as [number, number | null]) }];
  const full = (r: (typeof dsci)[number]) => r[5] >= MIN_N;
  const band = dsci.map((r, i) => (full(r) ? { x: i + 1, mean: r[2], min: r[3], max: r[4] } : { x: i + 1, mean: null, min: null, max: null }));
  const bandWeeks = dsci.filter(full).length;
  const thinWeeks = dsci.length - bandWeeks;
  const lastDsci = [...dsci].reverse().find((r) => r[1] !== null);
  const dsciAriaVars = { date: shortDate(lastDsci?.[0], true, lang) ?? "—", v: num(lastDsci?.[1], 0) };
  const dsciAria =
    lastDsci && full(lastDsci)
      ? t("udm.c1.aria", { ...dsciAriaVars, min: num(lastDsci[3], 0), max: num(lastDsci[4], 0) })
      : t("udm.c1.ariaNoBand", { ...dsciAriaVars, min: MIN_N });
  const dsciRows = dsci.map((r) => [shortDate(r[0], true, lang) ?? r[0], num(r[1], 0), num(r[2], 0), num(r[3], 0), num(r[4], 0), r[5]]);

  // ---- chapter 2: D0-D4 shares, 100%-stacked
  const cats = index.categories.series;
  const catWeeks = cats.map((r) => r[0]);
  const xs = cats.map((_, i) => i + 1);
  const diff = (a: number | null, b: number | null) => (a === null || b === null ? null : a - b);
  const layers: AreaLayer[] = [
    { id: "none", color: CATEGORY_COLORS.none, values: cats.map((r) => r[1]) },
    { id: "d0", color: CATEGORY_COLORS.d0, values: cats.map((r) => diff(r[2], r[3])) },
    { id: "d1", color: CATEGORY_COLORS.d1, values: cats.map((r) => diff(r[3], r[4])) },
    { id: "d2", color: CATEGORY_COLORS.d2, values: cats.map((r) => diff(r[4], r[5])) },
    { id: "d3", color: CATEGORY_COLORS.d3, values: cats.map((r) => diff(r[5], r[6])) },
    { id: "d4", color: CATEGORY_COLORS.d4, values: cats.map((r) => r[6]) },
  ];
  const catAria = t("udm.c2.aria", { date: shortDate(catWeeks.at(-1), true, lang) ?? "—" });
  const catRows = cats.map((r) => [shortDate(r[0], true, lang) ?? r[0], pct(r[1]), pct(r[2]), pct(r[3]), pct(r[4]), pct(r[5]), pct(r[6])]);
  const catHead = [t("udm.h.week"), t("udm.legend.none"), t("udm.legend.d0"), t("udm.legend.d1"), t("udm.legend.d2"), t("udm.legend.d3"), t("udm.legend.d4")];

  return (
    <>
      <TopBar
        title={t("udm.title")}
        release={release}
        sub={t("udm.sub", {
          date: shortDate(cur.map_date, true, lang) ?? "—",
          rel: shortDate(index.latest.release_key, true, lang) ?? "—",
          next: shortDate(index.next_release, false, lang) ?? "—",
        })}
      />

      <section aria-label={t("udm.k.dsci")} className="flex flex-col gap-3">
        <div className="grid-fit-220">
          <Stat color={RED} label={t("udm.k.dsci")} value={num(cur.dsci, 0)} sub={t("udm.k.dsciSub")} testid="udm-dsci" />
          <Stat color={RED} label={t("udm.k.d0")} value={pct(cur.d0_pct)} testid="udm-d0" />
          <Stat color={RED} label={t("udm.k.d4")} value={pct(cur.d4_pct)} testid="udm-d4" />
          <Stat color={RED} label={t("udm.k.next")} value={shortDate(index.next_release, true, lang) ?? "—"} sub={t("udm.k.nextSub")} testid="udm-next" />
        </div>
      </section>

      <Chapter n={1} color={RED} eyebrow="udm.c1.eb" title="udm.c1">
        <p className="m-0 text-[15px] leading-snug" data-testid="udm-c1-line">
          {t("udm.c1.lead", {
            date: shortDate(cur.map_date, true, lang) ?? "—",
            v: num(cur.dsci, 0),
            mean: num(cur.dsci_5yr_mean, 0),
            min: num(cur.dsci_5yr_min, 0),
            max: num(cur.dsci_5yr_max, 0),
            n: cur.n_5yr,
          })}
        </p>
        {dsci.length > 1 ? (
          <>
            <PaceChart
              lines={dsciLines}
              band={band}
              xMax={dsci.length}
              xTicks={weekTicks(weeks, lang)}
              color={RED}
              ariaLabel={dsciAria}
              targetLabel=""
              reference={null}
              domain="data"
              format={(x) => num(x, 0)}
            />
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" data-testid="udm-c1-legend">
              <span className="flex items-center gap-1.5">
                <Swatch kind="line" color={RED} />
                {t("udm.c1.cur")}
              </span>
              {bandWeeks > 0 && (
                <>
                  <span className="flex items-center gap-1.5">
                    <Swatch kind="band" color={RED} />
                    {t("udm.c1.band")}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <Swatch kind="dashed" />
                    {t("udm.c1.mean")}
                  </span>
                </>
              )}
            </div>
            {thinWeeks > 0 && (
              <p className="m-0 text-xs text-muted" data-testid="udm-band-gap">
                {t("udm.c1.bandGap", { k: thinWeeks, min: MIN_N })}
              </p>
            )}
            <ChartTable caption={dsciAria} head={[t("udm.h.week"), t("udm.h.dsci"), t("esr.h.mean"), t("esr.h.min"), t("esr.h.max"), "n"]} rows={dsciRows} />
          </>
        ) : (
          <p className="m-0 text-[13px] text-muted">{t("udm.c1.none")}</p>
        )}
      </Chapter>

      <Chapter n={2} color={RED} eyebrow="udm.c2.eb" title="udm.c2">
        <p className="m-0 text-[15px] leading-snug" data-testid="udm-c2-line">
          {t("udm.c2.lead", { date: shortDate(cur.map_date, true, lang) ?? "—", none: pct(cur.none_pct), d4: pct(cur.d4_pct) })}
        </p>
        <CategoryArea xs={xs} layers={layers} xMax={xs.length} xTicks={weekTicks(catWeeks, lang)} ariaLabel={catAria} />
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" data-testid="udm-c2-legend">
          {(["none", "d0", "d1", "d2", "d3", "d4"] as const).map((k) => (
            <span key={k} className="flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block h-2.5 w-3.5 rounded-sm" style={{ background: CATEGORY_COLORS[k] }} />
              {t(`udm.legend.${k}`)}
            </span>
          ))}
        </div>
        <ChartTable caption={catAria} head={catHead} rows={catRows} />
      </Chapter>

      <Chapter n={3} color={RED} eyebrow="udm.c3.eb" title="udm.c3">
        <figure className="m-0 flex flex-col gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element -- a static snapshot copied into the site, not a Next-optimizable remote image */}
          <img src={`/data/${index.image.url}`} alt={t("udm.c3.alt")} className="w-full rounded-xl border border-line" loading="lazy" />
          <figcaption className="text-xs text-muted">
            {t("udm.c3.caption", { date: shortDate(cur.map_date, true, lang) ?? "—" })}
            {index.image.captured_at && <> · {t("udm.c3.captured", { date: shortDate(index.image.captured_at, true, lang) ?? "—" })}</>}
          </figcaption>
        </figure>
        <a
          href="https://droughtmonitor.unl.edu/CurrentMap.aspx"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-h-11 items-center font-semibold text-link"
        >
          {t("udm.c3.link")}
        </a>
      </Chapter>

      <TakeSection title="udm.take" testid="udm-take" files={take} reportId="us_drought_monitor" methodAnchor="us-drought-monitor">
        <p className="m-0 text-xs text-muted" data-testid="udm-window">
          {t("udm.window", { from: shortDate(index.history_start, true, lang) ?? index.history_start })}
        </p>
      </TakeSection>
    </>
  );
}
