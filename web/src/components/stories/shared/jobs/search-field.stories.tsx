import type { Meta, StoryObj } from "@storybook/react-vite";

import { SearchField } from "@/components/shared/jobs/filters/search-field";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Search field",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The search box of the Jobs toolbar, over company, title, location and notes, applied after a pause in typing or on Enter: on List and Map.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Empty, with its placeholder saying what it searches. */
export const Empty: Story = {
  name: "Nothing searched",
  render: () => (
    <WithFilters>
      {(props) => <SearchField {...props} className="w-80" />}
    </WithFilters>
  ),
};

/** Words already searched, as a link to the list keeps them. */
export const Searched: Story = {
  name: "Words searched",
  render: () => (
    <WithFilters initial={{ ...EMPTY_FILTERS, q: "data engineer" }}>
      {(props) => <SearchField {...props} className="w-80" />}
    </WithFilters>
  ),
};

/** On a phone it takes the row's width. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <WithFilters>{(props) => <SearchField {...props} />}</WithFilters>
    </div>
  ),
};
