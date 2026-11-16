"use client";

import { niceTicks, useWidth } from "@/components/charts/common";

export type AreaLayer = { id: string; color: string; values: (number | null)[] };

/** Stacked area chart (bottom to top, in ``layers`` order) on a shared x axis, scaled to the
 *  data (not necessarily 0-100%: the layers need not sum to 100). Used for the U.S. Drought
 *  Monitor's D0-D4 category shares. A null share (should not happen once every category is
 *  published) stacks as 0, never bridged. */
export default function CategoryArea({
  xs,
  layers,
  xMax,
  xTicks,
  ariaLabel,
  height = 240,
}: {
  xs: number[];
  layers: AreaLayer[];
  xMax: number;
  xTicks: { x: number; label: string }[];
  ariaLabel: string;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const m = { l: 36, r: 12, t: 10, b: 26 };
  const x = (v: number) => m.l + (v / Math.max(xMax, 1)) * (width - m.l - m.r);

  const bands = layers.reduce<{ layer: AreaLayer; y0: number[]; y1: number[] }[]>((acc, layer) => {
    const y0 = acc.length ? acc[acc.length - 1].y1 : xs.map(() => 0);
    const y1 = y0.map((c, i) => c + (layer.values[i] ?? 0));
    return [...acc, { layer, y0, y1 }];
  }, []);
  const top = bands.length ? bands[bands.length - 1].y1 : [0];
  const ticks = niceTicks(0, Math.max(...top, 0), 4);
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const y = (v: number) => height - m.b - ((v - lo) / (hi - lo || 1)) * (height - m.b - m.t);
  const poly = (ys: number[], reversed: boolean) => {
    const order = [...xs.keys()];
    if (reversed) order.reverse();
    return order.map((i) => `${x(xs[i]).toFixed(1)},${y(ys[i]).toFixed(1)}`).join(" ");
  };
  const shownTicks: typeof xTicks = [];
  for (const t of xTicks) {
    const prev = shownTicks[shownTicks.length - 1];
    if (!prev || x(t.x) - x(prev.x) >= 40) shownTicks.push(t);
  }

  return (
    <div ref={ref} className="w-full">
      <svg role="img" aria-label={ariaLabel} viewBox={`0 0 ${width} ${height}`} width="100%" height={height} className="block">
        {ticks.map((tk) => (
          <g key={tk}>
            <line x1={m.l} x2={width - m.r} y1={y(tk)} y2={y(tk)} style={{ stroke: "var(--grid)" }} strokeWidth={1} />
            <text x={m.l - 6} y={y(tk) + 4} textAnchor="end" fontSize={11} style={{ fill: "var(--muted)" }}>
              {tk}%
            </text>
          </g>
        ))}
        {bands.map(({ layer, y0, y1 }) => (
          <polygon key={layer.id} points={`${poly(y1, false)} ${poly(y0, true)}`} style={{ fill: layer.color }} />
        ))}
        {shownTicks.map((t) => (
          <text key={t.x} x={x(t.x)} y={height - 8} textAnchor="middle" fontSize={11} style={{ fill: "var(--muted)" }}>
            {t.label}
          </text>
        ))}
      </svg>
    </div>
  );
}
