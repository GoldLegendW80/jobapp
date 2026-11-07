import type { Meta, StoryObj } from "@storybook/react-vite";

import { CompanyField } from "@/components/shared/jobs/filters/company-field";
import { WithFilters } from "@/design-system/fixtures/job-harnesses";
import { EMPTY_FILTERS } from "@/features/jobs/helpers/filters";

const meta = {
  title: "Shared/Jobs/Company field",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Narrows the postings to one company, applied after a pause in typing or on Enter: in the Filters panel of the Jobs toolbar, with its label above.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** In the Filters panel: its label above, "Any company" while empty. */
export const Labelled: Story = {
  name: "In the Filters panel",
  render: () => (
    <div className="max-w-sm">
      <WithFilters>
        {(props) => <CompanyField {...props} labelled />}
      </WithFilters>
    </div>
  ),
};

/** A company already picked. */
export const CompanyPicked: Story = {
  name: "A company picked",
  render: () => (
    <div className="max-w-sm">
      <WithFilters initial={{ ...EMPTY_FILTERS, company: "Lumen Freight" }}>
        {(props) => <CompanyField {...props} labelled />}
      </WithFilters>
    </div>
  ),
};

/** Without `labelled`, the label is read by screen readers only and the placeholder names it. */
export const Compact: Story = {
  name: "Without a visible label",
  render: () => (
    <div className="max-w-60">
      <WithFilters>{(props) => <CompanyField {...props} />}</WithFilters>
    </div>
  ),
};
