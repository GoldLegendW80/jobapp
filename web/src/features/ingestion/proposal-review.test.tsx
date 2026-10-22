import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/server";

import {
  NEWER_RUN_NOTE,
  PAGE,
  ProposalList,
  REPLACE_NOTE,
  TWO_ENTRIES_NOTE,
  TYPED_NOTE,
} from "./proposal-list";

/**
 * The decisions of the review (task G13) against a fake api that keeps what was decided, so the
 * page is read again after each one as it is against the real one.
 */

const CV = { id: "doc-cv", name: "cv.txt", deleted: false };
const LETTER = { id: "doc-letter", name: "letter.txt", deleted: false };

function cite(document: typeof CV, excerpt: string) {
  return {
    document_id: document.id,
    document,
    excerpt,
    excerpt_cut: false,
    start: 0,
    end: excerpt.length,
    transcribed: false,
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
    before: null as unknown,
    after: value,
    noop: false,
    stale: false,
    stale_reason: null as string | null,
    locked: false,
    bulk: null as string | null,
    problem: null as string | null,
    flags: [] as string[],
    free_text: false,
    citations: [cite(CV, "Alice Martin")],
    conflict: [] as { value: unknown; citations: unknown[] }[],
    depends_on: [] as string[],
    base_revision: 1,
    applied_revision: null as number | null,
    ...more,
  };
}
type Item = ReturnType<typeof item>;

const EMPLOYER = {
  organisation: "Acme Group",
  title: "Engineer",
  start: "2021",
  end: null,
};

/** The api of the review, keeping its decisions. `calls` lists every write with its body. */
function fakeApi(items: Item[]) {
  const state = { items, revision: 3, overall: 10 };
  const calls: { path: string; body: unknown }[] = [];
  const count = (status: string) =>
    state.items.filter((one) => one.status === status).length;
  const counts = (of: Item[]) => ({
    total: of.length,
    pending: of.filter((one) => one.status === "pending").length,
    accepted: of.filter((one) => one.status === "accepted").length,
    rejected: of.filter((one) => one.status === "rejected").length,
    superseded: 0,
    invalid: of.filter((one) => one.status === "invalid").length,
    undone: 0,
  });
  const decide = (one: Item, status: string, value?: unknown) => {
    one.status = status;
    one.bulk = null;
    if (value !== undefined) one.value = value;
    if (status === "accepted") {
      state.revision += 1;
      state.overall += 5;
    }
  };
  const written = async (request: Request) => {
    const text = await request.text();
    const body: unknown = text === "" ? null : JSON.parse(text);
    calls.push({
      path: decodeURIComponent(new URL(request.url).pathname),
      body,
    });
    return body as { edited_value?: unknown; proposal_ids?: string[] } | null;
  };
  server.use(
    http.get("*/api/ingestion/runs/:id/proposals", () => {
      const sections = [...new Set(state.items.map((one) => one.section))];
      return HttpResponse.json({
        run_id: "run-1",
        status: "done",
        partial: false,
        profile_revision: state.revision,
        counts: counts(state.items),
        documents: [CV, LETTER],
        sections: sections.map((section) => {
          const of = state.items.filter((one) => one.section === section);
          return { section, counts: counts(of), items: of };
        }),
      });
    }),
    http.get("*/api/profile", () =>
      HttpResponse.json({ completeness: { overall: state.overall } }),
    ),
    http.post("*/api/proposals/:id/accept", async ({ request, params }) => {
      const body = await written(request);
      const one = state.items.find((each) => each.id === params.id);
      if (!one) return HttpResponse.json({ detail: "gone" }, { status: 404 });
      decide(one, "accepted", body?.edited_value);
      return HttpResponse.json({ id: one.id, status: "accepted" });
    }),
    http.post("*/api/proposals/:id/reject", async ({ request, params }) => {
      await written(request);
      const one = state.items.find((each) => each.id === params.id);
      if (one) decide(one, "rejected");
      return HttpResponse.json({ id: params.id, status: "rejected" });
    }),
    http.post("*/api/ingestion/runs/:id/:what", async ({ request, params }) => {
      const body = await written(request);
      const asked = state.items.filter((one) =>
        body?.proposal_ids?.includes(one.id),
      );
      const accept = params.what === "accept-section";
      const taken = asked.filter((one) => !accept || one.bulk === null);
      const skipped = asked
        .filter((one) => !taken.includes(one))
        .map((one) => ({ id: one.id, reason: String(one.bulk) }));
      for (const one of taken) one.status = accept ? "accepted" : "rejected";
      if (accept && taken.length > 0) {
        state.revision += 1;
        state.overall += 5 * taken.length;
      }
      return HttpResponse.json({
        run_id: "run-1",
        section: "identity",
        decided: taken.map((one) => one.id),
        skipped,
        left_out: {},
        revision: accept && taken.length > 0 ? state.revision : null,
        profile_revision: state.revision,
      });
    }),
  );
  return { state, calls, count };
}

