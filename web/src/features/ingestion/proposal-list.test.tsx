import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/server";

import {
  EMPTY_NOTE,
  EXCERPT_SHOWN,
  FREE_TEXT_NOTE,
  ProposalList,
} from "./proposal-list";

const CV = { id: "doc-cv", name: "cv.txt", deleted: false };
const LETTER = { id: "doc-letter", name: "letter.txt", deleted: false };
const TEXT =
  "Alice Martin, engineer.\nEngineer at Acme Group from March 2021.\n";

function cite(document: typeof CV | null, excerpt: string, more = {}) {
  const start = Math.max(0, TEXT.indexOf(excerpt));
  return {
    document_id: document?.id ?? "doc-gone",
    document,
    excerpt,
    excerpt_cut: false,
    start,
    end: start + excerpt.length,
    transcribed: false,
    ...more,
  };
}

function item(key: string, value: unknown, more = {}) {
  return {
    id: `p-${key}`,
    status: "pending",
    section: "identity",
    key,
    slot: key.split(":")[0],
    action: "add",
    value,
    before: null,
    after: value,
    noop: false,
    stale: false,
    stale_reason: null,
    locked: false,
    bulk: null,
    problem: null,
    flags: [],
    free_text: false,
    citations: [cite(CV, "Alice Martin")],
    conflict: [],
    depends_on: [],
    base_revision: 1,
    applied_revision: null,
    ...more,
  };
}

function counts(items: { status: string }[]) {
  const pending = items.filter((one) => one.status === "pending").length;
  return {
    total: items.length,
    pending,
    accepted: items.filter((one) => one.status === "accepted").length,
    rejected: 0,
    superseded: 0,
    invalid: 0,
    undone: 0,
  };
}

function review(
  sections: Record<string, ReturnType<typeof item>[]>,
  more = {},
) {
  const all = Object.values(sections).flat();
  return {
    run_id: "run-1",
    status: "done",
    partial: false,
    profile_revision: 3,
    counts: counts(all),
    documents: [CV, LETTER],
    sections: Object.entries(sections).map(([section, items]) => ({
      section,
      counts: counts(items),
      items,
    })),
    ...more,
  };
}

/** The api of the list; the answer records every path and query it was asked. */
function fakeApi(
  body: Record<string, unknown>,
  runs: { id: string; status: string }[] = [],
) {
  const calls = { paths: [] as string[], bodies: [] as unknown[] };
  const record = (request: Request) => {
    const url = new URL(request.url);
    calls.paths.push(`${request.method} ${url.pathname}${url.search}`);
  };
  server.use(
    http.get("*/api/ingestion/runs", ({ request }) => {
      record(request);
      return HttpResponse.json({ runs, active_run_id: null });
    }),
    http.get("*/api/ingestion/runs/:id/proposals", ({ request }) => {
      record(request);
      return HttpResponse.json(body);
    }),
    http.get("*/api/ingestion/documents/:id/text", ({ request }) => {
      record(request);
      const offset = Number(new URL(request.url).searchParams.get("offset"));
      return HttpResponse.json({
        text: TEXT.slice(offset),
        offset,
        next_offset: null,
        total: TEXT.length,
        images: [],
      });
    }),
    http.all("*/api/*", async ({ request }) => {
      record(request);
      calls.bodies.push(await request.text());
      return HttpResponse.json({ detail: "unexpected" }, { status: 500 });
    }),
  );
  return calls;
}

function show(runId?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProposalList runId={runId} />
    </QueryClientProvider>,
  );
}

const proposal = (key: string) =>
  screen
    .getAllByTestId("proposal")
    .find((one) => one.dataset.key === key) as HTMLElement;

