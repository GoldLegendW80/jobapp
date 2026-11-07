import type { Meta, StoryObj } from "@storybook/react-vite";

import { FilterChips } from "@/components/shared/jobs/filters/filter-chips";
import { SOME_FILTERS } from "@/design-system/fixtures/job-filters";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Filter chips",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One chip per active filter, each removable, and Clear filters: under the Jobs toolbar on List and Map. Nothing shows while no filter is set.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every kind of filter at once; remove one, or clear them all. */
export const Several: Story = {
  name: "Several filters",
  render: () => (
    <WithFilters initial={SOME_FILTERS}>
      {(props) => <FilterChips {...props} />}
    </WithFilters>
  ),
};

/** One status picked. */
export const One: Story = {
  name: "One filter",
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, statuses: ["applied"] }}>
      {(props) => <FilterChips {...props} />}
    </WithFilters>
  ),
};

/** On a phone the chips wrap and a long one is cut short. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <WithFilters
        initial={{
          ...SOME_FILTERS,
          company: "Compagnie Fluviale et Maritime du Rhône et de la Saône",
        }}
      >
        {(props) => <FilterChips {...props} />}
      </WithFilters>
    </div>
  ),
};
