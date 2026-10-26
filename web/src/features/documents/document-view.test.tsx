import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fakeCv,
  fakeDocumentOf,
  fakeDocuments,
  fakeVersion,
} from "@/test/documents";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { createQueryClient } from "@/lib/query";

import { DocumentView } from "./document-view";
import {
  documentKey,
  templatesKey,
  versionsKey,
  type Generation,
} from "./documents-api";
import { pollInterval, POLL_MS, WAITING_POLL_MS } from "./labels";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const open = (
  status: Generation["status"],
  wait_reason: Generation["wait_reason"] = null,
): Generation => ({ id: "g", status, wait_reason, error: null });

describe("the document view", () => {
  const version = fakeVersion({ number: 2, corrections_count: 1 });

  it("draws no page of the command line's PDF and links to it", async () => {
    const api = fakeDocuments();
    const cv = fakeCv({ id: "a", name: "Imported CV" });
    const legacy = fakeVersion({
      origin: "legacy",
      frozen_at: "2026-10-14T09:00:00Z",
      frozen_reason: "legacy_applied",
      has_preview: false,
    });
    api.documents.set("a", fakeDocumentOf(cv, { latest: legacy }));
    api.versions.set("a", [legacy]);
    renderWithQuery(<DocumentView documentId="a" />);

    expect(
      await screen.findByText(/This is the PDF your command line made/),
    ).toBeVisible();
    expect(
      screen.queryByRole("img", { name: /^Page 1 of version/ }),
    ).toBeNull();
    expect(screen.getByRole("link", { name: "Open the PDF" })).toHaveAttribute(
      "href",
      "/api/documents/a/versions/1/pdf",
    );
  });

  it("shows the page, one page, the PDF links, the corrections and the history", async () => {
    const api = fakeDocuments();
    const cv = fakeCv({ id: "a", name: "Main CV" });
    api.documents.set("a", fakeDocumentOf(cv, { latest: version }));
    api.versions.set("a", [
      version,
      fakeVersion({ number: 1, origin: "edited" }),
    ]);
    api.corrections.set("a", [
      {
        field_path: "highlights.1",
        before: "Led 40 engineers",
        after: "",
        code: "number",
        by: "code",
        reason: "A number your profile does not state",
      },
    ]);
    const user = userEvent.setup();
    renderWithQuery(<DocumentView documentId="a" />);

    expect(
      await screen.findByRole("img", { name: "Page 1 of version 2" }),
    ).toHaveAttribute("src", "/api/documents/a/versions/2/page1.png?width=900");
    expect(screen.getByTestId("fit")).toHaveTextContent("One page");
    expect(screen.getByRole("link", { name: "Open the PDF" })).toHaveAttribute(
      "href",
      "/api/documents/a/versions/2/pdf",
    );
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute(
      "download",
    );

    const corrections = await screen.findByRole("region", {
      name: "Corrected by the fact check (1)",
    });
    expect(
      await within(corrections).findByText("Highlights, item 2"),
    ).toBeVisible();
    expect(corrections).toHaveTextContent("Before: Led 40 engineers");
    expect(corrections).toHaveTextContent("After: Removed");
    expect(corrections).toHaveTextContent(
      "A number your profile does not state",
    );
    expect(corrections).toHaveTextContent("changed by a rule");

    const history = await screen.findByRole("region", { name: "Versions" });
    expect(history).toHaveTextContent("Version 2, Written");
    expect(history).toHaveTextContent("Version 1, Edited by hand");
    await user.click(
      within(history).getByRole("button", { name: "Open version 1" }),
    );
    expect(
      await screen.findByRole("img", { name: "Page 1 of version 1" }),
    ).toBeVisible();
    // The label of a button holds its visible text, "Shown" or "Open".
    expect(
      within(history).getByRole("button", { name: "Shown, version 1" }),
    ).toHaveTextContent("Shown");
    expect(
      within(history).getByRole("button", { name: "Open version 2" }),
    ).toHaveTextContent("Open");
    // Version 1 has no corrections: the section goes with it.
    expect(
      screen.queryByRole("region", { name: /Corrected by the fact check/ }),
    ).toBeNull();

    // Edit (D16), Chat (D17) and Template (D18) are offered; Compare waits for its task.
    expect(screen.getByRole("tab", { name: "Edit" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Chat" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Template" })).toBeVisible();
    expect(screen.queryByRole("tab", { name: "Compare" })).toBeNull();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("says overflow in red, keeps the download, and names a sent version", async () => {
    const api = fakeDocuments();
    const over = fakeVersion({
      number: 3,
      fit: "overflow",
      frozen_at: "2026-10-12T10:00:00Z",
      frozen_reason: "marked_sent",
    });
    api.documents.set(
      "a",
      fakeDocumentOf(fakeCv({ id: "a" }), { latest: over }),
    );
    api.versions.set("a", [over]);
    renderWithQuery(<DocumentView documentId="a" />);
    expect(
      await screen.findByText("This is longer than one page"),
    ).toBeVisible();
    expect(screen.getByTestId("fit")).toHaveTextContent("Longer than one page");
    expect(screen.getByRole("link", { name: "Download PDF" })).toBeVisible();
    expect(screen.getAllByText(/Sent on/).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("shows the state while the first version is written, with no page", async () => {
    const api = fakeDocuments();
    const cv = fakeCv({ id: "a", last_version_at: null });
    api.documents.set("a", fakeDocumentOf(cv));
    renderWithQuery(
      <DocumentView documentId="a" generation={open("running")} />,
    );
    expect(
      await screen.findByText("Your first CV is being built from your profile"),
    ).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it.each([
    ["credential", "Waiting for a model credential. Add one in your account."],
    [
      "daily_ceiling",
      "Waiting for tomorrow: the safety ceiling of the day is reached.",
    ],
    ["base_cv", "Waiting for your base CV to exist."],
  ] as const)("says why a generation waits on %s", async (reason, sentence) => {
    const api = fakeDocuments();
    api.documents.set(
      "a",
      fakeDocumentOf(fakeCv({ id: "a", last_version_at: null })),
    );
    renderWithQuery(
      <DocumentView documentId="a" generation={open("waiting", reason)} />,
    );
    expect(await screen.findByText(sentence)).toBeVisible();
  });

  it("says a failed attempt and keeps the page that exists", async () => {
    const api = fakeDocuments();
    api.documents.set("a", fakeDocumentOf(fakeCv({ id: "a" })));
    renderWithQuery(
      <DocumentView
        documentId="a"
        generation={{ ...open("failed"), error: "token_limit" }}
      />,
    );
    expect(await screen.findByText("The last attempt failed")).toBeVisible();
    expect(
      screen.getByText(
        "The writer reached its limit before it finished. Try again.",
      ),
    ).toBeVisible();
    expect(
      await screen.findByRole("img", { name: /^Page 1 of version/ }),
    ).toBeVisible();
  });

  it("draws the page again while it renders, then shows the new version", async () => {
    const api = fakeDocuments();
    const cv = fakeCv({ id: "a" });
    api.documents.set(
      "a",
      fakeDocumentOf(cv, {
        render: { state: "rendering", revision: 2, error: null, message: null },
      }),
    );
    renderWithQuery(<DocumentView documentId="a" />);
    expect(
      await screen.findByText("A newer page is being drawn"),
    ).toBeVisible();
    api.documents.set(
      "a",
      fakeDocumentOf(cv, { latest: fakeVersion({ number: 2 }) }),
    );
    api.versions.set("a", [fakeVersion({ number: 2 }), fakeVersion()]);
    expect(
      await screen.findByRole(
        "img",
        { name: "Page 1 of version 2" },
        { timeout: POLL_MS * 3 },
      ),
    ).toBeVisible();
    expect(screen.queryByText("A newer page is being drawn")).toBeNull();
  });

  it("reads the document once more when the generation ends between two polls", async () => {
    const api = fakeDocuments();
    const cv = fakeCv({ id: "a", last_version_at: null });
    api.documents.set("a", fakeDocumentOf(cv));
    const client = createQueryClient();
    const ui = (generation: Generation) => (
      <QueryClientProvider client={client}>
        <DocumentView documentId="a" generation={generation} />
      </QueryClientProvider>
    );
    const view = render(ui(open("running")));
    expect(
      await screen.findByText("Your first CV is being built from your profile"),
    ).toBeVisible();
    // The writer finishes; the list that owns the generation says so before the document's own
    // timer fires.
    api.documents.set(
      "a",
      fakeDocumentOf(cv, { latest: fakeVersion({ number: 1 }) }),
    );
    api.versions.set("a", [fakeVersion({ number: 1 })]);
    view.rerender(ui(open("done")));
    expect(
      await screen.findByRole("img", { name: "Page 1 of version 1" }),
    ).toBeVisible();
    expect(
      screen.queryByText("Nothing has been written for this document yet."),
    ).toBeNull();
  });

  it("reads the document once more when the generation fails", async () => {
    const api = fakeDocuments();
    const cv = fakeCv({ id: "a", last_version_at: null });
    api.documents.set("a", fakeDocumentOf(cv));
    const client = createQueryClient();
    const ui = (generation: Generation) => (
      <QueryClientProvider client={client}>
        <DocumentView documentId="a" generation={generation} />
      </QueryClientProvider>
    );
    const view = render(ui(open("running")));
    expect(
      await screen.findByText("Your first CV is being built from your profile"),
    ).toBeVisible();
    api.documents.set(
      "a",
      fakeDocumentOf(cv, { latest: fakeVersion({ number: 1 }) }),
    );
    api.versions.set("a", [fakeVersion({ number: 1 })]);
    view.rerender(ui({ ...open("failed"), error: "run_failed" }));
    expect(
      await screen.findByRole("img", { name: "Page 1 of version 1" }),
    ).toBeVisible();
  });

  it.each([
    ["idle", null, false],
    ["idle", open("done"), false],
    ["idle", open("failed"), false],
    ["idle", open("queued"), POLL_MS],
    ["idle", open("running"), POLL_MS],
    ["idle", open("waiting", "consent"), WAITING_POLL_MS],
    ["idle", open("waiting", "daily_ceiling"), WAITING_POLL_MS],
    ["rendering", null, POLL_MS],
    ["rendering", open("waiting", "consent"), POLL_MS],
  ] as const)(
    "polls %s with %j every %j",
    (renderState, generation, expected) => {
      expect(pollInterval(renderState, generation)).toBe(expected);
    },
  );

  it("says a render that failed, with the api's own sentence", async () => {
    const api = fakeDocuments();
    api.documents.set(
      "a",
      fakeDocumentOf(fakeCv({ id: "a" }), {
        render: {
          state: "idle",
          revision: 1,
          error: "timeout",
          message: "The page took too long to draw. Try again.",
        },
      }),
    );
    renderWithQuery(<DocumentView documentId="a" />);
    expect(
      await screen.findByText("The page could not be drawn"),
    ).toBeVisible();
    expect(
      screen.getByText("The page took too long to draw. Try again."),
    ).toBeVisible();
  });

  it("answers a document that cannot be loaded, and retries", async () => {
    let answers = 0;
    server.use(
      http.get("*/api/documents/a", () => {
        answers += 1;
        return answers === 1
          ? HttpResponse.json({ detail: "Not found." }, { status: 404 })
          : HttpResponse.json(fakeDocumentOf(fakeCv({ id: "a" })));
      }),
      http.get("*/api/documents/a/versions", () =>
        HttpResponse.json([fakeVersion()]),
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<DocumentView documentId="a" />);
    expect(
      await screen.findByText("The document could not be loaded"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(screen.queryByText("The document could not be loaded")).toBeNull();
    });
    expect(
      await screen.findByRole("img", { name: /^Page 1 of version/ }),
    ).toBeVisible();
  });

  it("writes nothing for a document that has no version and no generation", async () => {
    const api = fakeDocuments();
    api.documents.set(
      "a",
      fakeDocumentOf(fakeCv({ id: "a", last_version_at: null })),
    );
    renderWithQuery(<DocumentView documentId="a" />);
    expect(
      await screen.findByText(
        "Nothing has been written for this document yet.",
      ),
    ).toBeVisible();
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the independent capture lifecycle", () => {
  it.each(["done", "failed"] as const)(
    "leaves the document unread during capture, then reads it once on %s",
    async (terminal) => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      const api = fakeDocuments();
      const cv = fakeCv({ id: "a", source: "uploaded" });
      api.documents.set("a", fakeDocumentOf(cv));
      const client = createQueryClient();
      client.setQueryData(templatesKey, []);
      client.setQueryData([...versionsKey("a"), 1], [fakeVersion()]);
      const ui = (capture: Generation) => (
        <QueryClientProvider client={client}>
          <DocumentView
            documentId="a"
            generation={open("done")}
            capture={capture}
          />
        </QueryClientProvider>
      );
      const view = render(ui(open("queued")));
      await screen.findByRole("link", { name: "Download PDF" });
      const reads = () =>
        api.requests.filter(
          (one) => one.method === "GET" && one.path === "/api/documents/a",
        ).length;
      expect(reads()).toBe(1);
      view.rerender(ui(open("running")));
      await act(() => vi.advanceTimersByTimeAsync(WAITING_POLL_MS));
      expect(client.isFetching({ queryKey: documentKey("a") })).toBe(0);
      expect(reads()).toBe(1);
      api.documents.set(
        "a",
        fakeDocumentOf(cv, {
          latest: fakeVersion({ number: 2 }),
          template_slug: "upload-new",
        }),
      );
      api.versions.set("a", [fakeVersion({ number: 2 }), fakeVersion()]);
      view.rerender(ui(open(terminal)));
      expect(
        await screen.findByRole("img", { name: "Page 1 of version 2" }),
      ).toBeVisible();
      expect(reads()).toBe(2);
      if (terminal === "done") {
        expect(client.getQueryState(templatesKey)?.isInvalidated).toBe(true);
        expect(
          api.requests.filter(
            (one) =>
              one.method === "GET" && one.path === "/api/documents/a/versions",
          ).length,
        ).toBeGreaterThanOrEqual(2);
      }
      expect(screen.queryByText("The last attempt failed")).toBeNull();
      expect(screen.getByRole("tab", { name: "Edit" })).toBeEnabled();
      expect(screen.getByRole("link", { name: "Download PDF" })).toBeVisible();
      await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 20));
      expect(reads()).toBe(2);
    },
  );
});
