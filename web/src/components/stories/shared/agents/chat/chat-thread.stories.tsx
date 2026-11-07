import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { ChatEmptyState } from "@/components/shared/agents/chat/chat-empty-state";
import { ChatThread } from "@/components/shared/agents/chat/chat-thread";
import { ChatTurn } from "@/components/shared/agents/chat/chat-turn";
import { Card, CardContent } from "@/components/ui/card";
import { MessageScrollerItem } from "@/components/ui/message-scroller";
import { LONG_CONVERSATION } from "@/design-system/fixtures/shared-agents";

const turns = (count: number) =>
  LONG_CONVERSATION.slice(0, count).map((message) => (
    <MessageScrollerItem
      key={message.id}
      messageId={String(message.id)}
      scrollAnchor={message.role === "user"}
    >
      <ChatTurn from={message.role === "user" ? "user" : "agent"}>
        {message.text}
      </ChatTurn>
    </MessageScrollerItem>
  ));

const meta = {
  title: "Shared/Agents/Chat thread",
  component: ChatThread,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The scrolling frame of every chat (MessageScroller): it opens on the newest message, follows the answer being written, holds a question just sent in view, offers a button back to the newest message after scrolling up, and says when the conversation loads, failed or is empty. Fills the Chat panel of a posting; fits in the card of a document's chat.",
      },
    },
  },
  args: { scrollTestId: "chat-scroll", children: turns(2) },
} satisfies Meta<typeof ChatThread>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A side panel of a fixed height, as the Chat panel of a posting. */
const inAPanel: NonNullable<Story["decorators"]> = [
  (Story) => (
    <div className="flex h-96 max-w-md flex-col rounded-xl border">
      <Story />
    </div>
  ),
];

/** A short exchange: nothing to scroll. */
export const AShortConversation: Story = {
  name: "A short conversation",
  decorators: inAPanel,
};

/** A long one opens on its newest message; scroll up and the button brings you back. */
export const ALongConversation: Story = {
  name: "A long conversation",
  decorators: inAPanel,
  args: { children: turns(LONG_CONVERSATION.length) },
};

/** While the conversation is read. */
export const Loading: Story = {
  name: "Loading",
  decorators: inAPanel,
  args: { loading: true, children: null },
};

/** The conversation could not be read: why, in the server's words. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  decorators: inAPanel,
  args: {
    error: "The service is busy. Try again in a moment.",
    children: null,
  },
};

/** No message yet: what the chat does, and examples to start it. */
export const Empty: Story = {
  name: "Before the first message",
  decorators: inAPanel,
  args: {
    children: null,
    empty: (
      <ChatEmptyState
        testId="chat-empty"
        title="Ask anything about this posting."
        description="The agent reads the posting and your profile to answer. It changes nothing, and it only knows what they say."
        examples={["Does this role fit my profile?", "What should I check?"]}
        examplesLabel="Example questions"
        onPick={fn()}
      />
    ),
  },
};

/** In a card (a document's chat): as tall as its messages up to half the screen. */
export const InACard: Story = {
  name: "In a card",
  args: {
    fit: true,
    scrollTestId: "document-chat-scroll",
    children: turns(LONG_CONVERSATION.length),
  },
  decorators: [
    (Story) => (
      <Card className="max-w-sm">
        <CardContent>
          <Story />
        </CardContent>
      </Card>
    ),
  ],
};

/** On a phone. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { children: turns(LONG_CONVERSATION.length) },
  decorators: [
    (Story) => (
      <div className="flex h-[32rem] max-w-sm flex-col">
        <Story />
      </div>
    ),
  ],
};
