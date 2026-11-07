import type { Meta, StoryObj } from "@storybook/react-vite";

import { ChatMessage } from "@/components/shared/agents/chat/chat-message";
import {
  CONVERSATION,
  UNANSWERED,
} from "@/design-system/fixtures/shared-agents";
import type { ChatMessage as Message } from "@/features/agents/api/chat-api";

const [QUESTION, ANSWER] = CONVERSATION as [Message, Message];
const FAILED = UNANSWERED.at(-1) as Message;

const meta = {
  title: "Shared/Agents/Chat message",
  component: ChatMessage,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A stored message of the chat about a posting, and why a question of the person got no answer: in the conversation of the Chat panel on a posting.",
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
  args: { message: QUESTION, messages: CONVERSATION },
} satisfies Meta<typeof ChatMessage>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The person's question, on the right. */
export const YourQuestion: Story = { name: "Your question" };

/** The agent's answer, on the left. */
export const TheAgentsAnswer: Story = {
  name: "The agent's answer",
  args: { message: ANSWER },
};

/** The agent stored an answer without text. */
export const NoText: Story = {
  name: "The agent gave no text",
  args: { message: { ...ANSWER, text: "" } },
};

/** The run failed before it answered: the reason shows under the question. */
export const NoAnswerFailed: Story = {
  name: "No answer, the agent failed",
  args: { message: FAILED, messages: UNANSWERED },
};

/** The person stopped the answer. */
export const NoAnswerStopped: Story = {
  name: "No answer, it was stopped",
  args: {
    message: { ...FAILED, run_status: "cancelled", error: null },
    messages: UNANSWERED,
  },
};
