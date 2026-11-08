import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { KeepRule } from "@/components/shared/documents/chat-panel/keep-rule";
import { CHAT_MESSAGES } from "@/design-system/fixtures/documents";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

import type { Message } from "@/features/documents/api/chat-api";

const CORRECTED: Message[] = [
  {
    id: 1,
    channel: "content",
    role: "user",
    text: "Never say leveraged",
    generation_id: "g-chat-1",
    version_number: null,
    at: "2026-11-05T09:20:00Z",
    rule: "Never say leveraged",
  },
  {
    id: 2,
    channel: "content",
    role: "assistant",
    text: "Removed leveraged from two bullets.\nThis is version 2.",
    generation_id: "g-chat-1",
    version_number: 2,
    at: "2026-11-05T09:21:00Z",
  },
];

const meta = {
  title: "Shared/Documents/Keep as a rule",
  component: KeepRule,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Under a document's chat: the person's newest message, once answered, when it corrects the wording, offered as a writing rule. Nothing is written before Keep; the person may change the rule first. A fact is never a rule: the api refuses one and says where facts go.",
      },
    },
  },
  args: { documentId: "doc-1", messages: CORRECTED, answering: false },
  beforeEach: () => {
    server.use(
      http.post(
        "*/api/documents/:id/messages/:message/rule",
        async ({ request }) => {
          const body = (await request.json()) as { text: string };
          return HttpResponse.json({ rule: body.text, kept: true });
        },
      ),
    );
  },
} satisfies Meta<typeof KeepRule>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A correction answered: Keep as a rule, or Not now. */
export const Offered: Story = { name: "A correction offered as a rule" };

/** A request that is no rule (a template, a fact): nothing is offered. */
export const NoOffer: Story = {
  name: "No offer",
  args: { messages: CHAT_MESSAGES },
};

/** While the answer is written, the offer waits. */
export const Answering: Story = {
  name: "While the answer is written",
  args: { answering: true },
};
