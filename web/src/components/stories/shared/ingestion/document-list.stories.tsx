import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { DocumentList } from "@/components/shared/ingestion/document-list";
import { Card, CardContent } from "@/components/ui/card";
import { camilleDocuments } from "@/design-system/fixtures/camille-ingestion";
import {
  everyDocumentState,
  LONG_NAME_DOCUMENT,
} from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments } from "@/test/ingestion";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Ingestion/Document list",
  component: DocumentList,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The person's documents, newest first, each with its type, size, state, Preview and Delete, and the check of a possible secret: Your documents in Fill my profile, the welcome wizard and the CV upload panel.",
      },
    },
  },
  args: {
    documents: camilleDocuments(),
    onPreview: () => undefined,
    onDelete: () => undefined,
  },
  beforeEach: () => {
    fakeDocuments(everyDocumentState());
  },
} satisfies Meta<typeof DocumentList>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A CV and notes, both read: each can be previewed or deleted. */
export const TwoDocumentsRead: Story = {
  name: "Two documents read",
};

/** Every state a row can show: being read, to check, waiting for the agent, not read, waiting for a reader, pasted, ready. */
export const EveryState: Story = {
  name: "Every state of a document",
  args: { documents: everyDocumentState() },
};

/** A text that looks like it holds a password: no agent reads it until the person keeps or deletes it. */
export const PossibleSecret: Story = {
  name: "A possible secret to check",
  args: {
    documents: everyDocumentState().filter((one) => one.id === "doc-secret"),
  },
};

/** "It is fine, keep it" waits for the server. */
export const KeepingAPossibleSecret: Story = {
  name: "Keeping a possible secret",
  args: PossibleSecret.args,
  beforeEach: () => {
    server.use(
      http.post("*/api/ingestion/documents/:id/confirm", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "It is fine, keep it",
      }),
    );
  },
};

/** The confirmation failed: the server's sentence shows in the box, which stays. */
export const KeepingFails: Story = {
  name: "Keeping a possible secret fails",
  args: PossibleSecret.args,
  beforeEach: () => {
    server.use(
      http.post("*/api/ingestion/documents/:id/confirm", () =>
        HttpResponse.json(
          { detail: "This document was deleted in another tab." },
          { status: 404 },
        ),
      ),
    );
  },
  play: KeepingAPossibleSecret.play,
};

/** No document yet: what to do first. */
export const NoDocumentYet: Story = {
  name: "No document yet",
  args: { documents: [] },
};

/** Inside a card already (the CV upload panel): rows between hairlines, no card of their own. */
export const InsideACard: Story = {
  name: "Inside a card",
  args: { flat: true, documents: everyDocumentState().slice(0, 4) },
  decorators: [
    (Story) => (
      <Card>
        <CardContent>
          <Story />
        </CardContent>
      </Card>
    ),
  ],
};

/** Inside a card with nothing in it: the empty state without a frame of its own. */
export const InsideACardEmpty: Story = {
  name: "Inside a card, no document yet",
  args: { flat: true, documents: [] },
  decorators: InsideACard.decorators,
};

/** A long name wraps; its state and actions stay readable. */
export const LongName: Story = {
  name: "Long document name",
  args: { documents: [LONG_NAME_DOCUMENT, ...camilleDocuments()] },
};

/** On a phone the state goes under the name and the buttons wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { documents: everyDocumentState().slice(0, 4) },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
