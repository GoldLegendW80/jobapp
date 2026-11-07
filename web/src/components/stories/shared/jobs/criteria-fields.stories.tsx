import type { Meta, StoryObj } from "@storybook/react-vite";

import { CriteriaFields } from "@/components/shared/jobs/search-setup/criteria-fields";
import { WithSearchForm } from "@/design-system/fixtures/job-harnesses";
import { findJobsSearch } from "@/design-system/fixtures/jobs-actions";
import { EMPTY, type Search } from "@/features/profile/api/profile-api";

/** The fields over `search`, in a column as More options lays them out. */
function Fields({ search = findJobsSearch() }: { search?: Search }) {
  return (
    <div className="flex max-w-xl flex-col gap-4">
      <WithSearchForm search={search}>
        {(form) => <CriteriaFields form={form} />}
      </WithSearchForm>
    </div>
  );
}

const meta = {
  title: "Shared/Jobs/Criteria fields",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The remote preference, the contract types and how recent a posting may be: under More options in step 2 of Find jobs.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Hybrid, permanent contracts, the last 30 days. */
export const Typical: Story = {
  name: "Criteria set",
  render: () => <Fields />,
};

/** A first search: nothing set, the default number of days. */
export const NothingSet: Story = {
  name: "Nothing set",
  render: () => <Fields search={structuredClone(EMPTY.search)} />,
};

/** On a phone the contract boxes wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <Fields />
    </div>
  ),
};