describe("ProposalList", () => {
  it("shows the proposals of the run per section, with counts, values and sources", async () => {
    const calls = fakeApi(
      review({
        identity: [
          item("full_name", "Alice Martin", {
            before: "A. Martin",
            locked: true,
            citations: [cite(CV, "Alice Martin"), cite(LETTER, "Alice Martin")],
          }),
          item("headline", "Engineer", { free_text: true }),
        ],
        skills: [
          item("years_experience", 2.2, {
            section: "skills",
            flags: ["derived"],
            depends_on: ["employers:acme-group-2021"],
          }),
        ],
      }),
    );
    show("run-1");
    const identity = await screen.findByRole("region", { name: "Identity" });
    expect(within(identity).getByText("2 proposals")).toBeInTheDocument();
    expect(
      screen.getByText(/3 proposals in 2 sections\. Nothing changes/),
    ).toBeInTheDocument();
    const name = proposal("full_name");
    expect(within(name).getByText("A. Martin")).toBeInTheDocument();
    expect(within(name).getByText("Accepted one by one")).toBeInTheDocument();
    const sources = within(name).getByRole("list", {
      name: "Sources of Full name",
    });
    expect(within(sources).getAllByRole("listitem")).toHaveLength(2);
    expect(within(sources).getByText("letter.txt")).toBeInTheDocument();
    expect(
      within(proposal("years_experience")).getByText("Calculated"),
    ).toBeInTheDocument();
    // The sentence about quotes is on the free text item and on no other.
    expect(
      within(proposal("headline")).getByText(FREE_TEXT_NOTE),
    ).toBeInTheDocument();
    expect(within(name).queryByText(FREE_TEXT_NOTE)).toBeNull();
    // Showing the list reads this run's proposals once and the profile's completeness, and
    // writes nothing (the decisions are in proposal-review.test.tsx).
    expect([...calls.paths].sort()).toEqual([
      "GET /api/ingestion/runs/run-1/proposals",
      "GET /api/profile",
    ]);
    expect(calls.bodies).toEqual([""]);
  });

  it("shows a conflict as both values, each with its source", async () => {
    fakeApi(
      review({
        experience: [
          item(
            "employers:acme-group-2021",
            { organisation: "Acme Group", title: "Engineer", start: "2021-03" },
            {
              section: "experience",
              flags: ["conflict"],
              locked: true,
              citations: [cite(CV, "March 2021")],
              conflict: [
                {
                  value: {
                    organisation: "Acme Group",
                    title: "Engineer",
                    start: "2021-05",
                  },
                  citations: [cite(LETTER, "Engineer at Acme")],
                },
              ],
            },
          ),
        ],
      }),
    );
    show("run-1");
    const row = await screen.findByTestId("proposal");
    expect(within(row).getByText("Sources disagree")).toBeInTheDocument();
    const values = within(row).getByRole("radiogroup", {
      name: "Values for Employers: Acme Group",
    });
    expect(within(values).getByText("2021-03")).toBeInTheDocument();
    expect(within(values).getByText("2021-05")).toBeInTheDocument();
    expect(
      within(
        within(values).getByRole("list", { name: "Sources of value 1" }),
      ).getByText("cv.txt"),
    ).toBeInTheDocument();
    expect(
      within(
        within(values).getByRole("list", { name: "Sources of value 2" }),
      ).getByText("letter.txt"),
    ).toBeInTheDocument();
  });

  it("says source deleted and deleted from the data alone", async () => {
    fakeApi(
      review(
        {
          identity: [
            item("full_name", "Alice Martin", {
              citations: [cite(null, "Alice Martin"), cite(LETTER, "engineer")],
            }),
          ],
        },
        { documents: [{ id: "doc-gone", name: null, deleted: true }, LETTER] },
      ),
    );
    show("run-1");
    const row = await screen.findByTestId("proposal");
    const [gone, kept] = within(
      within(row).getByRole("list", { name: "Sources of Full name" }),
    ).getAllByRole("listitem");
    expect(gone).toHaveTextContent("Source deleted");
    expect(gone).toHaveTextContent("Alice Martin");
    expect(within(gone as HTMLElement).queryByRole("button")).toBeNull();
    expect(within(kept as HTMLElement).getByRole("button")).toBeInTheDocument();
    const read = screen.getByRole("list", { name: "Documents read" });
    expect(within(read).getByText("Deleted")).toBeInTheDocument();
    expect(within(read).getByText("letter.txt")).toBeInTheDocument();
  });

  it("says source deleted alone once the delete took the quote too", async () => {
    fakeApi(
      review(
        {
          identity: [
            item("full_name", "Alice Martin", {
              citations: [
                cite(null, "", { source_deleted: true, start: 0, end: 0 }),
                cite(LETTER, "engineer"),
              ],
            }),
          ],
        },
        { documents: [{ id: "doc-gone", name: null, deleted: true }, LETTER] },
      ),
    );
    show("run-1");
    const row = await screen.findByTestId("proposal");
    const [gone, kept] = within(
      within(row).getByRole("list", { name: "Sources of Full name" }),
    ).getAllByRole("listitem");
    expect(gone).toHaveTextContent(/^Source deleted$/);
    expect((gone as HTMLElement).querySelector("q")).toBeNull();
    expect(within(gone as HTMLElement).queryByRole("button")).toBeNull();
    expect(
      within(gone as HTMLElement).queryByText(/open at the quote/),
    ).toBeNull();
    expect((kept as HTMLElement).querySelector("q")).toHaveTextContent(
      "engineer",
    );
  });

  it("renders what a document wrote as text, cut to a bounded length", async () => {
    const hostile = `<img src=x onerror="alert(1)">`;
    const long = hostile + "x".repeat(2 * EXCERPT_SHOWN);
    fakeApi(
      review({
        identity: [
          item("headline", `${hostile}<script>alert(2)</script>`, {
            citations: [cite(CV, long)],
          }),
        ],
      }),
    );
    const { container } = show("run-1");
    const row = await screen.findByTestId("proposal");
    expect(container.querySelector("img, script")).toBeNull();
    expect(within(row).getAllByText(/<img src=x/)).not.toHaveLength(0);
    const quote = row.querySelector("q");
    expect(Array.from(quote?.textContent ?? "")).toHaveLength(
      EXCERPT_SHOWN + 1,
    );
    expect(quote?.textContent.endsWith("…")).toBe(true);
  });

  it("opens a citation on the text of its document, at the quote", async () => {
    const calls = fakeApi(
      review({
        experience: [
          item(
            "employers:acme-group-2021",
            { organisation: "Acme Group", start: "2021-03" },
            { section: "experience", citations: [cite(CV, "March 2021")] },
          ),
        ],
      }),
    );
    show("run-1");
    const user = userEvent.setup();
    const row = await screen.findByTestId("proposal");
    await user.click(
      within(row).getByRole("button", { name: /cv\.txt, open at the quote/ }),
    );
    const dialog = await screen.findByRole("dialog");
    const mark = await within(dialog).findByTestId("cited-quote");
    expect(mark).toHaveTextContent("March 2021");
    expect(
      within(dialog).getByRole("region", { name: "Text of the document" }),
    ).toHaveTextContent("Engineer at Acme Group from March 2021.");
    expect(calls.paths.at(-1)).toBe(
      "GET /api/ingestion/documents/doc-cv/text?offset=0&limit=20000",
    );
  });

  it("says so when the run proposes nothing", async () => {
    fakeApi(review({}));
    show("run-1");
    expect(await screen.findByText(EMPTY_NOTE)).toBeInTheDocument();
    expect(screen.queryAllByTestId("proposal")).toHaveLength(0);
  });

  it("shows a decided and a stale item as such", async () => {
    fakeApi(
      review({
        identity: [
          item("headline", "Engineer", { status: "accepted", after: null }),
          item("full_name", "Alice Martin", {
            stale: true,
            stale_reason: "profile_changed",
          }),
        ],
      }),
    );
    show("run-1");
    const identity = await screen.findByRole("region", { name: "Identity" });
    expect(
      within(identity).getByText("2 proposals, 1 left to review"),
    ).toBeInTheDocument();
    expect(
      within(proposal("headline")).getByText("Accepted"),
    ).toBeInTheDocument();
    expect(
      within(proposal("full_name")).getByText("Check again"),
    ).toBeInTheDocument();
  });

  it("finds the latest finished run by itself, and renders nothing without one", async () => {
    const calls = fakeApi(
      review({ identity: [item("full_name", "Alice Martin")] }),
      [
        { id: "run-3", status: "running" },
        { id: "run-2", status: "done" },
        { id: "run-1", status: "done" },
      ],
    );
    show();
    expect(await screen.findByTestId("proposal")).toBeInTheDocument();
    // The profile is read too, for the completeness bar (task G13).
    expect(calls.paths.filter((path) => path !== "GET /api/profile")).toEqual([
      "GET /api/ingestion/runs",
      "GET /api/ingestion/runs/run-2/proposals",
    ]);
  });

  it("renders nothing while no run has finished", async () => {
    const calls = fakeApi(review({}), [{ id: "run-3", status: "failed" }]);
    const { container } = show();
    await expect.poll(() => calls.paths).toEqual(["GET /api/ingestion/runs"]);
    expect(container).toBeEmptyDOMElement();
  });

  it("uses no dash as a separator in its own wording", async () => {
    fakeApi(
      review({
        identity: [
          item("full_name", "Alice Martin", {
            stale: true,
            stale_reason: "depends_on_changed",
            free_text: true,
            locked: true,
            flags: [
              "conflict",
              "derived",
              "transcribed",
              "looks_like_instruction",
            ],
          }),
        ],
      }),
    );
    const { container } = show("run-1");
    await screen.findByTestId("proposal");
    expect(container.textContent).not.toMatch(/[—–]| - /);
  });
});
