import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  fakePostingDocuments,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { fakeSession, member } from "@/test/auth";
import { fakeVersion } from "@/test/documents";
import { renderApp } from "@/test/render";

const JOB = POSTING_DETAIL;
const NAME = `CV for ${JOB.title} at ${JOB.company}`;

/** Whether `b` comes after `a` in the page. */
const after = (a: Node, b: Node) =>
  (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

describe("the page of a posting's document", () => {
  it("names the document and its posting, holds the PDF in its header, and its facts and versions beside the page", async () => {
    fakeSession(member);
    fakePostingDocuments(
      JOB,
      [
        postingDocument("cv", JOB.id, {
          latest: fakeVersion({ number: 2 }),
          generation: { status: "done", wait_reason: null },
        }),
      ],
      [fakeVersion({ number: 1 })],
    );
    const user = userEvent.setup();
    renderApp(`/jobs/${JOB.id}/documents/cv`);

    const title = await screen.findByRole("heading", { level: 1, name: NAME });
    expect(document.title).toBe(`${NAME} · jobapp`);
    // The PDF of the version shown is the header's only, before the views.
    const download = await screen.findByRole("link", { name: "Download PDF" });
    expect(download).toHaveAttribute(
      "href",
      "/api/documents/cv-doc/versions/2/pdf",
    );
    expect(screen.getAllByRole("link", { name: "Download PDF" })).toHaveLength(
      1,
    );
    expect(after(title, download)).toBe(true);
    // The page of the document comes next, its text edited where it is read.
    const sheet = await screen.findByTestId("document-sheet");
    expect(after(download, sheet)).toBe(true);
    expect(
      within(sheet).getByRole("button", { name: "Edit the summary" }),
    ).toBeVisible();

    const details = screen.getByRole("region", { name: "Details" });
    expect(details).toHaveTextContent("TemplateClassic");
    expect(details).toHaveTextContent("LanguageEnglish");
    expect(within(details).getByTestId("fit")).toHaveTextContent("One page");
    const versions = await screen.findByRole("region", { name: "Versions" });
    expect(versions).toHaveTextContent(
      "An application sends the approved version, else the latest.",
    );

    // A version opened from the history is shown read only, with its own PDF in the header.
    await user.click(
      within(versions).getByRole("button", { name: "Open version 1" }),
    );
    expect(
      await screen.findByRole("img", { name: "Page 1 of version 1" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("heading", { level: 2, name: "Version 1" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Edit the summary" }),
    ).toBeNull();
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute(
      "href",
      "/api/documents/cv-doc/versions/1/pdf",
    );
  });
});
