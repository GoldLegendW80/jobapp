import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ChatThread } from "@/components/shared/agents/chat/chat-thread";
import { ChatMessages } from "@/components/shared/documents/chat-panel/chat-messages";
import { CHAT_MESSAGES } from "@/design-system/fixtures/documents";

const LONG = Array.from({ length: 5 }, (_, round) =>
  CHAT_MESSAGES.map((one) => ({ ...one, id: one.id + round * 10 })),
).flat();

const meta = {
  title: "Shared/Documents/Chat messages",
  component: ChatMessages,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A document's conversation, in the chat's thread: the person's messages on the right, the assistant's on the left with the version each made, and the answer being written with Stop. In the chat of a CV or a letter.",
      },
    },
  },
  args: { messages: CHAT_MESSAGES, pending: null },
  render: (args) => (
    <ChatThread fit scrollTestId="document-chat-scroll">
      <ChatMessages {...args} />
    </ChatThread>
  ),
  decorators: [
    (Story) => (
      <div className="max-w-sm rounded-xl bg-card p-4">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ChatMessages>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A change asked and answered. */
export const Answered: Story = { name: "A change answered" };

/** The answer is being written: Stop ends it. */
export const BeingWritten: Story = {
  name: "An answer being written",
  args: {
    messages: CHAT_MESSAGES.slice(0, 1),
    pending: { label: "Changing your document", stopping: false, onStop: fn() },
  },
};

/** Waiting for a free assistant: Stop still ends it. */
export const Queued: Story = {
  name: "Waiting for a free assistant",
  args: {
    messages: CHAT_MESSAGES.slice(0, 1),
    pending: {
      label: "Waiting for a free assistant",
      stopping: false,
      onStop: fn(),
    },
  },
};

/** A long conversation scrolls in its own box. */
export const Long: Story = {
  name: "A long conversation",
  args: { messages: LONG },
};
