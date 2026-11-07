import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobDetail, fakeJobsApi, makeDetail, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { JobListItem } from "../api/jobs-api";

beforeEach(() => {
  fakeSession(admin);
  // The detail pane beside the list shows a posting of makeJob.
  server.use(
    http.get("*/api/jobs/:id", ({ params }) => {
      const n = /^00000000-0000-4000-9000-(\d{12})$/.exec(String(params.id));
      return n
        ? HttpResponse.json(makeDetail(makeJob(Number(n[1]))))
        : undefined;
    }),
  );
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

const list = () => screen.getByRole("list", { name: "Postings" });
const rowBoxes = () =>
  within(list())
    .getAllByRole("checkbox")
    .filter((box) => / at /.test(box.getAttribute("aria-label") ?? ""));
const bar = () => screen.getByRole("region", { name: "Bulk actions" });

async function openJobs(rows: JobListItem[]) {
  const api = fakeJobsApi(rows);
  const app = renderApp("/jobs");
  await within(
    await screen.findByRole("list", { name: "Postings" }),
  ).findAllByRole("listitem");
  return { api, app };
}

function rows(n: number): JobListItem[] {
  return Array.from({ length: n }, (_, i) => makeJob(i + 1));
}

/** Fake POST /api/jobs/bulk and /bulk/undo over the list rows. */
function fakeBulk(
  state: { rows: JobListItem[] },
  options: { fail?: boolean; latencyMs?: number } = {},
) {
  const calls = { bulk: [] as unknown[], undo: [] as unknown[] };
  let previous: Record<string, string> = {};
  server.use(
    http.post("*/api/jobs/bulk", async ({ request }) => {
      const body = (await request.json()) as {
        ids: string[];
        set: { status?: string; notes_append?: string };
      };
      calls.bulk.push(body);
      if (options.latencyMs) await delay(options.latencyMs);
      if (options.fail)
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      previous = {};
      const results = body.ids.map((id) => {
        const row = state.rows.find((r) => r.id === id)!;
        const before = row.status;
        const changed = Boolean(body.set.status && body.set.status !== before);
        if (changed) {
          previous[id] = before;
          row.status = body.set.status!;
        }
        if (body.set.notes_append)
          row.notes = row.notes
            ? `${row.notes}\n${body.set.notes_append}`
            : body.set.notes_append;
        return {
          id,
          changed: changed || Boolean(body.set.notes_append),
          status: row.status,
          previous_status: before,
        };
      });
      const changed = results.filter((r) => r.changed).length;
      return HttpResponse.json({
        results,
        changed,
        unchanged: results.length - changed,
        undo_token: body.set.status ? "signed.token" : null,
        undo_expires_at: body.set.status ? "2026-10-09T09:10:00Z" : null,
      });
    }),
    http.post("*/api/jobs/bulk/undo", async ({ request }) => {
      calls.undo.push(await request.json());
      const restored = Object.keys(previous);
      for (const id of restored)
        state.rows.find((r) => r.id === id)!.status = previous[id]!;
      previous = {};
      if (!restored.length)
        return HttpResponse.json(
          {
            detail:
              "Nothing to undo: these jobs were already restored or changed since.",
          },
          { status: 409 },
        );
      return HttpResponse.json({ restored, skipped: [] });
    }),
  );
  return calls;
}

describe("multi-select", () => {
  it("selects one row, then a range with Shift, and clears", async () => {
    await openJobs(rows(20));
    const user = userEvent.setup();
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
    await user.click(rowBoxes()[1]!);
    expect(within(bar()).getByText("1 posting selected")).toBeVisible();
    // Some of the view is selected: the header box says so, half ticked.
    expect(
      screen.getByRole("checkbox", { name: "Clear the selection" }),
    ).toBePartiallyChecked();
    await user.keyboard("{Shift>}");
    await user.click(rowBoxes()[5]!);
    await user.keyboard("{/Shift}");
    expect(within(bar()).getByText("5 postings selected")).toBeVisible();
    for (const box of rowBoxes().slice(1, 6)) expect(box).toBeChecked();
    expect(rowBoxes()[0]).not.toBeChecked();
    // Shift again unselects a range from the last click.
    fireEvent.click(rowBoxes()[3]!, { shiftKey: true });
    expect(within(bar()).getByText("2 postings selected")).toBeVisible();
    // A click on a checkbox never opens the posting.
    expect(
      screen.getByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeVisible();
    await user.click(within(bar()).getByRole("button", { name: "Clear" }));
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
  });

  it("selects every posting of the view, up to 500, with a clear warning", async () => {
    await openJobs(rows(620));
    const user = userEvent.setup();
    const header = screen.getByRole("checkbox", {
      name: "Select every posting in this view",
    });
    await user.click(header);
    expect(
      await within(bar()).findByText("500 postings selected"),
    ).toBeVisible();
    expect(
      within(bar()).getByText(
        "Only the first 500 of 620 postings in this view are selected: a bulk action takes 500 at a time.",
      ),
    ).toBeVisible();
    const clear = screen.getByRole("checkbox", {
      name: "Clear the selection",
    });
    expect(clear).toBeChecked();
    await user.click(clear);
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
  });

  it("selects all of a small view without a warning, and a filter change clears it", async () => {
    await openJobs(rows(12));
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("checkbox", {
        name: "Select every posting in this view",
      }),
    );
    expect(
      await within(bar()).findByText("12 postings selected"),
    ).toBeVisible();
    expect(within(bar()).queryByRole("note")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^Shortlisted: / }));
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
    });
  });

  it("toggles a row with the space bar on the focused row", async () => {
    await openJobs(rows(5));
    const user = userEvent.setup();
    const first = within(list())
      .getAllByRole("listitem")
      .find((r) => r.getAttribute("data-index") === "0")!;
    first.focus();
    await user.keyboard(" ");
    expect(within(bar()).getByText("1 posting selected")).toBeVisible();
    expect(rowBoxes()[0]).toBeChecked();
    // X ticks it off again, as Space does.
    await user.keyboard("x");
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
    expect(rowBoxes()[0]).not.toBeChecked();
  });
});

