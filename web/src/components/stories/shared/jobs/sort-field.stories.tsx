import type { Meta, StoryObj } from "@storybook/react-vite";

import { SortField } from "@/components/shared/jobs/filters/sort-field";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Sort field",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The order of the postings, newest found first by default: in the Filters panel of the Jobs toolbar.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Newest found first, the default. */
export const Default: Story = {
  name: "Newest found first",
  render: () => (
    <WithFilters>{(props) => <SortField {...props} />}</WithFilters>
  ),
};

/** The best fits first. */
export const BestFit: Story = {
  name: "Best fit first",
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, sort: "fit" }}>
      {(props) => <SortField {...props} />}
    </WithFilters>
  ),
};
