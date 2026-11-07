import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { HeldMessage } from "@/components/shared/agents/chat/held-message";

const meta = {
  title: "Shared/Agents/Held message",
  component: HeldMessage,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A message the person sent with Enter while the previous answer was still being written: it waits, dashed, with Cancel, under the conversation of the Chat panel.",
      },
    },
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
  args: { text: "And what about the salary?", onCancel: fn() },
} satisfies Meta<typeof HeldMessage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Pending, with Cancel. */
export const Pending: Story = { name: "Waiting to be sent" };

/** A long message wraps inside its bubble. */
export const LongMessage: Story = {
  name: "Long message",
  args: {
    text: "Could you also check whether the team works remotely on Fridays, whether the on call rotation includes weekends, and whether the posting says anything about a relocation package for someone moving from Lille?",
  },
};
