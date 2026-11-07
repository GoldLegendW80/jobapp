import type { Meta, StoryObj } from "@storybook/react-vite";

import { ConnectionState } from "@/components/shared/agents/run/connection-state";

const meta = {
  title: "Shared/Agents/Connection state",
  component: ConnectionState,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The state of the live connection to a run's steps, under the run's title in the run panel: it reconnects by itself after a network loss.",
      },
    },
  },
  args: { state: "live" },
} satisfies Meta<typeof ConnectionState>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The panel opens the stream: a spinner until the first answer. */
export const Connecting: Story = {
  name: "Connecting",
  args: { state: "connecting" },
};

/** Steps arrive as they happen. */
export const Live: Story = { name: "Live", args: { state: "live" } };

/** The connection dropped: it comes back by itself and picks up where it stopped. */
export const Reconnecting: Story = {
  name: "Reconnecting after a network loss",
  args: { state: "reconnecting" },
};

/** The run ended: nothing more will come. */
export const Ended: Story = { name: "Ended", args: { state: "ended" } };
