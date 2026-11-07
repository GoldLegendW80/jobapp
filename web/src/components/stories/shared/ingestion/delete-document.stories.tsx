import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { DeleteDocument } from "@/components/shared/ingestion/delete-document";
import { camilleDocuments } from "@/design-system/fixtures/camille-ingestion";
import { LONG_NAME_DOCUMENT } from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments } from "@/test/ingestion";
import { server } from "@/test/server";

const [CV] = camilleDocuments();

const meta = {
  title: "Shared/Ingestion/Delete document",
  component: DeleteDocument,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      // Each story in its own frame on the docs page, so an open dialog covers only its own.
      story: { inline: false, height: "520px" },
      description: {
        component:
          "The confirmation before a document is deleted for good, saying what goes, what stays in the profile, the backups and the provider: from Delete on a row of Your documents.",
      },
    },
  },
  args: {
    document: CV,
    onClose: () => undefined,
    onDeleted: () => undefined,
  },
  beforeEach: () => {
    fakeDocuments(camilleDocuments());
  },
} satisfies Meta<typeof DeleteDocument>;

export default meta;
type Story = StoryObj<typeof meta>;

const deleteForGood = async ({
  canvasElement,
}: {
  canvasElement: HTMLElement;
}) => {
  const page = within(canvasElement.ownerDocument.body);
  await userEvent.click(
    await page.findByRole("button", { name: "Delete for good" }),
  );
};

/** The person reads what deleting means, then keeps the document or deletes it. */
export const AskedBeforeDeleting: Story = {
  name: "Asked before deleting",
};

/** A long name wraps inside the dialog. */
export const LongDocumentName: Story = {
  name: "Long document name",
  args: { document: LONG_NAME_DOCUMENT },
};

/** After Delete for good, both buttons wait for the server's answer. */
export const Deleting: Story = {
  name: "Deleting",
  beforeEach: () => {
    server.use(
      http.delete("*/api/ingestion/documents/:id", async () => {
        await delay("infinite");
        return new HttpResponse(null, { status: 204 });
      }),
    );
  },
  play: deleteForGood,
};

/** A run reads the document right now: the server's sentence shows and the document stays. */
export const TheServerRefuses: Story = {
  name: "The server refuses, the document stays",
  beforeEach: () => {
    server.use(
      http.delete("*/api/ingestion/documents/:id", () =>
        HttpResponse.json(
          {
            detail:
              "A run is reading this document right now. Delete it once the run has ended.",
          },
          { status: 409 },
        ),
      ),
    );
  },
  play: deleteForGood,
};
