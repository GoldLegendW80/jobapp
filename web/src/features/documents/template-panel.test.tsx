import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "@/lib/query";
import {
  fakeCv,
  fakeDocumentOf,
  fakeDocuments,
  fakeVersion,
} from "@/test/documents";
import { fakeGallery, fakeTemplate, TEMPLATES } from "@/test/templates";

import { DocumentView } from "./document-view";
import type { DocumentOut } from "./documents-api";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const BASE = fakeDocumentOf(fakeCv({ id: "b", name: "Main CV" }), {
  latest: fakeVersion({ number: 1 }),
});
const POSTING_CV: DocumentOut = {
  ...BASE,
  id: "p",
  scope: "posting",
  posting_id: "job-1",
  base_document_id: "b",
  name: null,
};
const LETTER: DocumentOut = { ...POSTING_CV, id: "l", kind: "letter" };

function setup(
  document: DocumentOut,
  options: Parameters<typeof fakeGallery>[1] = {},
) {
  const api = fakeDocuments();
  api.documents.set(document.id, document);
  api.versions.set(document.id, document.latest ? [document.latest] : []);
  const gallery = fakeGallery(api, options);
  return { api, gallery };
}

/** The document view on its own page, with the gallery behind "See all". */
async function openTemplates(documentId: string) {
  const user = userEvent.setup();
  const client = createQueryClient();
  const router = createMemoryRouter(
    [
      { path: "/doc", element: <DocumentView documentId={documentId} /> },
      { path: "/documents/templates", element: <p>The gallery</p> },
    ],
    { initialEntries: ["/doc"] },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await user.click(await screen.findByRole("tab", { name: "Template" }));
  const strip = await screen.findByRole("list", { name: "Templates" });
  return { user, router, strip, client };
}

const writes = (api: ReturnType<typeof fakeDocuments>) =>
  api.requests.filter((one) => one.method !== "GET");

describe("the Template view", () => {
  it("shows the person's own first, every template's thumbnail, and the current one marked", async () => {
    setup(POSTING_CV);
    const { strip } = await openTemplates("p");
    const buttons = within(strip).getAllByRole("button");
    expect(buttons.map((one) => one.textContent)).toEqual([
      "My uploaded lookFrom your CV",
      "Banner",
      "ClassicIn use",
      "Compact",
      "Sidebar",
      "Serif",
    ]);
    const classic = within(strip).getByRole("button", { name: /^Classic/ });
    expect(classic).toHaveAttribute("aria-pressed", "true");
    expect(
      within(strip).getByRole("button", { name: /^Compact/ }),
    ).toHaveAttribute("aria-pressed", "false");
    // The CV's page 1 at the address the api gave, with the template version in it.
    const image = classic.querySelector("img");
    expect(image).toHaveAttribute(
      "src",
      "/api/document-templates/classic/thumbnail?kind=cv&v=1",
    );
    // The person's own has no image: a plain frame.
    expect(
      within(strip)
        .getByRole("button", { name: /^My uploaded look/ })
        .querySelector("img"),
    ).toBeNull();
    expect(
      screen.getByText(
        "Only this CV changes. The base CV it was written from keeps its template.",
      ),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("switches with a click: the same words, a new version, the new one marked, the base CV untouched", async () => {
    const { api } = setup(POSTING_CV);
    const { user, strip } = await openTemplates("p");
    await user.click(within(strip).getByRole("button", { name: /^Compact/ }));
    await waitFor(() => {
      expect(
        within(strip).getByRole("button", { name: /^Compact/ }),
      ).toHaveAttribute("aria-pressed", "true");
    });
    expect(writes(api)).toEqual([
      expect.objectContaining({
        method: "PATCH",
        path: "/api/documents/p",
        body: { template_slug: "compact" },
      }),
    ]);
    // Only the posting's CV was asked to change: nothing was sent about its base CV.
    expect(writes(api).some((one) => one.path.includes("/b"))).toBe(false);
  });

  it("names the letter of the thumbnails for a letter", async () => {
    setup(LETTER);
    const { strip } = await openTemplates("l");
    expect(
      within(strip)
        .getByRole("button", { name: /^Banner/ })
        .querySelector("img"),
    ).toHaveAttribute(
      "src",
      "/api/document-templates/banner/thumbnail?kind=letter&v=1",
    );
    expect(
      screen.getByText(
        "Only this letter changes. The CV of the posting keeps its template.",
      ),
    ).toBeVisible();
  });

  it("says what a base CV's switch changes: the CVs written from it afterwards", async () => {
    const { api } = setup(BASE);
    const { user, strip } = await openTemplates("b");
    expect(
      screen.getByText(
        "The CVs you write from this one afterwards take its template. The CVs already written keep theirs.",
      ),
    ).toBeVisible();
    await user.click(within(strip).getByRole("button", { name: /^Sidebar/ }));
    await waitFor(() => {
      expect(writes(api)).toHaveLength(1);
    });
    expect(writes(api)[0]).toMatchObject({
      method: "PATCH",
      path: "/api/documents/b",
      body: { template_slug: "sidebar" },
    });
  });

  it("offers Make it fit when the switch makes the CV overflow, which asks for a shorter version", async () => {
    const { api } = setup(POSTING_CV, {
      after: (one) => ({
        ...one,
        latest: fakeVersion({
          number: 2,
          origin: "template",
          fit: "overflow",
          template_slug: one.template_slug,
        }),
      }),
    });
    const { user, strip } = await openTemplates("p");
    expect(screen.queryByTestId("overflow-note")).toBeNull();
    await user.click(within(strip).getByRole("button", { name: /^Banner/ }));
    const note = await screen.findByTestId("overflow-note", undefined, {
      timeout: 4000,
    });
    expect(note).toHaveTextContent("Longer than one page");
    expect(note).toHaveTextContent(
      "With this template the CV does not fit one page.",
    );
    await user.click(within(note).getByRole("button", { name: "Make it fit" }));
    await waitFor(() => {
      expect(writes(api).map((one) => `${one.method} ${one.path}`)).toEqual([
        "PATCH /api/documents/p",
        "POST /api/documents/p/fit",
      ]);
    });
    expect(
      await screen.findByText(
        "A shorter version is being written. It shows in See when it is ready.",
      ),
    ).toBeVisible();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("shows the api's sentence when Make it fit cannot start", async () => {
    const over = { ...POSTING_CV, latest: fakeVersion({ fit: "overflow" }) };
    const { gallery } = setup(over);
    gallery.fitStatus = 404;
    const { user } = await openTemplates("p");
    await user.click(
      within(screen.getByTestId("overflow-note")).getByRole("button", {
        name: "Make it fit",
      }),
    );
    expect(await screen.findByText("Not Found")).toBeVisible();
  });

  it.each([
    ["a letter", { ...LETTER, latest: fakeVersion({ fit: "overflow" }) }],
    ["a base CV", { ...BASE, latest: fakeVersion({ fit: "overflow" }) }],
  ])(
    "says how to fit %s, which has no shortening pass, without Make it fit",
    async (_, doc) => {
      setup(doc);
      await openTemplates(doc.id);
      const note = screen.getByTestId("overflow-note");
      expect(note).toHaveTextContent(
        "Shorten the longest fields in Edit, or choose a more compact template.",
      );
      expect(within(note).queryByRole("button")).toBeNull();
    },
  );

  it("keeps a sent version as it was and says so", async () => {
    const sent = fakeVersion({
      number: 1,
      frozen_at: "2026-10-14T09:30:00Z",
      frozen_reason: "marked_sent",
    });
    const doc = { ...POSTING_CV, latest: sent };
    const { api } = setup(doc);
    api.versions.set("p", [sent]);
    const { user, strip } = await openTemplates("p");
    const note = await screen.findByTestId("sent-note");
    expect(note.textContent).toMatch(
      /^Version 1 was sent on .+\. It stays as it was: a new template makes a new version\.$/,
    );
    await user.click(within(strip).getByRole("button", { name: /^Serif/ }));
    await waitFor(() => {
      expect(writes(api)).toHaveLength(1);
    });
    expect(note.textContent).not.toMatch(SEPARATOR);
  });

  it("says when the document's template is no longer served, and that Classic prints it", async () => {
    setup({ ...POSTING_CV, template_slug: "upload-gone0000" });
    await openTemplates("p");
    const note = screen.getByTestId("removed-note");
    expect(note).toHaveTextContent(
      "The template this document used is no longer available, so it is printed with Classic. Choose another one to change it.",
    );
    expect(
      within(screen.getByRole("list", { name: "Templates" })).queryByRole(
        "button",
        { pressed: true },
      ),
    ).toBeNull();
  });

  it("shows the api's sentence for a refused switch", async () => {
    const { gallery } = setup(POSTING_CV);
    gallery.patchStatus = 422;
    const { user, strip } = await openTemplates("p");
    await user.click(within(strip).getByRole("button", { name: /^Compact/ }));
    expect(
      await screen.findByText("This template does not exist."),
    ).toBeVisible();
  });

  it("opens the gallery for this document with See all", async () => {
    setup(POSTING_CV);
    const { user, router } = await openTemplates("p");
    await user.click(screen.getByRole("link", { name: "See all" }));
    expect(router.state.location.pathname).toBe("/documents/templates");
    expect(router.state.location.search).toBe("?document=p");
  });

  it("works with the keyboard only", async () => {
    const { api } = setup(POSTING_CV, {
      templates: [fakeTemplate("classic"), fakeTemplate("compact")],
    });
    const { user, strip } = await openTemplates("p");
    within(strip)
      .getByRole("button", { name: /^Classic/ })
      .focus();
    await user.tab();
    expect(
      within(strip).getByRole("button", { name: /^Compact/ }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(writes(api)).toHaveLength(1);
    });
    await user.tab();
    expect(screen.getByRole("link", { name: "See all" })).toHaveFocus();
  });

  it("names every bundled template it is given, the list being the api's", async () => {
    setup(POSTING_CV);
    const { strip } = await openTemplates("p");
    expect(within(strip).getAllByRole("listitem")).toHaveLength(
      TEMPLATES.length,
    );
  });
});
