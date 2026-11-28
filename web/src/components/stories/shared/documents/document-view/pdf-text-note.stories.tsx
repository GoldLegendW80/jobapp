import type { Meta, StoryObj } from "@storybook/react-vite";

import { PdfTextNote } from "@/components/shared/documents/document-view/pdf-text-note";
import { fakeVersion } from "@/test/documents";

/** The api's sentences for each finding (corrections.py, PDF_*). */
const FINDINGS = {
  noText: {
    code: "pdf_no_text",
    message:
      "This PDF holds no text an application system can read. Render it again, and if it stays so, choose another template.",
  },
  email: {
    code: "pdf_email_not_text",
    message:
      "Your e-mail is not text in this PDF, so an application system cannot read it. Choose another template, or check the e-mail of your profile.",
  },
  phone: {
    code: "pdf_phone_not_text",
    message:
      "Your phone number is not text in this PDF, so an application system cannot read it. Choose another template, or check the phone number of your profile.",
  },
  glyph: {
    code: "pdf_glyph_name",
    message:
      "Some letters of this PDF read as glyph codes, not letters. Choose another template.",
  },
};

const meta = {
  title: "Shared/Documents/PDF text note",
  component: PdfTextNote,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Beside the fit note, in the document view: what the check of the PDF's text layer found (no text, the e-mail or the phone drawn and not written, a blank box, a glyph code), each with what to do. The PDF can still be downloaded and sent; nothing shows when the check passed or never ran.",
      },
    },
  },
  args: {
    version: fakeVersion({ number: 2, pdf_text: [FINDINGS.email] }),
  },
} satisfies Meta<typeof PdfTextNote>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The e-mail is drawn, not written: an application system misses it. */
export const EmailNotText: Story = { name: "The e-mail is not text" };

/** Several findings at once, each with its own sentence. */
export const SeveralFindings: Story = {
  name: "Several findings",
  args: {
    version: fakeVersion({
      number: 2,
      pdf_text: [FINDINGS.phone, FINDINGS.glyph],
    }),
  },
};

/** A PDF with no text at all: the page reads as a picture. */
export const NoText: Story = {
  name: "No text at all",
  args: { version: fakeVersion({ number: 2, pdf_text: [FINDINGS.noText] }) },
};

/** The check passed: nothing is shown. */
export const Passed: Story = {
  name: "The check passed",
  args: { version: fakeVersion({ number: 2, pdf_text: [] }) },
};
