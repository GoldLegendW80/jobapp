import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PdfTextNote } from "@/components/shared/documents/document-view/pdf-text-note";
import { fakeVersion } from "@/test/documents";

/** The api's sentences (corrections.py, PDF_EMAIL and PDF_GLYPH_NAME). */
const EMAIL = {
  code: "pdf_email_not_text",
  message:
    "Your e-mail is not text in this PDF, so an application system cannot read it. Choose another template, or check the e-mail of your profile.",
};
const GLYPH = {
  code: "pdf_glyph_name",
  message:
    "Some letters of this PDF read as glyph codes, not letters. Choose another template.",
};
const SEPARATOR = /[–—]| - /;

describe("PdfTextNote", () => {
  it("says each finding with what to do, and that the PDF still goes", () => {
    render(<PdfTextNote version={fakeVersion({ pdf_text: [EMAIL, GLYPH] })} />);
    const note = screen.getByTestId("pdf-text-note");
    expect(note).toHaveRole("alert");
    expect(
      within(note).getByText(
        "An application system may not read all of this PDF",
      ),
    ).toBeVisible();
    expect(
      within(note)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([EMAIL.message, GLYPH.message]);
    expect(note).toHaveTextContent(
      "It can still be downloaded and sent as it is.",
    );
    expect(note.textContent).not.toMatch(SEPARATOR);
  });

  it.each([
    ["the check passed", []],
    ["the check never ran", null],
    ["an api older than the check", undefined],
  ])("shows nothing when %s", (_, pdf_text) => {
    const { container } = render(
      <PdfTextNote version={fakeVersion({ pdf_text })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
