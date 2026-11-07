import type { Meta, StoryObj } from "@storybook/react-vite";

import { DocumentSheet } from "@/components/shared/documents/document-sheet";
import {
  CAMILLE_FACTS,
  DocumentForm,
} from "@/design-system/fixtures/document-form";
import { CV_DRAFT, LETTER_DRAFT } from "@/design-system/fixtures/documents";

const meta = {
  title: "Shared/Documents/Document sheet",
  component: DocumentSheet,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The CV or the letter drawn as its template prints it (columns, band, photo, accent, typeface), from its text and the profile: read as it is, or edited where it is read when a form is given (each text a button that puts its field in its place). The document view shows it; the PDF stays the page that is sent. Every template: Shared, Documents, Template pages.",
      },
    },
  },
  args: {
    kind: "cv",
    language: "en",
    template: "band",
    content: CV_DRAFT,
    facts: CAMILLE_FACTS,
    form: null,
    known: true,
  },
  decorators: [
    (Story) => (
      <div className="max-w-3xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DocumentSheet>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A CV read as it was written (an earlier version). */
export const ReadACv: Story = { name: "Read a CV" };

/** The same CV edited where it is read: hover a text, click it, Escape puts it back. */
export const EditACv: Story = {
  name: "Edit a CV in place",
  render: (args) => (
    <DocumentForm initial={CV_DRAFT}>
      {(form) => <DocumentSheet {...args} form={form} />}
    </DocumentForm>
  ),
};

/** A new draft: every empty text says what to click to write it. */
export const EmptyDraft: Story = {
  name: "An empty draft",
  render: (args) => (
    <DocumentForm initial={{}}>
      {(form) => <DocumentSheet {...args} content={{}} form={form} />}
    </DocumentForm>
  ),
};

/** A letter, edited in place: recipient, subject, paragraphs, closing. */
export const EditALetter: Story = {
  name: "Edit a letter in place",
  args: { kind: "letter", content: LETTER_DRAFT },
  render: (args) => (
    <DocumentForm initial={LETTER_DRAFT} kind="letter">
      {(form) => <DocumentSheet {...args} form={form} />}
    </DocumentForm>
  ),
};

/** In French with Ledger: the template's own headings and the date print in French. */
export const FrenchLedger: Story = {
  name: "In French, with Ledger",
  args: { language: "fr", template: "ledger" },
};

/** A person's own template is drawn with Band; the printed page shows their design. */
export const OwnTemplate: Story = {
  name: "A template of your own",
  args: { template: "mine-3f2a" },
};

/** A note of the fact check rings its part and is listed above the page. */
export const WithANote: Story = {
  name: "With a note on a part",
  render: (args) => (
    <DocumentForm
      initial={CV_DRAFT}
      notes={
        new Map([
          [
            "summary",
            [
              {
                sentence: "A number your profile does not state",
                excerpt: "seven",
              },
            ],
          ],
        ])
      }
    >
      {(form) => <DocumentSheet {...args} form={form} />}
    </DocumentForm>
  ),
};

/** A long text wraps inside the page and never widens it. */
export const LongContent: Story = {
  name: "Long content",
  args: {
    content: {
      ...CV_DRAFT,
      summary: `${String(CV_DRAFT.summary)} ${String(CV_DRAFT.summary)} ${String(CV_DRAFT.summary)}`,
      headline:
        "Senior data engineer for streaming, batch and analytics platforms across several countries",
    },
  },
};

/** At a phone's width the page keeps its layout, its text no smaller than the app's small type. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
