import type { Meta, StoryObj } from "@storybook/react-vite";

import { DocumentPreview } from "@/components/shared/ingestion/document-preview";
import { camilleDocuments } from "@/design-system/fixtures/camille-ingestion";
import {
  everyDocumentState,
  failDocuments,
  fakePreviewDocuments,
  LONG_TEXT,
} from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import type { IngestionDocument } from "@/features/ingestion/api/documents-api";
import { fakeDocument } from "@/test/ingestion";

const [CV] = camilleDocuments();

const document = (
  id: string,
  overrides: Partial<IngestionDocument>,
): IngestionDocument => fakeDocument({ id, ...overrides });

const meta = {
  title: "Shared/Ingestion/Document preview",
  component: DocumentPreview,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      // Each story in its own frame on the docs page, so an open dialog covers only its own.
      story: { inline: false, height: "640px" },
      description: {
        component:
          "The text kept from a document, as an agent will read it, page by page with the pages kept as images: from Preview on a row of Your documents.",
      },
    },
  },
  args: { document: CV, onClose: () => undefined },
  beforeEach: () => {
    fakePreviewDocuments();
  },
} satisfies Meta<typeof DocumentPreview>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The text of a CV, whole. */
export const ReadTheText: Story = {
  name: "Read the text of a CV",
};

/** A PDF of two pages: the first read as text, the second a scan kept as an image for the agent. */
export const PagesKeptAsImages: Story = {
  name: "A page kept as an image",
  args: {
    document: document("doc-two-pages", {
      name: "references.pdf",
      kind: "pdf",
      mime: "application/pdf",
      pages: 2,
      pages_text: 1,
      pages_image: 1,
      status: "awaiting_transcription",
    }),
  },
};

/** A photo with no text yet: the image is the document until the agent reads it. */
export const AnImageOnly: Story = {
  name: "An image the agent reads at the next run",
  args: {
    document: everyDocumentState().find((one) => one.id === "doc-photo"),
  },
};

/** A long text loads a part at a time, with Show more. */
export const LongText: Story = {
  name: "Long text, a part at a time",
  args: {
    document: document("doc-long-note", {
      name: "every-project.md",
      mime: "text/markdown",
      size: LONG_TEXT.length,
    }),
  },
  beforeEach: () => {
    fakePreviewDocuments().page = 1_500;
  },
};

/** A document with no text in it says so. */
export const NoText: Story = {
  name: "No text in the document",
  args: {
    document: document("doc-empty", {
      name: "blank-page.pdf",
      kind: "pdf",
      mime: "application/pdf",
      pages: 1,
      pages_text: 0,
    }),
  },
};

/** While the text loads. */
export const Loading: Story = {
  name: "Loading the text",
  beforeEach: () => {
    failDocuments("loading");
  },
};

/** The text could not be loaded: what happened, and Retry. */
export const CouldNotLoad: Story = {
  name: "The text could not be loaded",
  beforeEach: () => {
    failDocuments("error");
  },
};
