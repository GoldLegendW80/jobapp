import type { Meta, StoryObj } from "@storybook/react-vite";

import { StatusBadge } from "@/components/shared/jobs/badges";
import { STATUSES } from "@/features/jobs/helpers/labels";

const meta = {
  title: "Shared/Jobs/Status badge",
  component: StatusBadge,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Where a posting stands in the person's pipeline, from Found to Interview: in the Jobs list, on the map's cards and on a posting.",
      },
    },
  },
  args: { status: "shortlisted" },
  argTypes: {
    status: { control: "select", options: [...STATUSES] },
  },
} satisfies Meta<typeof StatusBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A shortlisted posting. */
export const Typical: Story = { name: "A shortlisted posting" };

/** The pipeline in order: only an interview is solid, a CV ready stands out, a no is red. */
export const EveryStatus: Story = {
  name: "Every status",
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      {STATUSES.map((status) => (
        <StatusBadge key={status} status={status} />
      ))}
    </div>
  ),
};

/** A status a newer api sends and this build does not know: still readable, never blank. */
export const UnknownStatus: Story = {
  name: "A status this build does not know",
  args: { status: "offer_received" },
};
