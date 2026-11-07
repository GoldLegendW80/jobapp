import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";

import { ChatPanel } from "@/components/shared/documents/chat-panel";
import { Card, CardContent } from "@/components/ui/card";
import {
  CHAT_MESSAGES,
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

const CV = postingDocument("cv", POSTING_DETAIL.id, {
  latest: fakeVersion({ number: 2 }),
});
const LONG = Array.from({ length: 4 }, (_, round) =>
  CHAT_MESSAGES.map((one) => ({ ...one, id: one.id + round * 10 })),
).flat();

const meta = {
  title: "Shared/Documents/Chat panel",
  component: ChatPanel,
  tags: ["autodocs"],
  decorators: [
    withApp,
    (Story) => (
      <Card className="max-w-sm">
        <CardContent>
          <Story />
        </CardContent>
      </Card>
    ),
  ],
  parameters: {
    docs: {
      description: {
        component:
          "The chat that changes a document, built from the chat of a posting (its thread, turns, empty state and prompt): the conversation, the examples of a first change, the box with Send inside it, and the consent and credential it waits for. In the document view's side rail, or a drawer on a phone.",
      },
    },
  },
  args: { document: CV, generation: null },
  beforeEach: () => {
    fakeOneDocument(CV, { consent: { accepted: true } });
  },
} satisfies Meta<typeof ChatPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Before the first change: what the assistant does, and three examples. */
export const FirstChange: Story = { name: "Before the first change" };

/** A change asked and answered, with the version it made. */
export const Answered: Story = {
  name: "A change answered",
  beforeEach: () => {
    fakeOneDocument(CV, {
      consent: { accepted: true },
      messages: CHAT_MESSAGES,
    });
  },
};

/** A long conversation scrolls inside the thread, opened on the newest message. */
export const LongConversation: Story = {
  name: "A long conversation",
  beforeEach: () => {
    fakeOneDocument(CV, { consent: { accepted: true }, messages: LONG });
  },
};

/** The answer is being written: Stop, and Send waits. */
export const BeingAnswered: Story = {
  name: "An answer being written",
  beforeEach: () => {
    fakeOneDocument(CV, {
      consent: { accepted: true },
      messages: CHAT_MESSAGES.slice(0, 1),
    });
    server.use(
      http.get("*/api/document-generations/:id", () =>
        HttpResponse.json({
          id: "g-chat-1",
          status: "running",
          wait_reason: null,
          error: null,
          document_id: CV.id,
          mode: "chat",
          trigger: "chat",
          outcome: null,
          version_number: null,
          corrections: 0,
          created_at: "2026-11-05T09:20:00Z",
          started_at: "2026-11-05T09:20:01Z",
          finished_at: null,
        }),
      ),
    );
  },
};

/** No consent yet: the box waits, and Read and confirm opens what is sent. */
export const NoConsent: Story = {
  name: "Waiting for the consent",
  beforeEach: () => {
    fakeOneDocument(CV);
  },
};

/** No model credential saved: the way to the Account page. */
export const NoCredential: Story = {
  name: "Without a model credential",
  beforeEach: () => {
    fakeOneDocument(CV, { consent: { accepted: true, model: null } });
  },
};

/** The conversation could not be read. */
export const CouldNotLoad: Story = {
  name: "Could not be loaded",
  beforeEach: () => {
    fakeOneDocument(CV, { consent: { accepted: true } });
    server.use(
      http.get("*/api/documents/:id/messages", () =>
        HttpResponse.json({ detail: "The service is busy." }, { status: 503 }),
      ),
    );
  },
};
