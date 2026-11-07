import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ChatEmptyState } from "@/components/shared/agents/chat/chat-empty-state";

const meta = {
  title: "Shared/Agents/Chat empty state",
  component: ChatEmptyState,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A conversation with no message yet: what the chat does, and examples that fill the prompt. In the Chat panel of a posting and the chat of a CV or a letter, before the first message.",
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
  args: {
    testId: "chat-empty",
    title: "Ask anything about this posting.",
    description:
      "The agent reads the posting and your profile to answer. It changes nothing, and it only knows what they say.",
    examples: [
      "Does this role fit my profile?",
      "What should I stress in my application?",
      "What should I check before applying?",
    ],
    examplesLabel: "Example questions",
    onPick: fn(),
  },
} satisfies Meta<typeof ChatEmptyState>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A click on a question puts it in the composer. */
export const FirstQuestion: Story = { name: "Before the first question" };

/** The chat of a document: no title (its section has one), examples of a change. */
export const FirstChange: Story = {
  name: "Before the first change",
  args: {
    testId: "document-chat-empty",
    title: undefined,
    description:
      "The assistant changes what you ask and nothing else; the fact check removes any claim your profile does not state.",
    examples: [
      "Make the summary shorter",
      "Use the sidebar template",
      "Put my most relevant experience first",
    ],
    examplesLabel: "Examples",
  },
};

/** On a phone, the panel takes the whole screen. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
