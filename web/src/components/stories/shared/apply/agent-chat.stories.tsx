import type { Meta, StoryObj } from "@storybook/react-vite";

import { AgentChat } from "@/components/shared/apply/agent-chat";
import {
  chatWith,
  SIGNIN_ASK,
  STEERED_ASK,
} from "@/design-system/fixtures/agent-wait";

const meta = {
  title: "Shared/Apply/Agent chat",
  component: AgentChat,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The conversation with the apply agent of one application (A25): each ask and each answer in order, who said it and how each ask ended, then the standing line on passwords. The assistant's words are plain text on one line, bounded, never a link.",
      },
    },
  },
  args: { chat: chatWith(SIGNIN_ASK) },
} satisfies Meta<typeof AgentChat>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two earlier asks, an answer, and the open ask last. */
export const Conversation: Story = {};

/** Words a page steered, and a long unbroken string, stay inside the bubble. */
export const SteeredAndLong: Story = {
  args: {
    chat: [
      ...chatWith(STEERED_ASK, "cancelled"),
      {
        at: null,
        who: "person",
        kind: "answer",
        text: `https://careers.example.org/${"x".repeat(400)}`,
        end: null,
      },
    ],
  },
};

/** No ask yet: only the standing line. */
export const Empty: Story = { args: { chat: [] } };
