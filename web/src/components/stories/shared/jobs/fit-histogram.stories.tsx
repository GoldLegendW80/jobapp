import type { Meta, StoryObj } from "@storybook/react-vite";

import { FitHistogram } from "@/components/shared/jobs/filters/fit-histogram";
import {
  EMPTY_BUCKETS,
  FIT_BUCKETS,
} from "@/design-system/fixtures/job-filters";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS, toggleFitRange } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Fit histogram",
  component: FitHistogram,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "How the fit of the filtered postings spreads over ten ranges, on the shadcn Chart: each bar is a button (Tab, Enter) that narrows the list to its range. In the Filters panel of the Jobs toolbar, loaded when the panel opens.",
      },
    },
  },
  args: {
    buckets: FIT_BUCKETS,
    unscored: 12,
    fitMin: null,
    fitMax: null,
    loading: false,
    onSelect: () => undefined,
  },
  render: (args) => (
    <div className="max-w-md">
      <WithFilters
        initial={{ ...EMPTY_FILTERS, fitMin: args.fitMin, fitMax: args.fitMax }}
      >
        {({ filters, onChange }) => (
          <FitHistogram
            {...args}
            fitMin={filters.fitMin}
            fitMax={filters.fitMax}
            onSelect={(low, high) => {
              onChange(toggleFitRange(filters, low, high));
            }}
          />
        )}
      </WithFilters>
    </div>
  ),
} satisfies Meta<typeof FitHistogram>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The spread of a few weeks of postings; a bar picks its range, a second click clears it. */
export const Typical: Story = { name: "The spread of fit" };

/** One range picked: the other bars fade. */
export const RangePicked: Story = {
  name: "A range picked",
  args: { fitMin: 70, fitMax: 79 },
};

/** While the counts load, the bars hold their place, faded. */
export const Loading: Story = {
  name: "Loading",
  args: { buckets: EMPTY_BUCKETS, unscored: 0, loading: true },
};

/** Nothing scored yet: empty ranges, and the unscored count beside the title. */
export const NothingScored: Story = {
  name: "Nothing scored yet",
  args: { buckets: EMPTY_BUCKETS, unscored: 37 },
};

/** At a phone's width, the ten bars still fit. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-xs">
        <Story />
      </div>
    ),
  ],
};
