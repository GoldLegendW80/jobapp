import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import {
  fakeCv,
  fakeDocumentOf,
  fakeDocuments,
  fakeVersion,
} from "@/test/documents";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { waitForOnFakeClock } from "@/test/clock";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { PostingDocument } from "./documents-api";
import { POLL_MS } from "./labels";
import { cardState, LETTER_FOLLOW_MS } from "./posting-documents-state";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const PATH = `/jobs/${JOB_ID}`;

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

function postingDocument(
  kind: "cv" | "letter",
  overrides: Partial<PostingDocument> = {},
): PostingDocument {
  const base = fakeDocumentOf(fakeCv({ id: `${kind}-doc` }));
  return {
    ...base,
    scope: "posting",
    kind,
    posting_id: JOB_ID,
    base_document_id: kind === "cv" ? "cv-1" : null,
    name: null,
    approved: null,
    sent: [],
    generation: null,
    ...overrides,
  };
}

interface Posted {
  path: string;
  body: unknown;
}

/** A fake of the posting's documents, the spend and the writes, in memory. */
function fakePosting(
  documents: PostingDocument[],
  options: { fit?: number; auto?: boolean; minFit?: number } = {},
) {
  fakeSession(member);
  fakeProfile();
  const api = fakeDocuments({ cvs: [fakeCv()] });
  // The job is served by reference, so a test can move its status between two reads.
  const job = makeDetail({
    id: JOB_ID,
    fit: options.fit ?? 80,
    status: "found",
  });
  const state = {
    documents,
    posted: [] as Posted[],
    reads: 0,
    api,
    job,
    detail: fakeJobDetail(job),
  };
  server.use(
    http.get("*/api/jobs/:id/documents", () => {
      state.reads += 1;
      return HttpResponse.json({ documents: state.documents });
    }),
    http.get("*/api/documents/spend", () =>
      HttpResponse.json({
        day: "2026-10-14",
        auto: options.auto ?? true,
        min_fit: options.minFit ?? 70,
        daily_ceiling: 100,
        written_today: 0,
        waiting: 0,
        postings_30_days: 0,
        to_write: 0,
        to_write_estimate: { tokens: { low: 0, high: 0 }, cost: null },
        priced: false,
        cost_note: "",
        today: { input: 0, output: 0, cost_usd: "0" },
        last_30_days: { input: 0, output: 0, cost_usd: "0" },
      }),
    ),
    http.post("*/api/jobs/:id/documents/write", async ({ request }) => {
      const body = (await request.json()) as { kinds: string[] };
      state.posted.push({ path: new URL(request.url).pathname, body });
      return HttpResponse.json(
        {
          generations: body.kinds.map((kind) => ({
            id: `g-${kind}`,
            kind,
            status: "queued",
            wait_reason: null,
          })),
        },
        { status: 202 },
      );
    }),
    http.post("*/api/documents/:id/fit", ({ request, params }) => {
      state.posted.push({ path: new URL(request.url).pathname, body: params });
      return HttpResponse.json(
        { id: "g-fit", status: "queued", wait_reason: null, error: null },
        { status: 202 },
      );
    }),
  );
  return state;
}

async function openSection() {
  const user = userEvent.setup();
  const app = renderApp(PATH);
  const heading = await screen.findByRole("heading", { name: "Documents" });
  const section = heading.closest("section") as HTMLElement;
  await waitFor(() => {
    expect(within(section).getAllByTestId("card-state")).toHaveLength(2);
  });
  return { user, section, ...app };
}

const ready = (kind: "cv" | "letter", extra: Partial<PostingDocument> = {}) =>
  postingDocument(kind, { latest: fakeVersion({ number: 2 }), ...extra });

beforeEach(() => {
  fakeSession(member);
});

