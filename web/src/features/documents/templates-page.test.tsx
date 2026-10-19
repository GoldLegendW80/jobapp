import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import {
  fakeCv,
  fakeDocumentOf,
  fakeDocuments,
  fakeVersion,
} from "@/test/documents";
import { renderApp } from "@/test/render";
import { fakeGallery } from "@/test/templates";

import type { DocumentOut } from "./documents-api";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const BASE = fakeDocumentOf(fakeCv({ id: "b", name: "Main CV" }), {
  latest: fakeVersion(),
});
const POSTING_CV: DocumentOut = {
  ...BASE,
  id: "p",
  scope: "posting",
  posting_id: "job-1",
  base_document_id: "b",
  name: null,
};

function setup(...documents: DocumentOut[]) {
  fakeSession(member);
  const api = fakeDocuments();
  for (const one of documents) api.documents.set(one.id, one);
  const gallery = fakeGallery(api);
  return { api, gallery };
}

async function openGallery(path = "/documents/templates") {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Templates" });
  await screen.findByRole("heading", { level: 2, name: "All templates" });
  return { user, ...app };
}

const cardNames = (section: HTMLElement) =>
  within(section)
    .queryAllByRole("heading", { level: 3 })
    .map((one) => one.textContent);

const section = (name: string) => screen.getByRole("region", { name });

const writes = (api: ReturnType<typeof fakeDocuments>) =>
  api.requests.filter((one) => one.method !== "GET");

