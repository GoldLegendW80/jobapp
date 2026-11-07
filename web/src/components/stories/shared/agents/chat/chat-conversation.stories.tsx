import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { ChatConversation } from "@/components/shared/agents/chat/chat-conversation";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import {
  ANSWER_SO_FAR,
  fakeCredential,
  fakeThread,
  LONG_CONVERSATION,
  UNANSWERED,
} from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Agents/Chat conversation",
  component: ChatConversation,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <div className="flex h-[36rem] max-w-md flex-col rounded-xl border bg-background">
        <Story />
      </div>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The conversation about one posting with the chat agent and the composer under it: the body of the Chat panel on a posting. A message sent here is answered live.",
      },
    },
  },
  args: { job: POSTING_DETAIL },
  beforeEach: () => {
    fakeCredential();
  },
} satisfies Meta<typeof ChatConversation>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two questions answered; send another one and watch the answer arrive. */
export const AConversation: Story = {
  name: "A conversation",
  beforeEach: () => {
    fakeThread();
  },
};

/** No message yet: three questions to start with. */
export const FirstQuestion: Story = {
  name: "Before the first question",
  beforeEach: () => {
    fakeThread({ none: true });
  },
};

/** The newest question is being answered: Enter holds the next message until it is done. */
export const AnswerBeingWritten: Story = {
  name: "Answer being written",
  beforeEach: () => {
    fakeThread({ writing: ANSWER_SO_FAR });
  },
};

/** The last question got no answer, and says why. */
export const UnansweredQuestion: Story = {
  name: "A question without an answer",
  beforeEach: () => {
    fakeThread({ messages: UNANSWERED });
  },
};

/** A long conversation: the newest messages only, the list scrolls. */
export const LongConversation: Story = {
  name: "Long conversation, older messages not shown",
  beforeEach: () => {
    fakeThread({ messages: LONG_CONVERSATION, truncated: true });
  },
};

/** No model credential saved: the composer is off and links to the Account page. */
export const NoModelCredential: Story = {
  name: "No model credential",
  beforeEach: () => {
    fakeThread({ none: true });
    server.use(
      http.get("*/api/me/model-credential", () =>
        HttpResponse.json(
          { detail: "No model credential is saved." },
          { status: 404 },
        ),
      ),
    );
  },
};

/** The conversation is read. */
export const Loading: Story = {
  name: "Loading",
  beforeEach: () => {
    fakeThread({ read: "loading" });
  },
};

/** The conversation could not be read. */
export const LoadFailed: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    fakeThread({ read: "error" });
  },
};

/** On a phone the panel takes the whole screen. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="flex h-full max-w-sm flex-col">
        <Story />
      </div>
    ),
  ],
  beforeEach: () => {
    fakeThread();
  },
};
