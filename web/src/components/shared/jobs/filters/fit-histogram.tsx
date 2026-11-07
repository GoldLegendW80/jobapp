import { useId, type KeyboardEvent } from "react";
import {
  Bar,
  BarChart,
  DefaultZIndexes,
  LabelList,
  Rectangle,
  usePlotArea,
  XAxis,
  ZIndexLayer,
  type BarShapeProps,
} from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { cn } from "@/lib/utils";

import type { FitBucket } from "@/features/jobs/api/jobs-api";
import { fitTone, type FitTone } from "@/features/jobs/helpers/labels";

/** A count with thousands separators ("5,100"). */
const count = (n: number) => n.toLocaleString("en-GB");

/** The fit scale of the badges, as colours of the design system (they follow the theme). */
const FIT_CONFIG = {
  count: { label: "Postings" },
  top: { label: "Top fit", color: "var(--success)" },
  good: {
    label: "Good fit",
    color: "color-mix(in oklch, var(--success) 60%, transparent)",
  },
  fair: {
    label: "Fair fit",
    color: "color-mix(in oklch, var(--muted-foreground) 40%, transparent)",
  },
  low: {
    label: "Low fit",
    color: "color-mix(in oklch, var(--muted-foreground) 25%, transparent)",
  },
  none: {
    label: "Not scored",
    color: "color-mix(in oklch, var(--muted-foreground) 15%, transparent)",
  },
} satisfies ChartConfig;

/** A range set by hand (40 to 100) keeps the bars it covers bright, as a picked bar does. */
const inRange = (
  bucket: FitBucket,
  fitMin: number | null,
  fitMax: number | null,
) => (fitMin ?? 0) <= bucket.low && bucket.high <= (fitMax ?? 100);

interface Datum extends FitBucket {
  range: string;
  tone: FitTone;
}

/**
 * Fit distribution of the filtered rows: ten buckets (0 to 9 ... 90 to 100) drawn as bars of a
 * shadcn Chart. Each bar is a button that narrows the list to its range (a second click clears
 * it), named for screen readers with its range and count, and reached with Tab. Unscored rows are
 * counted beside the chart; the api cannot filter on them, so that count is not a button.
 * Recharts is large: the filters panel loads this file only when it opens.
 */
export function FitHistogram({
  buckets,
  unscored,
  fitMin,
  fitMax,
  onSelect,
  loading,
}: {
  buckets: FitBucket[];
  unscored: number;
  fitMin: number | null;
  fitMax: number | null;
  onSelect: (low: number, high: number) => void;
  loading: boolean;
}) {
  const titleId = useId();
  const rangeActive = fitMin !== null || fitMax !== null;
  const data: Datum[] = buckets.map((bucket) => ({
    ...bucket,
    range: String(bucket.low),
    tone: fitTone(bucket.low),
  }));
  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-2 rounded-lg border bg-card p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={titleId} className="text-sm font-medium">
          Fit distribution
        </h2>
        <p className="text-xs text-muted-foreground">
          {count(unscored)} unscored
        </p>
      </div>
      <ChartContainer
        config={FIT_CONFIG}
        role="group"
        aria-label="Postings per fit range"
        aria-busy={loading}
        className={cn("aspect-auto h-36 w-full", loading && "opacity-50")}
      >
        <BarChart
          data={data}
          accessibilityLayer={false}
          margin={{ top: 16, right: 0, bottom: 0, left: 0 }}
        >
          <XAxis
            dataKey="range"
            tickLine={false}
            axisLine={false}
            tickMargin={6}
            interval={0}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                hideIndicator
                labelFormatter={(_, payload) => {
                  const bucket = data.find(
                    (one) => one === payload[0]?.payload,
                  );
                  return bucket ? `Fit ${bucket.low} to ${bucket.high}` : "";
                }}
              />
            }
          />
          <Bar
            dataKey="count"
            isAnimationActive={false}
            minPointSize={(value) => (Number(value) > 0 ? 3 : 0)}
            shape={(props: BarShapeProps) => {
              const bucket = data[props.index];
              const pressed =
                bucket !== undefined &&
                fitMin === bucket.low &&
                fitMax === bucket.high;
              return (
                <Rectangle
                  {...props}
                  radius={[4, 4, 0, 0]}
                  fill={`var(--color-${bucket?.tone ?? "none"})`}
                  fillOpacity={
                    rangeActive && bucket && !inRange(bucket, fitMin, fitMax)
                      ? 0.35
                      : 1
                  }
                  stroke={pressed ? "var(--primary)" : "none"}
                  strokeWidth={pressed ? 2 : 0}
                />
              );
            }}
          >
            <LabelList
              dataKey="count"
              position="top"
              formatter={(value) => count(Number(value))}
              className="fill-muted-foreground tabular-nums"
              fontSize={10}
            />
          </Bar>
          <BarButtons
            data={data}
            fitMin={fitMin}
            fitMax={fitMax}
            onSelect={onSelect}
          />
        </BarChart>
      </ChartContainer>
    </section>
  );
}

/**
 * One button per range over its whole column, above the bars and their counts: the click target,
 * the focus ring, and the name. Keyed by the range, so the focus stays on it while the counts
 * reload (Recharts keys its bars by their values).
 */
function BarButtons({
  data,
  fitMin,
  fitMax,
  onSelect,
}: {
  data: Datum[];
  fitMin: number | null;
  fitMax: number | null;
  onSelect: (low: number, high: number) => void;
}) {
  const plot = usePlotArea();
  if (!plot || data.length === 0) return null;
  const band = plot.width / data.length;
  return (
    <ZIndexLayer zIndex={DefaultZIndexes.label + 1}>
      {data.map((bucket, index) => {
        const select = () => {
          onSelect(bucket.low, bucket.high);
        };
        const onKeyDown = (event: KeyboardEvent) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select();
          }
        };
        return (
          <rect
            key={bucket.low}
            role="button"
            tabIndex={0}
            aria-pressed={fitMin === bucket.low && fitMax === bucket.high}
            aria-label={`Fit ${bucket.low} to ${bucket.high}: ${count(bucket.count)} postings`}
            x={plot.x + index * band + 1}
            y={plot.y}
            width={Math.max(0, band - 2)}
            height={plot.height}
            rx={4}
            onClick={select}
            onKeyDown={onKeyDown}
            className="cursor-pointer fill-transparent stroke-transparent stroke-[3px] outline-none focus-visible:stroke-ring/50"
          />
        );
      })}
    </ZIndexLayer>
  );
}