describe("the Documents section of a posting", () => {
  it("shows both documents ready on one page, each with a page 1 preview, Open and Download PDF", async () => {
    fakePosting([ready("cv"), ready("letter")]);
    const { section } = await openSection();
    const cv = within(section).getByTestId("posting-cv");
    expect(within(cv).getByText("Ready, on one page")).toBeVisible();
    expect(
      within(cv).getByRole("img", { name: "Page 1 of the CV, version 2" }),
    ).toHaveAttribute(
      "src",
      expect.stringContaining("/api/documents/cv-doc/versions/2/page1.png"),
    );
    expect(
      within(cv).getByRole("link", { name: "Open the CV" }),
    ).toHaveAttribute("href", `/jobs/${JOB_ID}/documents/cv`);
    expect(
      within(cv).getByRole("link", { name: "Download PDF of the CV" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("/api/documents/cv-doc/versions/2/pdf"),
    );
    const letter = within(section).getByTestId("posting-letter");
    expect(within(letter).getByText("Ready, on one page")).toBeVisible();
    expect(
      within(letter).getByRole("img", {
        name: "Page 1 of the letter, version 2",
      }),
    ).toBeVisible();
    expect(
      within(letter).getByRole("link", { name: "Open the letter" }),
    ).toBeVisible();
    // Label in name: the accessible name of the download starts with the words on the button.
    for (const download of within(section).getAllByRole("link", {
      name: /^Download PDF of the (CV|letter)$/,
    })) {
      expect(download).toHaveTextContent("Download PDF");
    }
    const names = [
      ...Array.from(section.querySelectorAll("[aria-label]"), (one) =>
        one.getAttribute("aria-label"),
      ),
      ...Array.from(section.querySelectorAll("img"), (one) => one.alt),
      ...Array.from(
        section.querySelectorAll(".sr-only"),
        (one) => one.textContent,
      ),
    ];
    expect(names.filter((name) => /\bcv\b/.test(name ?? ""))).toEqual([]);
    expect(within(letter).queryByText(/Based on/)).toBeNull();
    expect(within(cv).queryByRole("button", { name: "Write now" })).toBeNull();
    expect(section.textContent).not.toMatch(SEPARATOR);
  });

  it("says which base CV the CV came from, and writes it again from another one", async () => {
    const api = fakePosting([ready("cv"), ready("letter")]);
    const other = fakeCv({
      id: "cv-2",
      name: "Platform CV",
      is_default: false,
    });
    api.api.cvs.push(other);
    const { user, section } = await openSection();
    const cv = within(section).getByTestId("posting-cv");
    expect(cv).toHaveTextContent("Based on: My CV");
    expect(
      within(cv).queryByRole("button", { name: "Write it again from this CV" }),
    ).toBeNull();

    await user.selectOptions(within(cv).getByRole("combobox"), "cv-2");
    expect(cv).toHaveTextContent(
      "The letter is then written again from the new CV.",
    );
    await user.click(
      within(cv).getByRole("button", { name: "Write it again from this CV" }),
    );
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    // The letter follows the new CV, as the automatic chain writes it.
    expect(api.posted[0]?.body).toEqual({
      kinds: ["cv", "letter"],
      base_document_id: "cv-2",
    });
  });

  it("writes the letter again after the CV when the CV is tried again and a letter exists", async () => {
    const api = fakePosting([
      ready("cv", {
        generation: {
          status: "failed",
          wait_reason: null,
          error: "run_failed",
        },
      }),
      ready("letter"),
    ]);
    const { user, section } = await openSection();
    const cv = within(section).getByTestId("posting-cv");
    await user.click(within(cv).getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    expect(api.posted[0]?.body).toEqual({ kinds: ["cv", "letter"] });
  });

  it("writes the CV and the letter in one click when neither exists", async () => {
    const api = fakePosting([], { fit: 80 });
    const { user, section } = await openSection();
    for (const kind of ["posting-cv", "posting-letter"]) {
      expect(within(section).getByTestId(kind)).toHaveTextContent(
        "Not written yet",
      );
    }
    // The letter is written from the CV, so it has no button of its own before the CV exists.
    expect(
      within(within(section).getByTestId("posting-letter")).queryByRole(
        "button",
      ),
    ).toBeNull();
    await user.click(
      within(section).getByRole("button", { name: "Write both now" }),
    );
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    expect(api.posted[0]?.path).toBe(`/api/jobs/${JOB_ID}/documents/write`);
    expect(api.posted[0]?.body).toEqual({ kinds: ["cv", "letter"] });
  });

  it("names the threshold for a posting under it, and the button writes the CV", async () => {
    const api = fakePosting([], { fit: 58, minFit: 70 });
    const { user, section } = await openSection();
    expect(
      within(section).getAllByText("Fit 58 is under your threshold of 70"),
    ).toHaveLength(2);
    await user.click(
      within(within(section).getByTestId("posting-cv")).getByRole("button", {
        name: "Write now",
      }),
    );
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    expect(api.posted[0]?.body).toEqual({ kinds: ["cv"] });
  });

  it("says nothing is written automatically when the switch is off", async () => {
    fakePosting([], { fit: 90, auto: false });
    const { section } = await openSection();
    expect(
      within(section).getAllByText(
        "Documents are not written automatically. Write it when you want it.",
      ),
    ).toHaveLength(2);
  });

  it("explains a wait and links to where it is settled; Write now replaces only a wait of the automatic writer", async () => {
    const api = fakePosting([
      ready("cv", {
        generation: { status: "waiting", wait_reason: "credential" },
      }),
      ready("letter", {
        generation: { status: "waiting", wait_reason: "daily_ceiling" },
      }),
    ]);
    const { user, section } = await openSection();
    const cv = within(section).getByTestId("posting-cv");
    expect(cv).toHaveTextContent(
      "Waiting for a model credential. Add one in your account.",
    );
    expect(
      within(cv).getByRole("link", { name: "Open your account" }),
    ).toHaveAttribute("href", "/account");
    // A click waits for the credential too: the button would only add a wait.
    expect(within(cv).queryByRole("button", { name: "Write now" })).toBeNull();
    const letter = within(section).getByTestId("posting-letter");
    expect(letter).toHaveTextContent(
      "Waiting for tomorrow: the safety ceiling of the day is reached.",
    );
    // A click is never limited by the safety ceiling: it writes the letter now.
    await user.click(within(letter).getByRole("button", { name: "Write now" }));
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    expect(api.posted[0]?.body).toEqual({ kinds: ["letter"] });
  });

  it("offers no click while a written click waits for the consent, only the way to give it", async () => {
    const api = fakePosting([
      ready("cv", {
        generation: { status: "waiting", wait_reason: "consent" },
      }),
      ready("letter", {
        generation: { status: "waiting", wait_reason: "consent" },
      }),
    ]);
    api.api.cvs.push(
      fakeCv({ id: "cv-2", name: "Platform CV", is_default: false }),
    );
    const { user, section } = await openSection();
    for (const testId of ["posting-cv", "posting-letter"]) {
      const card = within(section).getByTestId(testId);
      expect(card).toHaveTextContent(
        "Waiting for you to confirm what is sent.",
      );
      expect(
        within(card).getByRole("link", { name: "Open the CV tab" }),
      ).toHaveAttribute("href", "/profile/cv");
      expect(
        within(card).queryByRole("button", { name: "Write now" }),
      ).toBeNull();
    }
    const cv = within(section).getByTestId("posting-cv");
    await user.selectOptions(within(cv).getByRole("combobox"), "cv-2");
    expect(
      within(cv).getByRole("button", { name: "Write it again from this CV" }),
    ).toBeDisabled();
    expect(api.posted).toEqual([]);
  });

  it("says why a generation failed and offers Try again", async () => {
    const api = fakePosting([
      postingDocument("cv", {
        generation: {
          status: "failed",
          wait_reason: null,
          error: "token_limit",
        },
      }),
    ]);
    const { user, section } = await openSection();
    const cv = within(section).getByTestId("posting-cv");
    expect(cv).toHaveTextContent(
      "Writing failed. The writer reached its limit before it finished. Try again.",
    );
    await user.click(within(cv).getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    expect(api.posted[0]?.body).toEqual({ kinds: ["cv"] });
  });

  it("shows an overflow in red with Make it fit, which asks for a shorter version", async () => {
    const api = fakePosting([
      ready("cv", { latest: fakeVersion({ number: 3, fit: "overflow" }) }),
    ]);
    const { user, section } = await openSection();
    const cv = within(section).getByTestId("posting-cv");
    expect(within(cv).getByText(/Longer than one page/)).toBeVisible();
    await user.click(within(cv).getByRole("button", { name: "Make it fit" }));
    await waitFor(() => {
      expect(api.posted).toHaveLength(1);
    });
    expect(api.posted[0]?.path).toBe("/api/documents/cv-doc/fit");
  });

  it("shows the date a frozen version was sent", async () => {
    fakePosting([
      ready("cv", {
        sent: [2],
        latest: fakeVersion({
          number: 2,
          frozen_at: "2026-10-12T10:00:00Z",
          frozen_reason: "applied",
        }),
      }),
    ]);
    const { section } = await openSection();
    expect(within(section).getByTestId("posting-cv")).toHaveTextContent(
      /Sent on Oct 12, 2026/,
    );
  });

  it("reads the documents again while one is being written, and shows the result", async () => {
    const api = fakePosting([
      postingDocument("cv", {
        latest: null,
        generation: { status: "running", wait_reason: null },
      }),
    ]);
    const { section } = await openSection();
    expect(within(section).getByTestId("posting-cv")).toHaveTextContent(
      "Writing the CV",
    );
    api.documents = [ready("cv"), ready("letter")];
    await waitFor(
      () => {
        expect(within(section).getAllByText("Ready, on one page")).toHaveLength(
          2,
        );
      },
      { timeout: POLL_MS * 4 },
    );
  });

  describe("on the test's own clock", () => {
    beforeEach(() => {
      vi.useFakeTimers({
        toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
      });
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    const writing = (kind: "cv" | "letter") =>
      postingDocument(kind, {
        latest: null,
        generation: { status: "running", wait_reason: null },
      });

    async function openOnFakeClock() {
      const app = renderApp(PATH);
      await waitForOnFakeClock(() => {
        expect(screen.getAllByTestId("card-state")).toHaveLength(2);
      });
      return app;
    }

    it("reads the posting again when the CV's page is drawn, so its status follows", async () => {
      const api = fakePosting([
        ready("cv", {
          render: {
            state: "rendering",
            revision: 2,
            error: null,
            message: null,
          },
        }),
        ready("letter"),
      ]);
      const { container } = await openOnFakeClock();
      expect(screen.getByTestId("posting-cv")).toHaveTextContent(
        "Drawing the page",
      );
      const badge = () => container.querySelector("[data-status]");
      expect(badge()).toHaveAttribute("data-status", "found");
      const reads = api.detail.detailCalls;
      // The render job moves the posting to cv_ready when it stores the one page PDF.
      api.job.status = "cv_ready";
      api.documents = [ready("cv"), ready("letter")];
      await waitForOnFakeClock(() => {
        expect(screen.getByTestId("posting-cv")).toHaveTextContent(
          "Ready, on one page",
        );
        expect(badge()).toHaveAttribute("data-status", "cv_ready");
      }, 100);
      expect(badge()).toHaveTextContent("CV ready");
      expect(api.detail.detailCalls).toBe(reads + 1);
    });

    it("keeps reading after the CV is written until the letter that follows it is being written", async () => {
      const api = fakePosting([writing("cv")]);
      await openOnFakeClock();
      expect(screen.getByTestId("posting-cv")).toHaveTextContent(
        "Writing the CV",
      );
      // The CV is written and drawn; the worker has not made the letter's generation yet.
      api.documents = [ready("cv")];
      await waitForOnFakeClock(() => {
        expect(screen.getByTestId("posting-cv")).toHaveTextContent(
          "Ready, on one page",
        );
      }, 100);
      const reads = api.reads;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POLL_MS * 2);
      });
      expect(api.reads).toBeGreaterThan(reads);
      api.documents = [ready("cv"), writing("letter")];
      await waitForOnFakeClock(() => {
        expect(screen.getByTestId("posting-letter")).toHaveTextContent(
          "Writing the letter",
        );
      }, 100);
    });

    it("stops reading when no letter follows within the window", async () => {
      const api = fakePosting([writing("cv")]);
      await openOnFakeClock();
      api.documents = [ready("cv")];
      await waitForOnFakeClock(() => {
        expect(screen.getByTestId("posting-cv")).toHaveTextContent(
          "Ready, on one page",
        );
      }, 100);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(LETTER_FOLLOW_MS + POLL_MS);
      });
      const reads = api.reads;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(LETTER_FOLLOW_MS * 4);
      });
      expect(api.reads).toBe(reads);
      expect(screen.getByTestId("posting-letter")).toHaveTextContent(
        "Not written yet",
      );
    });

    it("expects no letter after a CV generation that failed", async () => {
      const api = fakePosting([writing("cv")]);
      await openOnFakeClock();
      api.documents = [
        postingDocument("cv", {
          latest: null,
          generation: {
            status: "failed",
            wait_reason: null,
            error: "run_failed",
          },
        }),
      ];
      await waitForOnFakeClock(() => {
        expect(screen.getByTestId("posting-cv")).toHaveTextContent(
          "Writing failed",
        );
      }, 100);
      const reads = api.reads;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(LETTER_FOLLOW_MS);
      });
      expect(api.reads).toBe(reads);
    });
  });

  it("asks for a CV or an experience when the profile has neither", async () => {
    fakePosting([]);
    fakeProfile({});
    fakeDocuments();
    const { section } = await openSection();
    expect(
      await within(section).findByTestId("no-experience-line"),
    ).toHaveTextContent(
      "Add your CV or fill your experience to get documents for each posting.",
    );
    expect(
      within(section).queryByRole("button", { name: "Write both now" }),
    ).toBeNull();
  });

  it("does not ask when the profile has an experience", async () => {
    fakePosting([]);
    fakeDocuments();
    const { section } = await openSection();
    expect(within(section).queryByTestId("no-experience-line")).toBeNull();
  });
});

