import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import {
  Bar,
  BarChart,
  LabelList,
  Rectangle,
  XAxis,
  YAxis,
  type BarShapeProps,
} from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { FIT_BUCKETS } from "@/design-system/fixtures/job-filters";
import { fitTone } from "@/features/jobs/helpers/labels";

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

const BUCKETS = FIT_BUCKETS.map((bucket) => ({
  ...bucket,
  range: String(bucket.low),
  tone: fitTone(bucket.low),
}));

const PLACES_CONFIG = {
  count: { label: "Postings", color: "var(--brand)" },
} satisfies ChartConfig;

const PLACES = [
  { place: "Lyon", count: 64 },
  { place: "Paris", count: 51 },
  { place: "Remote, France", count: 38 },
  { place: "Grenoble", count: 12 },
  { place: "Annecy", count: 5 },
];

function FitHistogram({ buckets = BUCKETS }: { buckets?: typeof BUCKETS }) {
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="flex flex-col gap-2">
      <ChartContainer
        config={FIT_CONFIG}
        className="aspect-auto h-36 w-full"
        aria-label="Postings per fit range"
      >
        <BarChart
          accessibilityLayer
          data={buckets}
          margin={{ top: 16, left: 0, right: 0 }}
        >
          <XAxis
            dataKey="range"
            tickLine={false}
            axisLine={false}
            tickMargin={6}
          />
          <ChartTooltip
            cursor={false}
            content={
              <ChartTooltipContent
                hideIndicator
                labelFormatter={(_, payload) => {
                  const datum: unknown = payload[0]?.payload;
                  const bucket = BUCKETS.find((one) => one === datum);
                  return bucket
                    ? `Fit ${String(bucket.low)} to ${String(bucket.high)}`
                    : "";
                }}
              />
            }
          />
          <Bar
            dataKey="count"
            radius={[4, 4, 0, 0]}
            onClick={(_, index) => {
              setPicked((now) => (now === index ? null : index));
            }}
            shape={(props: BarShapeProps) => (
              <Rectangle
                {...props}
                fill={`var(--color-${buckets[props.index]?.tone ?? "none"})`}
                fillOpacity={
                  picked === null || picked === props.index ? 1 : 0.35
                }
                className="cursor-pointer"
              />
            )}
          >
            <LabelList
              dataKey="count"
              position="top"
              className="fill-muted-foreground"
              fontSize={10}
            />
          </Bar>
        </BarChart>
      </ChartContainer>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {picked === null
          ? "Click a bar to show that fit range only."
          : `Showing fit ${String(BUCKETS[picked]?.low)} to ${String(BUCKETS[picked]?.high)}.`}
      </p>
    </div>
  );
}

const meta = {
  title: "Components/Data display/Chart",
  component: ChartContainer,
  tags: ["autodocs"],
  args: { config: FIT_CONFIG, children: <></> },
  parameters: {
    docs: {
      description: {
        component:
          "A chart of the app's own figures, drawn by Recharts inside ChartContainer: the fit spread of the postings in view, the postings per place. Colours come from the config as tokens (var(--success), var(--brand)), so they follow the theme; the tooltip and the axis use the system's type. A chart that filters keeps a keyboard way to the same filter beside it (the fit range fields).",
      },
    },
  },
} satisfies Meta<typeof ChartContainer>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The fit spread of the list: ten ranges on the badges' scale; a bar narrows the list to its range. */
export const Typical: Story = {
  render: () => (
    <div className="max-w-md">
      <FitHistogram />
    </div>
  ),
};

/** Postings per place, as bars along their names. */
export const Horizontal: Story = {
  render: () => (
    <ChartContainer
      config={PLACES_CONFIG}
      className="aspect-auto h-52 w-full max-w-md"
    >
      <BarChart
        accessibilityLayer
        data={PLACES}
        layout="vertical"
        margin={{ left: 8, right: 24 }}
      >
        <YAxis
          dataKey="place"
          type="category"
          tickLine={false}
          axisLine={false}
          width={110}
        />
        <XAxis dataKey="count" type="number" hide />
        <ChartTooltip
          cursor={false}
          content={<ChartTooltipContent hideIndicator />}
        />
        <Bar dataKey="count" fill="var(--color-count)" radius={4}>
          <LabelList
            dataKey="count"
            position="right"
            className="fill-muted-foreground"
            fontSize={11}
          />
        </Bar>
      </BarChart>
    </ChartContainer>
  ),
};

/** Nothing scored yet: the ranges stay, flat, and a sentence says why. */
export const Empty: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-2">
      <FitHistogram
        buckets={BUCKETS.map((bucket) => ({ ...bucket, count: 0 }))}
      />
      <p className="text-sm text-muted-foreground">
        No posting in this view has a fit score yet.
      </p>
    </div>
  ),
};

/** Loading: a block of the chart's height holds its place. */
export const Loading: Story = {
  render: () => (
    <div className="max-w-md" role="status" aria-label="Loading the fit spread">
      <Skeleton className="h-36 w-full" />
    </div>
  ),
};

/** On a phone the chart takes the width; the ten ranges still fit. */
export const OnAPhone: Story = {
  render: () => (
    <div className="max-w-sm">
      <FitHistogram />
    </div>
  ),
};
