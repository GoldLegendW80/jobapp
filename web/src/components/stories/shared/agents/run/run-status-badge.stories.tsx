import type { Meta, StoryObj } from "@storybook/react-vite";

import { RunStatusBadge } from "@/components/shared/agents/run/run-status-badge";
import { runStatuses } from "@/features/agents/api/runs-api";

const meta = {
  title: "Shared/Agents/Run status badge",
  component: RunStatusBadge,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Where an agent run stands, in its meaning color: beside the run's title in the run panel and on each row of Assistant activity.",
      },
    },
  },
  args: { status: "done" },
} satisfies Meta<typeof RunStatusBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every status side by side: waiting in grey, at work in blue with a spinner, done in green, failed in red. */
export const EveryStatus: Story = {
  name: "Every status",
  render: () => (
    <div className="flex flex-wrap gap-2">
      {runStatuses.map((status) => (
        <RunStatusBadge key={status} status={status} />
      ))}
    </div>
  ),
};

export const Queued: Story = { name: "Queued", args: { status: "queued" } };

export const Running: Story = { name: "Running", args: { status: "running" } };

export const Done: Story = { name: "Done", args: { status: "done" } };

export const Failed: Story = { name: "Failed", args: { status: "failed" } };

export const Cancelled: Story = {
  name: "Cancelled",
  args: { status: "cancelled" },
};