describe("the gallery", () => {
  it("lists every template served, the person's own first under From your CV, with names, descriptions and tags", async () => {
    setup();
    await openGallery();
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((one) => one.textContent);
    expect(headings).toEqual(["From your CV", "All templates"]);
    expect(cardNames(section("From your CV"))).toEqual(["My uploaded look"]);
    expect(cardNames(section("All templates"))).toEqual([
      "Banner",
      "Classic",
      "Compact",
      "Sidebar",
      "Serif",
    ]);
    const classic = screen.getByTestId("template-classic");
    expect(classic).toHaveTextContent("The classic look, in English.");
    expect(
      within(classic)
        .getAllByRole("listitem")
        .map((one) => one.textContent),
    ).toEqual(["one column", "sans", "ats friendly", "photo"]);
    expect(
      within(classic).getByRole("img", { name: "Classic, the CV" }),
    ).toHaveAttribute(
      "src",
      "/api/document-templates/classic/thumbnail?kind=cv&v=1",
    );
    expect(screen.getByText("6 templates.")).toBeVisible();
    // Opened from nowhere, nothing can be used.
    expect(
      screen.queryByRole("button", { name: "Use for this document" }),
    ).toBeNull();
    expect(document.title).toBe("Templates · jobapp");
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("filters by photo, columns and ats friendly, says how many match, and clears", async () => {
    setup();
    const { user } = await openGallery();
    await user.selectOptions(screen.getByLabelText("Photo"), "No photo");
    expect(cardNames(section("All templates"))).toEqual(["Compact", "Serif"]);
    // The person's own declares no photo slot in its manifest.
    expect(cardNames(section("From your CV"))).toEqual(["My uploaded look"]);
    expect(screen.getByText("3 of 6 templates match.")).toBeVisible();

    await user.selectOptions(screen.getByLabelText("Photo"), "Any");
    await user.selectOptions(screen.getByLabelText("Columns"), "Two columns");
    expect(cardNames(section("All templates"))).toEqual(["Sidebar"]);
    // An own template has no tags: a columns filter does not guess it.
    expect(screen.queryByRole("region", { name: "From your CV" })).toBeNull();

    await user.click(screen.getByLabelText("ATS friendly only"));
    expect(
      screen.getByText("No template matches these filters."),
    ).toBeVisible();
    expect(screen.getByText("0 of 6 templates match.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear the filters" }));
    expect(screen.getByText("6 templates.")).toBeVisible();
    expect(screen.getByLabelText("Columns")).toHaveValue("any");
    expect(screen.getByLabelText("ATS friendly only")).not.toBeChecked();
  });

  it("shows the letter on hover, and on a tap of See the letter", async () => {
    setup();
    const { user } = await openGallery();
    const banner = screen.getByTestId("template-banner");
    const image = within(banner).getByRole("img", { name: "Banner, the CV" });
    fireEvent.mouseEnter(image.parentElement!);
    expect(
      within(banner).getByRole("img", { name: "Banner, the letter" }),
    ).toHaveAttribute(
      "src",
      "/api/document-templates/banner/thumbnail?kind=letter&v=1",
    );
    fireEvent.mouseLeave(image.parentElement!);
    expect(
      within(banner).getByRole("img", { name: "Banner, the CV" }),
    ).toBeVisible();

    const toggle = within(banner).getByRole("button", {
      name: "See the letter",
    });
    await user.click(toggle);
    expect(
      within(banner).getByRole("img", { name: "Banner, the letter" }),
    ).toBeVisible();
    expect(
      within(banner).getByRole("button", { name: "See the CV" }),
    ).toHaveAttribute("aria-pressed", "true");
    await user.click(
      within(banner).getByRole("button", { name: "See the CV" }),
    );
    expect(
      within(banner).getByRole("img", { name: "Banner, the CV" }),
    ).toBeVisible();
    // The person's own has no letter image: no toggle.
    expect(
      within(screen.getByTestId("template-upload-abcd1234")).queryByRole(
        "button",
        { name: "See the letter" },
      ),
    ).toBeNull();
  });

  it("opened from a posting's CV, uses a template for that document only and goes back to it", async () => {
    const { api } = setup(POSTING_CV);
    const { user } = await openGallery("/documents/templates?document=p");
    expect(
      await screen.findByText(
        "Choose the look of this CV. The words stay the same.",
      ),
    ).toBeVisible();
    const classic = screen.getByTestId("template-classic");
    expect(within(classic).getByText("In use")).toBeVisible();
    expect(
      within(classic).queryByRole("button", { name: "Use for this document" }),
    ).toBeNull();
    await user.click(
      within(screen.getByTestId("template-compact")).getByRole("button", {
        name: "Use for this document",
      }),
    );
    expect(
      await screen.findByText("Compact is now used for this document."),
    ).toBeVisible();
    await waitFor(() => {
      expect(
        within(screen.getByTestId("template-compact")).getByText("In use"),
      ).toBeVisible();
    });
    expect(writes(api)).toEqual([
      expect.objectContaining({
        method: "PATCH",
        path: "/api/documents/p",
        body: { template_slug: "compact" },
      }),
    ]);
    expect(
      screen.getByRole("link", { name: "Back to the document" }),
    ).toHaveAttribute("href", "/jobs/job-1/documents/cv");
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("opened from a base CV, sets that base CV's template and says the later CVs take it", async () => {
    const { api } = setup(BASE);
    const { user } = await openGallery("/documents/templates?document=b");
    expect(
      await screen.findByText(
        'Choose the look of the base CV "Main CV". The words stay the same.',
      ),
    ).toBeVisible();
    await user.click(
      within(screen.getByTestId("template-sidebar")).getByRole("button", {
        name: "Use for this document",
      }),
    );
    expect(
      await screen.findByText(
        "Sidebar is now used for this document. The CVs you write from it afterwards take it; the CVs already written keep theirs.",
      ),
    ).toBeVisible();
    expect(writes(api)[0]).toMatchObject({
      method: "PATCH",
      path: "/api/documents/b",
      body: { template_slug: "sidebar" },
    });
    expect(
      screen.getByRole("link", { name: "Back to the document" }),
    ).toHaveAttribute("href", "/profile/cv/b");
  });

  it("names templates in the document's language", async () => {
    setup({ ...POSTING_CV, language: "fr" });
    await openGallery("/documents/templates?document=p");
    await screen.findByText(
      "Choose the look of this CV. The words stay the same.",
    );
    await waitFor(() => {
      expect(cardNames(section("All templates"))).toContain("Classic FR");
    });
    expect(screen.getByTestId("template-classic")).toHaveTextContent(
      "Le style classic, en français.",
    );
  });

  it("says when the document it was opened from cannot be read, and offers no switch", async () => {
    setup();
    await openGallery("/documents/templates?document=gone");
    expect(
      await screen.findByText(
        /The document this gallery was opened from could not be loaded/,
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Use for this document" }),
    ).toBeNull();
  });

  it("lays the cards out two by two on a phone", async () => {
    setup();
    await openGallery();
    for (const grid of screen.getAllByTestId("template-grid"))
      expect(grid.className).toMatch(/(^| )grid-cols-2( |$)/);
  });

  it("works with the keyboard only: the filters, the letter and the use", async () => {
    const { api } = setup(POSTING_CV);
    const { user } = await openGallery("/documents/templates?document=p");
    await screen.findByText(
      "Choose the look of this CV. The words stay the same.",
    );
    screen.getByLabelText("Photo").focus();
    await user.keyboard("{ArrowDown}");
    await user.tab();
    expect(screen.getByLabelText("Columns")).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("ATS friendly only")).toHaveFocus();
    await user.keyboard(" ");
    expect(screen.getByLabelText("ATS friendly only")).toBeChecked();
    const compact = screen.getByTestId("template-compact");
    within(compact).getByRole("button", { name: "See the letter" }).focus();
    await user.keyboard("{Enter}");
    expect(
      within(compact).getByRole("img", { name: "Compact, the letter" }),
    ).toBeVisible();
    await user.tab();
    expect(
      within(compact).getByRole("button", { name: "Use for this document" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(writes(api)).toHaveLength(1);
    });
  });
});
