import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ChatMessageList } from "@/components/shared/agents/chat/chat-message-list";
import { ChatThread } from "@/components/shared/agents/chat/chat-thread";
import { makeRun, RUN_ID } from "@/design-system/fixtures/runs";
import {
  CONVERSATION,
  fakeRuns,
  LONG_CONVERSATION,
  UNANSWERED,
  WRITING,
} from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Agents/Chat message list",
  component: ChatMessageList,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <div className="flex h-[32rem] max-w-md flex-col rounded-xl border">
        <ChatThread scrollTestId="chat-scroll">
          <Story />
        </ChatThread>
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The messages of the chat about a posting, oldest first, with the answer being written last, as the items of its thread: the body of the Chat panel on a posting.",
      },
    },
  },
  args: {
    messages: CONVERSATION,
    truncated: false,
    activeRun: null,
    onAnswerEnd: fn(),
  },
} satisfies Meta<typeof ChatMessageList>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two questions and their answers. */
export const AConversation: Story = { name: "A conversation" };

/** The newest question is being answered. */
export const AnswerBeingWritten: Story = {
  name: "Answer being written",
  args: { activeRun: RUN_ID },
  beforeEach: () => {
    fakeRuns({
      run: makeRun({
        agent: "chat",
        status: "running",
        finished_at: null,
        duration_s: null,
      }),
      steps: WRITING.steps,
      writing: WRITING.part,
    });
  },
};

/** The last question got no answer, and says why. */
export const UnansweredQuestion: Story = {
  name: "A question without an answer",
  args: { messages: UNANSWERED },
};

/** The server sent the newest messages only: a divider says so on top. */
export const OlderMessagesNotShown: Story = {
  name: "Older messages not shown",
  args: { messages: LONG_CONVERSATION, truncated: true },
};
