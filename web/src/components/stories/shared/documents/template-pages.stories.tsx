import type { Meta, StoryObj } from "@storybook/react-vite";

import { DocumentSheet } from "@/components/shared/documents/document-sheet";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import {
  SAMPLE_CV,
  SAMPLE_DAY,
  SAMPLE_FACTS,
  SAMPLE_LETTER,
} from "@/design-system/fixtures/template-sample";

const meta = {
  title: "Shared/Documents/Template pages",
  component: DocumentSheet,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Each template's CV and letter as the document page draws it, with the invented person of the templates' thumbnails: the same columns, band, photo place, accent, typeface and sizes as the PDF, each text edited where it is read. The document view shows the page of the document's template.",
      },
    },
  },
  args: {
    kind: "cv",
    language: "en",
    template: "band",
    content: SAMPLE_CV,
    facts: SAMPLE_FACTS,
    form: null,
    known: true,
    date: SAMPLE_DAY,
  },
  render: (args) => (
    <DocumentForm
      initial={args.kind === "cv" ? SAMPLE_CV : SAMPLE_LETTER}
      kind={args.kind}
    >
      {(form) => <DocumentSheet {...args} content={form.draft} form={form} />}
    </DocumentForm>
  ),
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

/** Band: the page of its thumbnail (api/app/documents/templates/band), edited in place. */
export const BandCv: Story = {
  name: "Band CV",
  args: { template: "band", kind: "cv" },
};

/** Band: the page of its letter thumbnail (api/app/documents/templates/band), edited in place. */
export const BandLetter: Story = {
  name: "Band letter",
  args: { template: "band", kind: "letter" },
};

/** Ledger: the page of its thumbnail (api/app/documents/templates/ledger), edited in place. */
export const LedgerCv: Story = {
  name: "Ledger CV",
  args: { template: "ledger", kind: "cv" },
};

/** Ledger: the page of its letter thumbnail (api/app/documents/templates/ledger), edited in place. */
export const LedgerLetter: Story = {
  name: "Ledger letter",
  args: { template: "ledger", kind: "letter" },
};

/** Photo columns: the page of its thumbnail (api/app/documents/templates/photo-columns), edited in place. */
export const PhotoColumnsCv: Story = {
  name: "Photo columns CV",
  args: { template: "photo-columns", kind: "cv" },
};

/** Photo columns: the page of its letter thumbnail (api/app/documents/templates/photo-columns), edited in place. */
export const PhotoColumnsLetter: Story = {
  name: "Photo columns letter",
  args: { template: "photo-columns", kind: "letter" },
};