describe("bulk actions", () => {
  it("sets a status at once, then offers Undo in the toast", async () => {
    const { api } = await openJobs(rows(10));
    const calls = fakeBulk(api, { latencyMs: 100 });
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    fireEvent.click(rowBoxes()[4]!, { shiftKey: true });
    await user.selectOptions(
      within(bar()).getByLabelText("New status"),
      "shortlisted",
    );
    await user.click(within(bar()).getByRole("button", { name: "Set status" }));
    // Optimistic: the rows show the new status before the api answers.
    const badges = () =>
      Array.from(list().querySelectorAll("[data-status]")).map((b) =>
        b.getAttribute("data-status"),
      );
    expect(badges().slice(0, 5)).toEqual(Array(5).fill("shortlisted"));
    const toast = await screen.findByText("5 postings set to Shortlisted.");
    expect(calls.bulk[0]).toEqual({
      ids: rows(5).map((r) => r.id),
      set: { status: "shortlisted" },
    });
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
    const toastEl = toast.closest("[data-sonner-toast]") as HTMLElement;
    await user.click(within(toastEl).getByRole("button", { name: "Undo" }));
    await screen.findByText(
      "Undone: 5 postings back to their previous status.",
    );
    expect(calls.undo).toEqual([{ token: "signed.token" }]);
    await waitFor(() => {
      expect(badges().slice(0, 5)).toEqual(Array(5).fill("found"));
    });
  });

  it("rolls the rows back when the bulk change fails", async () => {
    const { api } = await openJobs(rows(4));
    fakeBulk(api, { fail: true, latencyMs: 50 });
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    await user.selectOptions(
      within(bar()).getByLabelText("New status"),
      "skipped",
    );
    await user.click(within(bar()).getByRole("button", { name: "Set status" }));
    expect(list().querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "skipped",
    );
    await screen.findByText(
      /Nothing was changed\. Something went wrong on the server\. Try again in a moment\./,
    );
    expect(list().querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "found",
    );
    expect(within(bar()).getByText("1 posting selected")).toBeVisible();
  });

  it("asks before marking many as applied", async () => {
    const { api } = await openJobs(rows(3));
    const calls = fakeBulk(api);
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    await user.click(rowBoxes()[1]!);
    await user.selectOptions(
      within(bar()).getByLabelText("New status"),
      "applied",
    );
    await user.click(within(bar()).getByRole("button", { name: "Set status" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Mark 2 postings as applied?");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.bulk).toEqual([]);
  });

  it("applies to the selected rows after one confirmation, then clears the selection and the rows say Applying", async () => {
    const list = rows(3);
    await openJobs(list);
    const applied: string[] = [];
    const calls = { previews: [] as unknown[], starts: [] as unknown[] };
    server.use(
      http.get("*/api/applications", () =>
        HttpResponse.json({
          items: applied.map((postingId, n) => ({
            id: `00000000-0000-4000-a000-00000000000${String(n)}`,
            posting_id: postingId,
            mode: "direct",
            dry_run: false,
            state: "queued",
            ats: "ashby",
            form_url: "https://jobs.example.org/form",
            attempt: 0,
            needs: null,
            error_code: null,
            created_at: "2026-11-06T08:00:00Z",
            updated_at: "2026-11-06T08:00:00Z",
            submitted_at: null,
          })),
        }),
      ),
      http.post("*/api/apply/bulk/preview", async ({ request }) => {
        const body = (await request.json()) as { posting_ids: string[] };
        calls.previews.push(body);
        return HttpResponse.json({
          review_before_send: false,
          limit: 25,
          items: body.posting_ids.map((postingId) => {
            const row = list.find((one) => one.id === postingId)!;
            return {
              posting_id: postingId,
              company: row.company,
              title: row.title,
              documents: [],
              estimate: null,
              email: false,
              seen: "c".repeat(64),
            };
          }),
          left_out: [],
          beyond: [],
          estimate: null,
          cost_note: "",
        });
      }),
      http.post("*/api/apply/bulk", async ({ request }) => {
        const body = (await request.json()) as {
          items: { posting_id: string }[];
        };
        calls.starts.push(body);
        applied.push(...body.items.map((one) => one.posting_id));
        return HttpResponse.json({
          results: body.items.map((one) => ({
            posting_id: one.posting_id,
            started: true,
            mode: "direct",
          })),
          started: body.items.length,
          left_out: 0,
        });
      }),
    );
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    await user.click(rowBoxes()[2]!);
    await user.click(within(bar()).getByRole("button", { name: "Apply" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Apply to 2 postings?",
    });
    expect(calls.previews).toEqual([
      { posting_ids: [list[0]!.id, list[2]!.id] },
    ]);
    expect(dialog).toHaveTextContent(
      `${list[0]!.company}, ${list[0]!.title}CV and letter ready.`,
    );
    // One click for both: the confirmation's click is the approval.
    await user.click(
      within(dialog).getByRole("button", { name: "Apply to 2" }),
    );
    await within(dialog).findByRole("heading", {
      name: "Applying to 2 postings",
    });
    expect(calls.starts).toHaveLength(1);
    // The selection is cleared: the bar is gone, the summary stays until it is closed.
    expect(
      screen.queryByRole("region", { name: "Bulk actions", hidden: true }),
    ).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    for (const box of rowBoxes()) expect(box).not.toBeChecked();
    for (const row of [list[0]!, list[2]!]) {
      expect(
        await screen.findByRole("link", {
          name: `Applying: the application to ${row.title} at ${row.company}`,
        }),
      ).toBeVisible();
    }
    expect(
      screen.getByRole("button", {
        name: `Apply to ${list[1]!.title} at ${list[1]!.company}`,
      }),
    ).toBeInTheDocument();
  });

  it("adds a note to the selected rows", async () => {
    const { api } = await openJobs(rows(3));
    const calls = fakeBulk(api);
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    await user.click(rowBoxes()[2]!);
    await user.click(within(bar()).getByRole("button", { name: "Add note" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Add a note to 2 postings",
    });
    const add = within(dialog).getByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    await user.type(
      within(dialog).getByLabelText("Note"),
      "  Called back 9 Oct  ",
    );
    await user.click(add);
    await screen.findByText("Note added to 2 postings.");
    expect(calls.bulk[0]).toEqual({
      ids: [rows(3)[0]!.id, rows(3)[2]!.id],
      set: { notes_append: "Called back 9 Oct" },
    });
  });

  it("explains an Undo that comes too late", async () => {
    const { api } = await openJobs(rows(2));
    fakeBulk(api);
    server.use(
      http.post("*/api/jobs/bulk/undo", () =>
        HttpResponse.json(
          {
            detail:
              "This undo has expired: an undo is possible for 10 minutes.",
          },
          { status: 410 },
        ),
      ),
    );
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    await user.selectOptions(
      within(bar()).getByLabelText("New status"),
      "closed",
    );
    await user.click(within(bar()).getByRole("button", { name: "Set status" }));
    const toast = await screen.findByText("1 posting set to Closed.");
    await user.click(
      within(toast.closest("[data-sonner-toast]") as HTMLElement).getByRole(
        "button",
        { name: "Undo" },
      ),
    );
    expect(
      await screen.findByText(
        "This undo has expired: an undo is possible for 10 minutes.",
      ),
    ).toBeVisible();
  });
});

describe("add a posting", () => {
  it("adds, scores, and opens the new posting", async () => {
    await openJobs(rows(2));
    const created = makeDetail({
      id: "00000000-0000-4000-9000-000000000777",
      company: "Dust",
      title: "Agent Engineer",
      fit: 83,
    });
    fakeJobDetail(created);
    const sent: unknown[] = [];
    server.use(
      http.post("*/api/jobs", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(created, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a posting" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a posting" });
    const submit = within(dialog).getByRole("button", { name: "Add posting" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Company"), " Dust ");
    await user.type(within(dialog).getByLabelText("Title"), "Agent Engineer");
    await user.type(within(dialog).getByLabelText("Location"), "Paris");
    await user.type(
      within(dialog).getByLabelText("Link to the posting"),
      "https://dust.tt/jobs/1",
    );
    await user.type(
      within(dialog).getByLabelText("Posting text"),
      "Build agents.",
    );
    await user.click(submit);
    await screen.findByText("Posting added, fit 83.");
    expect(sent).toEqual([
      {
        company: "Dust",
        title: "Agent Engineer",
        location: "Paris",
        url: "https://dust.tt/jobs/1",
        posting_text: "Build agents.",
      },
    ]);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Agent Engineer" }),
    ).toBeVisible();
  });

  it("links to the existing posting on a duplicate", async () => {
    await openJobs(rows(2));
    server.use(
      http.post("*/api/jobs", () =>
        HttpResponse.json(
          {
            detail: "This posting is already in your list.",
            existing_id: "00000000-0000-4000-9000-000000000002",
            existing_slug: "job-2",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add a posting" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Company"), "Anthropic");
    await user.type(within(dialog).getByLabelText("Title"), "Engineer 2");
    // The contracts read as words ("Permanent"); the code is the value sent.
    const contract = within(dialog).getByLabelText("Contract type");
    expect(
      within(contract).getByRole("option", { name: "Permanent" }),
    ).toHaveValue("CDI");
    expect(contract).not.toHaveTextContent(/\b(CDI|CDD|Stage|Alternance)\b/);
    await user.selectOptions(contract, "CDI");
    await user.click(
      within(dialog).getByRole("button", { name: "Add posting" }),
    );
    expect(
      await within(dialog).findByText("This posting is already in your list."),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("link", { name: "Open the existing posting" }),
    ).toHaveAttribute("href", "/jobs/00000000-0000-4000-9000-000000000002");
  });
});

describe("rescore", () => {
  function fakeRescore(answers: Array<Record<string, unknown> | Response>) {
    const sent: unknown[] = [];
    server.use(
      http.post("*/api/jobs/rescore", async ({ request }) => {
        sent.push(await request.json());
        const next = answers.shift();
        return next instanceof Response ? next : HttpResponse.json(next);
      }),
    );
    return sent;
  }
  const part = (over: Record<string, unknown>) => ({
    scored: 0,
    changed: 0,
    unchanged: 0,
    skipped_manual: 0,
    skipped_no_text: 0,
    skipped_busy: 0,
    remaining: 0,
    next_after: null,
    ...over,
  });

  it("rescores all, resuming until done, and keeps manual fits", async () => {
    await openJobs(rows(3));
    const sent = fakeRescore([
      part({
        scored: 2000,
        changed: 10,
        unchanged: 1960,
        skipped_manual: 30,
        skipped_busy: 1,
        remaining: 433,
        next_after: "abc",
      }),
      part({
        scored: 400,
        changed: 2,
        unchanged: 388,
        skipped_manual: 10,
        skipped_no_text: 33,
        skipped_busy: 2,
      }),
    ]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(
      await screen.findByRole("menuitem", { name: "Rescore all postings" }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Rescore all postings?",
    });
    await user.click(within(dialog).getByRole("button", { name: "Rescore" }));
    await screen.findByText(
      "Rescored 2,400 postings: 12 changed, 2,348 unchanged, 40 fits set by hand kept, 33 postings without text skipped. 3 postings were busy and kept their score. Rescore again to include them.",
    );
    expect(sent).toEqual([
      { overwrite_manual: false },
      { overwrite_manual: false, after: "abc" },
    ]);
  });

  it("asks twice before replacing manual fits, and rescores the selection", async () => {
    await openJobs(rows(3));
    const sent = fakeRescore([part({ scored: 2, changed: 1, unchanged: 1 })]);
    const user = userEvent.setup();
    await user.click(rowBoxes()[0]!);
    await user.click(rowBoxes()[1]!);
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(
      await screen.findByRole("menuitem", {
        name: "Rescore the selected postings (2)",
      }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Rescore the 2 selected postings?",
    });
    await user.click(
      within(dialog).getByLabelText("Also replace the fits I set by hand"),
    );
    expect(dialog).toHaveTextContent(
      "Every fit you set by hand will be replaced by the computed one. This cannot be undone.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Rescore and replace" }),
    );
    await screen.findByText(/Rescored 2 postings/);
    expect(sent).toEqual([
      { ids: [rows(3)[0]!.id, rows(3)[1]!.id], overwrite_manual: true },
    ]);
  });

  it("the selected entry is disabled without a selection; no skills is explained", async () => {
    await openJobs(rows(3));
    fakeRescore([
      HttpResponse.json(
        {
          detail:
            "Your profile has no skills to score with yet. Add them in Profile, then rescore.",
        },
        { status: 409 },
      ),
    ]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(
      await screen.findByRole("menuitem", {
        name: "Rescore the selected postings",
      }),
    ).toHaveAttribute("data-disabled");
    await user.click(
      screen.getByRole("menuitem", { name: "Rescore all postings" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Rescore" }));
    expect(
      await within(dialog).findByText(
        "Your profile has no skills to score with yet. Add them in Profile, then rescore.",
      ),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("link", { name: "Open your profile" }),
    ).toHaveAttribute("href", "/profile");
  });
});
