import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { FitHistogram } from "@/features/jobs/components/fit-histogram";
import {
  FitBadge,
  PayText,
  StatusBadge,
} from "@/components/shared/jobs/badges";
import { KpiTiles } from "@/features/jobs/components/kpi-tiles";
import { STATUSES, type JobStatus } from "@/features/jobs/helpers/labels";

const meta = {
  title: "Patterns/Posting signals",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The three things a person scans a posting for: how well it fits, where it is in their pipeline, and what it pays. These are the app's own components (features/jobs), built only from Badge, Button and the meaning tokens. Each says its value in words, so color is never the only clue.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Fit on one scale everywhere: 85 and up is top (solid), 70 good, 50 fair, below that low. A fit set by hand says so. */
export const Fit: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <FitBadge fit={91} />
      <FitBadge fit={76} />
      <FitBadge fit={58} />
      <FitBadge fit={31} />
      <FitBadge fit={null} />
      <FitBadge fit={64} overridden />
    </div>
  ),
};

/** The pipeline in order. Only an interview is solid: it is the state worth celebrating. A CV that is ready is highlighted: it waits for the person's Apply. */
export const Pipeline: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      {STATUSES.map((status) => (
        <StatusBadge key={status} status={status} />
      ))}
    </div>
  ),
};

/** Pay as the posting states it, in words with separators. When it states none, the app says so instead of guessing. */
export const Pay: Story = {
  render: () => (
    <div className="flex flex-col gap-2 text-sm">
      <PayText
        job={{
          pay_min: 55000,
          pay_max: 65000,
          pay_currency: "EUR",
          pay_period: "year",
        }}
      />
      <PayText
        job={{
          pay_min: 600,
          pay_max: 600,
          pay_currency: "EUR",
          pay_period: "day",
        }}
      />
      <PayText job={{}} />
    </div>
  ),
};

const COUNTS: Record<JobStatus, number> = {
  found: 128,
  shortlisted: 14,
  cv_ready: 3,
  staged: 2,
  applied: 21,
  interview: 2,
  rejected: 9,
  closed: 4,
  skipped: 57,
};

/** The pipeline as filters on the Jobs page: a click shows that status alone. Try it, then look at the loading and unavailable stories. */
export const PipelineTiles: Story = {
  render: function PipelineTilesDemo() {
    const [selected, setSelected] = useState<JobStatus[]>(["shortlisted"]);
    return (
      <KpiTiles
        counts={COUNTS}
        selected={selected}
        onSelect={(status) => {
          setSelected((current) => (current.includes(status) ? [] : [status]));
        }}
        loading={false}
      />
    );
  },
};

/** While the list loads, the tiles hold their shape. */
export const PipelineTilesLoading: Story = {
  render: () => (
    <KpiTiles
      counts={undefined}
      selected={[]}
      onSelect={() => undefined}
      loading
    />
  ),
};

/** When the list could not be loaded, the tiles say so instead of pulsing forever. */
export const PipelineTilesUnavailable: Story = {
  render: () => (
    <KpiTiles
      counts={undefined}
      selected={[]}
      onSelect={() => undefined}
      loading={false}
      unavailable
    />
  ),
};

const BUCKETS = [
  { low: 0, high: 9, count: 3 },
  { low: 10, high: 19, count: 6 },
  { low: 20, high: 29, count: 11 },
  { low: 30, high: 39, count: 19 },
  { low: 40, high: 49, count: 26 },
  { low: 50, high: 59, count: 31 },
  { low: 60, high: 69, count: 24 },
  { low: 70, high: 79, count: 15 },
  { low: 80, high: 89, count: 7 },
  { low: 90, high: 100, count: 2 },
];

/** The spread of fit across every posting, on the same color scale as the badges. A bar filters the list to its range. */
export const FitSpread: Story = {
  render: function FitSpreadDemo() {
    const [range, setRange] = useState<[number, number] | null>(null);
    return (
      <div className="max-w-xl">
        <FitHistogram
          buckets={BUCKETS}
          unscored={12}
          fitMin={range?.[0] ?? null}
          fitMax={range?.[1] ?? null}
          onSelect={(low, high) => {
            setRange([low, high]);
          }}
          loading={false}
        />
      </div>
    );
  },
};