describe("the document page of a posting", () => {
  it("shows the document view of the posting's CV at /jobs/:id/documents/cv", async () => {
    const api = fakePosting([ready("cv"), ready("letter")]);
    api.api.documents.set("cv-doc", ready("cv"));
    renderApp(`${PATH}/documents/cv`);
    expect(
      await screen.findByRole("heading", { level: 1, name: "CV" }),
    ).toBeVisible();
    expect(await screen.findByTestId("document-view")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Back to the posting" }),
    ).toHaveAttribute("href", PATH);
  });

  it("says a letter that was never written has not been", async () => {
    fakePosting([ready("cv")]);
    renderApp(`${PATH}/documents/letter`);
    expect(
      await screen.findByText(
        /The letter of this posting has not been written/,
      ),
    ).toBeVisible();
  });

  it("is not found for a kind that is neither cv nor letter", async () => {
    fakePosting([]);
    renderApp(`${PATH}/documents/photo`);
    expect(await screen.findByText(/not found/i)).toBeVisible();
  });
});

describe("the state of a card", () => {
  const doc = (overrides: Partial<PostingDocument>) =>
    postingDocument("cv", overrides);
  const on = { auto: true, min: 70 };
  it.each([
    ["no document, fit at the threshold", undefined, 70, on, { tag: "empty" }],
    [
      "no document, one under",
      undefined,
      69,
      on,
      { tag: "under", fit: 69, min: 70 },
    ],
    ["no document, no score", undefined, null, on, { tag: "empty" }],
    [
      "no document, switch off",
      undefined,
      10,
      { auto: false, min: 70 },
      { tag: "manual" },
    ],
    ["threshold not known yet", undefined, 10, undefined, { tag: "empty" }],
    [
      "waiting with a reason",
      doc({ generation: { status: "waiting", wait_reason: "consent" } }),
      80,
      on,
      { tag: "waiting", reason: "consent" },
    ],
    [
      "queued",
      doc({ generation: { status: "queued", wait_reason: null } }),
      80,
      on,
      { tag: "writing" },
    ],
    [
      "failed",
      doc({
        generation: {
          status: "failed",
          wait_reason: null,
          error: "run_failed",
        },
      }),
      80,
      on,
      { tag: "failed", error: "run_failed" },
    ],
    [
      "failed after a version exists",
      doc({
        latest: fakeVersion(),
        generation: { status: "failed", wait_reason: null, error: null },
      }),
      80,
      on,
      { tag: "failed", error: null },
    ],
    [
      "done, nothing open",
      doc({ latest: null, generation: { status: "done", wait_reason: null } }),
      80,
      on,
      { tag: "empty" },
    ],
    ["one page", doc({ latest: fakeVersion() }), 10, on, { tag: "ready" }],
    [
      "overflow",
      doc({ latest: fakeVersion({ fit: "overflow" }) }),
      80,
      on,
      { tag: "overflow" },
    ],
    [
      "not drawn yet",
      doc({ latest: fakeVersion({ fit: null }) }),
      80,
      on,
      { tag: "unrendered", message: null },
    ],
    [
      "drawing",
      doc({
        latest: fakeVersion(),
        render: { state: "rendering", revision: 2, error: null, message: null },
      }),
      80,
      on,
      { tag: "drawing" },
    ],
    [
      "sent",
      doc({
        sent: [1],
        latest: fakeVersion({ frozen_at: "2026-10-12T10:00:00Z" }),
      }),
      80,
      on,
      { tag: "sent", at: "2026-10-12T10:00:00Z" },
    ],
    [
      "an older version was sent, the latest is a draft since",
      doc({ sent: [1], latest: fakeVersion({ number: 2 }) }),
      80,
      on,
      { tag: "ready" },
    ],
  ])("%s", (_name, document, fit, threshold, expected) => {
    expect(cardState(document, fit, threshold)).toEqual(expected);
  });
});
