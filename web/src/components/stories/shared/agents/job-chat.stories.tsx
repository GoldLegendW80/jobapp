import type { Meta, StoryObj } from "@storybook/react-vite";

import { JobChat } from "@/components/shared/agents/job-chat";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import {
  ANSWER_SO_FAR,
  fakeCredential,
  fakeThread,
} from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";

const AT = `/jobs/${POSTING_DETAIL.id}`;

const meta = {
  title: "Shared/Agents/Job chat",
  component: JobChat,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: AT,
    docs: {
      description: {
        component:
          "The Chat button of a posting's page and its side panel (the whole screen on a phone), holding the conversation about the posting with the chat agent. The panel stays open across a reload (?chat=open in the address).",
      },
    },
  },
  args: { job: POSTING_DETAIL },
  beforeEach: () => {
    fakeCredential();
    fakeThread();
  },
} satisfies Meta<typeof JobChat>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The button in the posting's header; a click opens the panel. */
export const ChatButton: Story = { name: "Chat button" };

/** The panel open on a conversation: send a question and watch the answer arrive. */
export const PanelOpen: Story = {
  name: "Panel open on a conversation",
  parameters: { at: `${AT}?chat=open` },
};

/** The panel open before the first question. */
export const FirstQuestion: Story = {
  name: "Panel open before the first question",
  parameters: { at: `${AT}?chat=open` },
  beforeEach: () => {
    fakeThread({ none: true });
  },
};

/** The panel open while the newest answer is being written. */
export const AnswerBeingWritten: Story = {
  name: "Panel open, answer being written",
  parameters: { at: `${AT}?chat=open` },
  beforeEach: () => {
    fakeThread({ writing: ANSWER_SO_FAR });
  },
};