function show() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProposalList runId="run-1" />
    </QueryClientProvider>,
  );
}

const proposal = (key: string) =>
  screen
    .getAllByTestId("proposal")
    .find((one) => one.dataset.key === key) as HTMLElement;

describe("the review of a run's proposals", () => {
  it("accepts one proposal, shows it kept and moves the completeness bar", async () => {
    const api = fakeApi([
      item("headline", "Engineer"),
      item("full_name", "Alice Martin", { locked: true, bulk: "locked" }),
    ]);
    const user = userEvent.setup();
    show();
    const bar = await screen.findByRole("progressbar", {
      name: "Profile completeness",
    });
    expect(bar).toHaveAttribute("aria-valuenow", "10");
    await user.click(
      within(proposal("headline")).getByRole("button", {
        name: "Accept: Headline",
      }),
    );
    expect(
      await within(proposal("headline")).findByText("Accepted"),
    ).toBeInTheDocument();
    // The revision the list was showing goes with the accept, and no value of the page's own.
    expect(api.calls).toEqual([
      {
        path: "/api/proposals/p-headline/accept",
        body: { profile_revision: 3 },
      },
    ]);
    await waitFor(() => {
      expect(bar).toHaveAttribute("aria-valuenow", "15");
    });
    expect(screen.getByText("Accepted: Headline.")).toHaveAttribute(
      "role",
      "status",
    );
    // Decided: its buttons are gone, the other proposal keeps its own.
    expect(
      within(proposal("headline")).queryByRole("button", {
        name: /Accept|Reject|Edit/,
      }),
    ).toBeNull();
    expect(
      within(proposal("full_name")).getByRole("button", {
        name: "Accept: Full name",
      }),
    ).toBeEnabled();
    expect(screen.getByTestId("review-left")).toHaveTextContent(
      "1 left to review, 1 accepted, 0 rejected.",
    );
  });

  it("edits a proposal with one control per field and shows the server's refusal at the field", async () => {
    const api = fakeApi([
      item("employers:acme-group-2021", EMPLOYER, {
        section: "experience",
        locked: true,
        bulk: "locked",
      }),
    ]);
    server.use(
      http.post(
        "*/api/proposals/:id/accept",
        () =>
          HttpResponse.json(
            {
              detail:
                "edited_value.start: Value error, a period is a year or a year and a month",
            },
            { status: 422 },
          ),
        { once: true },
      ),
    );
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    await user.click(
      within(row).getByRole("button", { name: "Edit: Employers: Acme Group" }),
    );
    const form = within(row).getByRole("form", {
      name: "Edit Employers: Acme Group",
    });
    expect(within(form).getByLabelText("Organisation")).toHaveFocus();
    const start = within(form).getByLabelText("Start");
    expect(start).toHaveValue("2021");
    expect(start).toHaveAccessibleDescription(/A year, or a year and a month/);
    await user.clear(start);
    await user.type(start, "March");
    await user.click(
      within(form).getByRole("button", { name: "Accept with my changes" }),
    );
    const refused = await within(form).findByRole("alert");
    expect(refused).toHaveTextContent(
      "a period is a year or a year and a month.",
    );
    expect(start).toBeInvalid();
    expect(start).toHaveAccessibleDescription(/a period is a year/);
    // The refused field takes the focus, and it was never disabled on the way.
    expect(start).toHaveFocus();
    expect(start).toBeEnabled();
    expect(api.count("pending")).toBe(1);
    // A year alone becomes a month, and the whole entry is sent as the user left it.
    await user.clear(start);
    await user.type(start, "2021-03");
    await user.click(
      within(form).getByRole("button", { name: "Accept with my changes" }),
    );
    expect(await within(row).findByText("Accepted")).toBeInTheDocument();
    expect(api.calls).toEqual([
      {
        path: "/api/proposals/p-employers:acme-group-2021/accept",
        body: {
          profile_revision: 3,
          edited_value: { ...EMPLOYER, start: "2021-03" },
        },
      },
    ]);
    expect(within(row).getByText("2021-03")).toBeInTheDocument();
  });

  it("rejects one proposal", async () => {
    const api = fakeApi([item("headline", "Engineer")]);
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    await user.click(
      within(row).getByRole("button", { name: "Reject: Headline" }),
    );
    expect(await within(row).findByText("Rejected")).toBeInTheDocument();
    expect(api.calls).toEqual([
      { path: "/api/proposals/p-headline/reject", body: null },
    ]);
    expect(screen.getByText("Every proposal is decided.")).toBeInTheDocument();
  });

  it("accepts a whole section in one call and says how many it left", async () => {
    const api = fakeApi([
      item("headline", "Engineer"),
      item("website", "https://example.org"),
      item("full_name", "Alice Martin", { locked: true, bulk: "locked" }),
      item("city", "Bordeaux", {
        flags: ["looks_like_instruction"],
        bulk: "flagged",
      }),
      item("links:x", { label: "x" }, { bulk: "invalid", problem: "No url." }),
    ]);
    const user = userEvent.setup();
    show();
    const identity = await screen.findByRole("region", { name: "Identity" });
    await user.click(
      within(identity).getByRole("button", {
        name: "Accept all in this section: Identity",
      }),
    );
    expect(
      await within(identity).findByText(
        // From the answer's `skipped`: the invalid one was never asked, so it is not counted.
        "Accepted 2. 2 left for you to accept one by one.",
      ),
    ).toHaveAttribute("role", "status");
    // One call for the section; what does not fit any more is not asked for.
    expect(api.calls).toEqual([
      {
        path: "/api/ingestion/runs/run-1/accept-section",
        body: {
          section: "identity",
          proposal_ids: ["p-headline", "p-website", "p-full_name", "p-city"],
        },
      },
    ]);
    await within(proposal("website")).findByText("Accepted");
    expect(
      within(proposal("full_name")).getByText("Accepted one by one"),
    ).toBeInTheDocument();
    expect(
      within(proposal("city")).getByText("Reads like an instruction"),
    ).toBeInTheDocument();
    // Nothing is left for a bulk accept: the section says so and keeps the bulk reject.
    expect(
      within(identity).queryByRole("button", { name: /Accept all/ }),
    ).toBeNull();
    expect(
      within(identity).getByText("What is left here is accepted one by one."),
    ).toBeInTheDocument();
  });

  it("rejects the rest of a section in one click, with no exclusion", async () => {
    const api = fakeApi([
      item("headline", "Engineer", { status: "accepted" }),
      item("full_name", "Alice Martin", { locked: true, bulk: "locked" }),
      item("links:x", { label: "x" }, { bulk: "invalid", problem: "No url." }),
    ]);
    const user = userEvent.setup();
    show();
    const identity = await screen.findByRole("region", { name: "Identity" });
    await user.click(
      within(identity).getByRole("button", {
        name: "Reject the rest of this section: Identity",
      }),
    );
    expect(
      await within(identity).findByText("Rejected 2."),
    ).toBeInTheDocument();
    expect(api.calls).toEqual([
      {
        path: "/api/ingestion/runs/run-1/reject-section",
        body: {
          section: "identity",
          proposal_ids: ["p-full_name", "p-links:x"],
        },
      },
    ]);
    await waitFor(() => {
      expect(
        within(identity).queryByRole("button", { name: /Reject the rest/ }),
      ).toBeNull();
    });
    expect(
      within(proposal("headline")).getByText("Accepted"),
    ).toBeInTheDocument();
  });

  it("names the proposal that stopped a bulk accept and applies nothing", async () => {
    const api = fakeApi([
      item("headline", "Engineer"),
      item("links:a", { label: "A", url: "https://a.example.org" }),
    ]);
    server.use(
      http.post("*/api/ingestion/runs/:id/accept-section", () =>
        HttpResponse.json(
          {
            detail:
              "Nothing was accepted. Your profile cannot take one of these as it is: too many links. Edit or reject that one, then accept the others again.",
            reason: "profile_conflict",
            proposal_id: "p-links:a",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    show();
    const identity = await screen.findByRole("region", { name: "Identity" });
    await user.click(
      within(identity).getByRole("button", { name: /Accept all/ }),
    );
    // The alert is on the proposal the server named, and the focus goes to it.
    expect(
      await within(proposal("links:a")).findByRole("alert"),
    ).toHaveTextContent("Your profile cannot take one of these as it is");
    expect(within(proposal("headline")).queryByRole("alert")).toBeNull();
    await waitFor(() => {
      expect(
        within(proposal("links:a")).getByRole("heading", { name: "Links" }),
      ).toHaveFocus();
    });
    expect(api.count("pending")).toBe(2);
  });

  it("keeps the single buttons disabled while a bulk call runs, then focuses the section", async () => {
    const api = fakeApi([item("headline", "Engineer")]);
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/ingestion/runs/:id/accept-section", async () => {
        await held;
        const one = api.state.items[0];
        if (one) one.status = "accepted";
        return HttpResponse.json({
          run_id: "run-1",
          section: "identity",
          decided: ["p-headline"],
          skipped: [],
          left_out: {},
          revision: 4,
          profile_revision: 4,
        });
      }),
    );
    const user = userEvent.setup();
    show();
    const identity = await screen.findByRole("region", { name: "Identity" });
    await user.click(
      within(identity).getByRole("button", { name: /Accept all/ }),
    );
    expect(
      within(proposal("headline")).getByRole("button", {
        name: "Accept: Headline",
      }),
    ).toBeDisabled();
    release();
    expect(
      await within(identity).findByText("Accepted 1."),
    ).toBeInTheDocument();
    // The bulk buttons went with what they decided: the focus is on the section's heading.
    await waitFor(() => {
      expect(
        within(identity).getByRole("heading", { name: "Identity" }),
      ).toHaveFocus();
    });
  });

  it("says when the rest of a bulk call was decided elsewhere", async () => {
    fakeApi([item("headline", "Engineer"), item("website", "https://a.org")]);
    server.use(
      http.post("*/api/ingestion/runs/:id/accept-section", () =>
        HttpResponse.json({
          run_id: "run-1",
          section: "identity",
          decided: ["p-headline"],
          skipped: [{ id: "p-website", reason: "not_pending" }],
          left_out: { not_pending: 1 },
          revision: 4,
          profile_revision: 4,
        }),
      ),
    );
    const user = userEvent.setup();
    show();
    const identity = await screen.findByRole("region", { name: "Identity" });
    await user.click(
      within(identity).getByRole("button", { name: /Accept all/ }),
    );
    expect(
      await within(identity).findByText(
        "Accepted 1. 1 was decided elsewhere meanwhile or no longer here.",
      ),
    ).toBeInTheDocument();
  });

  it("takes back an accepted proposal from the page", async () => {
    const api = fakeApi([
      item("headline", "Engineer", { status: "accepted", applied_revision: 3 }),
    ]);
    server.use(
      http.post("*/api/ingestion/proposals/:id/undo", ({ params }) => {
        const one = api.state.items.find((each) => each.id === params.id);
        if (one) one.status = "undone";
        return HttpResponse.json({ id: params.id, status: "undone" });
      }),
    );
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    await user.click(
      within(row).getByRole("button", { name: "Take back: Headline" }),
    );
    expect(await within(row).findByText("Taken back")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /Take back/ })).toBeNull();
  });

  it("shows the server's sentence when a take back is refused", async () => {
    fakeApi([
      item("headline", "Engineer", { status: "accepted", applied_revision: 3 }),
    ]);
    server.use(
      http.post("*/api/ingestion/proposals/:id/undo", () =>
        HttpResponse.json(
          {
            detail:
              "The profile changed here since this was accepted, so it was left as it is.",
            reason: "changed_since",
            status: "accepted",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    await user.click(within(row).getByRole("button", { name: /Take back/ }));
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "The profile changed here since this was accepted",
    );
  });

  it("says why accept all leaves a replace and a change of your own value", async () => {
    fakeApi([
      item("headline", "Lead engineer", {
        before: "Engineer",
        bulk: "typed_by_you",
      }),
      item("website", "https://b.org", { action: "replace", bulk: "replace" }),
    ]);
    show();
    await screen.findAllByTestId("proposal");
    expect(
      within(proposal("headline")).getByText("Your own value"),
    ).toBeInTheDocument();
    expect(
      within(proposal("headline")).getByText(TYPED_NOTE),
    ).toBeInTheDocument();
    expect(
      within(proposal("website")).getByText(REPLACE_NOTE),
    ).toBeInTheDocument();
    expect(
      screen.getByText("What is left here is accepted one by one."),
    ).toBeInTheDocument();
  });

  it("says that a newer run will replace what is undecided", async () => {
    const api = fakeApi([item("headline", "Engineer")]);
    show();
    await screen.findByTestId("proposal");
    expect(screen.queryByTestId("newer-run")).toBeNull();
    server.use(
      http.get("*/api/ingestion/runs/:id/proposals", () =>
        HttpResponse.json({
          run_id: "run-1",
          status: "done",
          partial: false,
          newer_run: true,
          profile_revision: api.state.revision,
          counts: {
            total: 0,
            pending: 0,
            accepted: 0,
            rejected: 0,
            superseded: 0,
            invalid: 0,
            undone: 0,
          },
          documents: [],
          sections: [],
        }),
      ),
    );
    cleanup();
    show();
    expect(await screen.findByTestId("newer-run")).toHaveTextContent(
      NEWER_RUN_NOTE,
    );
  });

  it("shows a conflict as a choice and accepts the value picked", async () => {
    const other = { ...EMPLOYER, start: "2021-05" };
    const api = fakeApi([
      item("employers:acme-group-2021", EMPLOYER, {
        section: "experience",
        flags: ["conflict"],
        locked: true,
        bulk: "locked",
        conflict: [{ value: other, citations: [cite(LETTER, "May 2021")] }],
      }),
    ]);
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    // Two positions started the same year share a key: the list says they may be two entries.
    expect(
      within(row).getByText(TWO_ENTRIES_NOTE.trim(), { exact: false }),
    ).toBeInTheDocument();
    const accept = within(row).getByRole("button", {
      name: "Accept: Employers: Acme Group",
    });
    // No value picked, no accept: the page never chooses between two documents.
    expect(accept).toBeDisabled();
    expect(
      within(row).getByText("Pick a value to accept it."),
    ).toBeInTheDocument();
    const values = within(row).getByRole("radiogroup", {
      name: "Values for Employers: Acme Group",
    });
    expect(within(values).getAllByRole("radio")).toHaveLength(2);
    expect(
      within(
        within(values).getByRole("list", { name: "Sources of value 2" }),
      ).getByText("letter.txt"),
    ).toBeInTheDocument();
    await user.click(within(values).getByRole("radio", { name: /^Value 2:/ }));
    await user.click(accept);
    expect(await within(row).findByText("Accepted")).toBeInTheDocument();
    expect(api.calls).toEqual([
      {
        path: "/api/proposals/p-employers:acme-group-2021/accept",
        body: { profile_revision: 3, edited_value: other },
      },
    ]);
  });

  it("restarts the open edit form from the value picked, and never sends the one that was not", async () => {
    const other = { ...EMPLOYER, start: "2021-05" };
    const api = fakeApi([
      item("employers:acme-group-2021", EMPLOYER, {
        section: "experience",
        flags: ["conflict"],
        locked: true,
        bulk: "locked",
        conflict: [{ value: other, citations: [cite(LETTER, "May 2021")] }],
      }),
    ]);
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    // The form opens on value 1 with nothing picked, and the user types into it.
    await user.click(
      within(row).getByRole("button", { name: "Edit: Employers: Acme Group" }),
    );
    const form = () =>
      within(row).getByRole("form", { name: "Edit Employers: Acme Group" });
    await user.clear(within(form()).getByLabelText("Organisation"));
    await user.type(within(form()).getByLabelText("Organisation"), "Typed Ltd");
    expect(within(form()).getByLabelText("Start")).toHaveValue("2021");
    // Picking value 2 restarts the form from value 2: what was typed is not carried over.
    await user.click(screen.getByRole("radio", { name: /^Value 2:/ }));
    expect(within(form()).getByLabelText("Start")).toHaveValue("2021-05");
    expect(within(form()).getByLabelText("Organisation")).toHaveValue(
      "Acme Group",
    );
    await user.click(
      within(form()).getByRole("button", { name: "Accept with my changes" }),
    );
    expect(await within(row).findByText("Accepted")).toBeInTheDocument();
    expect(api.calls).toEqual([
      {
        path: "/api/proposals/p-employers:acme-group-2021/accept",
        body: { profile_revision: 3, edited_value: other },
      },
    ]);
  });

  it("marks a stale item to check again and shows the server's sentence when it moved once more", async () => {
    fakeApi([
      item("years_experience", 2.2, {
        section: "skills",
        flags: ["derived"],
        bulk: "flagged",
        stale: true,
        stale_reason: "depends_on_changed",
      }),
    ]);
    server.use(
      http.post(
        "*/api/proposals/:id/accept",
        () =>
          HttpResponse.json(
            {
              detail:
                "The profile changed here since this was proposed. Compare the two values and accept again.",
              reason: "stale",
              profile_revision: 4,
            },
            { status: 409 },
          ),
        { once: true },
      ),
    );
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    expect(within(row).getByText("Check again")).toBeInTheDocument();
    expect(
      within(row).getByText(
        /calculated from a proposal you have since rejected/,
      ),
    ).toBeInTheDocument();
    const accept = within(row).getByRole("button", {
      name: "Accept: Years experience",
    });
    await user.click(accept);
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "Compare the two values and accept again.",
    );
    // Still there to accept, on the value shown.
    await user.click(accept);
    expect(await within(row).findByText("Accepted")).toBeInTheDocument();
    expect(within(row).queryByRole("alert")).toBeNull();
  });

  it("greys an invalid proposal with its reason and offers only to reject it", async () => {
    fakeApi([
      item(
        "links:x",
        { label: "x" },
        {
          bulk: "invalid",
          problem: "The address of a link is missing.",
        },
      ),
      item("headline", "Old", {
        status: "invalid",
        problem: "This value no longer fits the profile: too long.",
      }),
      item("city", "Bordeaux", {
        bulk: "profile_conflict",
        problem: "This list is full.",
      }),
    ]);
    show();
    await screen.findAllByTestId("proposal");
    const broken = proposal("links:x");
    expect(broken).toHaveClass("text-muted-foreground");
    expect(
      within(broken).getByText("The address of a link is missing."),
    ).toBeInTheDocument();
    expect(
      within(broken)
        .getAllByRole("button", { name: /Accept|Edit|Reject/ })
        .map((one) => one.getAttribute("aria-label")),
    ).toEqual(["Reject: Links"]);
    const set = proposal("headline");
    expect(set).toHaveClass("text-muted-foreground");
    expect(within(set).getByText("No longer fits")).toBeInTheDocument();
    expect(within(set).getByText(/too long/)).toBeInTheDocument();
    expect(
      within(set).queryAllByRole("button", { name: /Accept|Edit|Reject/ }),
    ).toHaveLength(0);
    // The profile cannot take it as it is: an edit or a reject, never the value as proposed.
    expect(
      within(proposal("city"))
        .getAllByRole("button", { name: /Accept|Edit|Reject/ })
        .map((one) => one.getAttribute("aria-label")),
    ).toEqual(["Edit: City", "Reject: City"]);
  });

  it("shows 300 proposals of one section a page at a time, and decides only what is shown", async () => {
    const many = Array.from({ length: 300 }, (_, index) =>
      item(
        `blocks:fact-${String(index).padStart(3, "0")}`,
        {
          title: `Fact ${String(index)}`,
          body: "A fact.",
        },
        { section: "facts" },
      ),
    );
    const api = fakeApi(many);
    const user = userEvent.setup();
    show();
    const facts = await screen.findByRole("region", { name: "Facts" });
    expect(screen.getAllByTestId("proposal")).toHaveLength(PAGE);
    expect(within(facts).getByText("Showing 50 of 300.")).toBeInTheDocument();
    await user.click(
      within(facts).getByRole("button", { name: "Show 50 more: Facts" }),
    );
    expect(screen.getAllByTestId("proposal")).toHaveLength(2 * PAGE);
    // Not all of it is shown: the button says how many it decides, and the outcome what is left.
    expect(
      within(facts).queryByRole("button", { name: /Reject the rest/ }),
    ).toBeNull();
    await user.click(
      within(facts).getByRole("button", {
        name: "Reject the 100 shown: Facts",
      }),
    );
    expect(
      await within(facts).findByText(
        "Rejected 100. 200 more in this section are not shown yet and still undecided. Show more brings them.",
      ),
    ).toBeInTheDocument();
    const body = api.calls[0]?.body as { proposal_ids: string[] };
    expect(body.proposal_ids).toHaveLength(2 * PAGE);
    expect(api.count("pending")).toBe(200);
  });

  it("says how many a bulk accept takes when the section holds more than is shown", async () => {
    const many = Array.from({ length: 120 }, (_, index) =>
      item(
        `blocks:fact-${String(index).padStart(3, "0")}`,
        { title: `Fact ${String(index)}`, body: "A fact." },
        { section: "facts" },
      ),
    );
    // Three of the fifty shown are accepted one by one, so the button takes 47.
    for (const one of many.slice(0, 3)) {
      one.bulk = "locked";
      one.locked = true;
    }
    const api = fakeApi(many);
    const user = userEvent.setup();
    show();
    const facts = await screen.findByRole("region", { name: "Facts" });
    expect(
      within(facts).queryByRole("button", { name: /Accept all/ }),
    ).toBeNull();
    const accept = within(facts).getByRole("button", {
      name: "Accept the 47 shown: Facts",
    });
    expect(accept).toHaveTextContent("Accept the 47 shown");
    await user.click(accept);
    expect(
      await within(facts).findByText(
        "Accepted 47. 3 left for you to accept one by one. 70 more in this section are not shown yet and still undecided. Show more brings them.",
      ),
    ).toBeInTheDocument();
    expect(api.count("accepted")).toBe(47);
    // Everything shown: the labels are the section's own again.
    await user.click(
      within(facts).getByRole("button", { name: "Show 50 more: Facts" }),
    );
    await user.click(
      within(facts).getByRole("button", { name: "Show 20 more: Facts" }),
    );
    expect(
      within(facts).getByRole("button", {
        name: "Accept all in this section: Facts",
      }),
    ).toBeInTheDocument();
    expect(
      within(facts).getByRole("button", {
        name: "Reject the rest of this section: Facts",
      }),
    ).toBeInTheDocument();
  });

  it("is usable with the keyboard alone and keeps the focus on the proposal decided", async () => {
    const api = fakeApi([item("headline", "Engineer")]);
    const user = userEvent.setup();
    show();
    const row = await screen.findByTestId("proposal");
    const edit = within(row).getByRole("button", { name: "Edit: Headline" });
    for (let tabs = 0; tabs < 20 && document.activeElement !== edit; tabs++)
      await user.tab();
    expect(edit).toHaveFocus();
    await user.keyboard("{Enter}");
    const field = within(row).getByLabelText("Headline");
    expect(field).toHaveFocus();
    await user.keyboard("{Control>}a{/Control}Lead engineer{Enter}");
    expect(await within(row).findByText("Accepted")).toBeInTheDocument();
    expect(api.calls[0]?.body).toEqual({
      profile_revision: 3,
      edited_value: "Lead engineer",
    });
    expect(
      within(row).getByRole("heading", { name: "Headline" }),
    ).toHaveFocus();
  });

  it("uses no dash as a separator in the wording of its controls", async () => {
    fakeApi([
      item("headline", "Engineer"),
      item("full_name", "Alice Martin", {
        locked: true,
        bulk: "locked",
        flags: ["conflict"],
        conflict: [{ value: "A. Martin", citations: [] }],
      }),
    ]);
    const user = userEvent.setup();
    const { container } = show();
    const identity = await screen.findByRole("region", { name: "Identity" });
    await user.click(
      within(proposal("headline")).getByRole("button", { name: /^Edit/ }),
    );
    await user.click(
      within(identity).getByRole("button", { name: /Reject the rest/ }),
    );
    await within(identity).findByText("Rejected 2.");
    expect(container.textContent).not.toMatch(/[—–]| - /);
  });
});
