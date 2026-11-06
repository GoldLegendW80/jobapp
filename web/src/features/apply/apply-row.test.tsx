import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApplication } from "@/test/apply";
import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { Application } from "./apply-api";

const WAIT = { timeout: 4000 };
const JOB = "00000000-0000-4000-9000-000000000001";

beforeEach(() => {
  fakeSession(admin);
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  fireEvent.scroll(window);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

afterEach(() => {
  toast.dismiss();
});

/** The person's applications as GET /api/applications lists them, and every start asked. */
function fakeApplications(initial: Application[] = []) {
  const state = { items: [...initial], starts: [] as unknown[] };
  server.use(
    http.get("*/api/applications", () =>
      HttpResponse.json({ items: state.items }),
    ),
    http.post(
      "*/api/postings/:id/applications",
      async ({ request, params }) => {
        state.starts.push({ id: params.id, body: await request.json() });
        const started = fakeApplication("queued", {
          posting_id: String(params.id),
          mode: "direct",
          needs: "documents:writing",
        });
        state.items = [started, ...state.items];
        return HttpResponse.json({ ...started, warnings: [] }, { status: 201 });
      },
    ),
  );
  return state;
}

/** The confirmation's two routes (bulk-apply-api.ts): the read of the posting, then its start,
 * which also lists the started application among the person's applications. */
function fakeConfirm(
  state: { items: Application[] },
  leftOut: { code: string; detail: string } | null = null,
) {
  const calls = { previews: [] as unknown[], starts: [] as unknown[] };
  server.use(
    http.post("*/api/apply/bulk/preview", async ({ request }) => {
      calls.previews.push(await request.json());
      const posting = {
        posting_id: JOB,
        company: "Example Robotics",
        title: "Robotics Engineer",
      };
      return HttpResponse.json({
        review_before_send: false,
        limit: 25,
        items: leftOut
          ? []
          : [
              {
                ...posting,
                documents: [],
                estimate: null,
                email: false,
                seen: "a".repeat(64),
              },
            ],
        left_out: leftOut ? [{ ...posting, ...leftOut }] : [],
        beyond: [],
        estimate: null,
        cost_note: "",
      });
    }),
    http.post("*/api/apply/bulk", async ({ request }) => {
      calls.starts.push(await request.json());
      const started = fakeApplication("queued", {
        posting_id: JOB,
        mode: "direct",
        needs: "documents:writing",
      });
      state.items = [started, ...state.items];
      return HttpResponse.json({
        results: [
          {
            posting_id: JOB,
            started: true,
            application_id: started.id,
            mode: "direct",
          },
        ],
        started: 1,
        left_out: 0,
      });
    }),
  );
  return calls;
}

function rowOf(title: string) {
  const table = screen.getByRole("table", { name: "Postings" });
  return within(table)
    .getAllByRole("row")
    .find((row) => row.textContent.includes(title)) as HTMLElement;
}

async function openJobs(rows = [makeJob(1, { title: "Robotics Engineer" })]) {
  fakeJobsApi(rows);
  const user = userEvent.setup();
  renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  return user;
}

describe("Apply on a row of the Jobs list", () => {
  it("asks for a confirmation that names the posting, starts it on Apply to 1, then links to it", async () => {
    const api = fakeApplications();
    const confirm = fakeConfirm(api);
    const user = await openJobs();
    const apply = await within(rowOf("Robotics Engineer")).findByRole(
      "button",
      { name: /^Apply to Robotics Engineer at / },
      WAIT,
    );
    await waitFor(() => {
      expect(apply).toBeEnabled();
    });
    await user.click(apply);
    // Owner, 2026-11-06: "yes confirmation". Nothing starts before it.
    const dialog = await screen.findByRole("alertdialog", {
      name: "Apply to 1 posting?",
    });
    expect(confirm.previews).toEqual([{ posting_ids: [JOB] }]);
    expect(dialog).toHaveTextContent("Example Robotics, Robotics Engineer");
    expect(confirm.starts).toEqual([]);
    expect(api.starts).toEqual([]);
    await user.click(
      within(dialog).getByRole("button", { name: "Apply to 1" }),
    );
    await waitFor(() => {
      expect(confirm.starts).toHaveLength(1);
    });
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Close",
      }),
    );
    // Still on the list: the row now says it is under way and links to it.
    const link = await within(rowOf("Robotics Engineer")).findByRole(
      "link",
      { name: /^Applying/ },
      WAIT,
    );
    expect(link).toHaveAttribute("href", `/jobs/${JOB}`);
    expect(screen.getByRole("table", { name: "Postings" })).toBeVisible();
    expect(
      within(rowOf("Robotics Engineer")).getByRole("link", {
        name: "Robotics Engineer",
      }),
    ).toHaveAttribute("href", `/jobs/${JOB}`);
  });

  it("is not offered while an application is under way, and is again once it ended", async () => {
    fakeApplications([
      fakeApplication("ready", { posting_id: JOB }),
      fakeApplication("failed", {
        id: "00000000-0000-4000-9000-0000000000a2",
        posting_id: "00000000-0000-4000-9000-000000000002",
      }),
    ]);
    await openJobs([
      makeJob(1, { title: "Robotics Engineer" }),
      makeJob(2, { title: "Field Engineer" }),
    ]);
    expect(
      await within(rowOf("Robotics Engineer")).findByRole(
        "link",
        { name: /^Waiting for you/ },
        WAIT,
      ),
    ).toBeVisible();
    expect(
      within(rowOf("Robotics Engineer")).queryByRole("button", {
        name: /^Apply/,
      }),
    ).toBeNull();
    expect(
      await within(rowOf("Field Engineer")).findByRole("button", {
        name: /^Apply to Field Engineer/,
      }),
    ).toBeVisible();
  });

  it("waits with the posting page's reason in a tooltip while the settings cannot be read", async () => {
    fakeApplications();
    server.use(
      http.get("*/api/apply/settings", () =>
        HttpResponse.json({ detail: "Down." }, { status: 503 }),
      ),
    );
    const user = await openJobs();
    const apply = await within(rowOf("Robotics Engineer")).findByRole(
      "button",
      { name: /^Apply to / },
      WAIT,
    );
    await waitFor(
      () => {
        expect(screen.queryAllByText("Reading your apply settings")).toEqual(
          [],
        );
        expect(apply).toBeDisabled();
      },
      { timeout: 8000 },
    );
    await user.hover(apply.parentElement as HTMLElement);
    expect(
      (
        await screen.findAllByText(
          "Your apply settings could not be read, so Apply waits.",
        )
      )[0],
    ).toBeInTheDocument();
  });

  it("is not offered on LinkedIn while LinkedIn is not allowed, as on the posting page", async () => {
    fakeApplications();
    server.use(
      http.get("*/api/browser/linkedin", () =>
        HttpResponse.json({
          enabled: false,
          accepted: false,
          allowed: false,
          text: "Fixture consent",
          text_version: 1,
          accepted_at: null,
          accepted_version: null,
          query: null,
          location: null,
          paused_until: null,
          last_sweep_at: null,
          needs_signin: false,
        }),
      ),
    );
    await openJobs([
      makeJob(1, {
        title: "Robotics Engineer",
        url: "https://www.linkedin.com/jobs/view/123456/",
      }),
    ]);
    await screen.findByRole("link", { name: "Robotics Engineer" });
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(
      within(rowOf("Robotics Engineer")).queryByRole("button", {
        name: /^Apply/,
      }),
    ).toBeNull();
  });

  it("says why it would not apply, and starts nothing", async () => {
    const api = fakeApplications();
    const confirm = fakeConfirm(api, {
      code: "same_role",
      detail: "You already applied to this role at this company.",
    });
    const user = await openJobs();
    const apply = await within(rowOf("Robotics Engineer")).findByRole(
      "button",
      { name: /^Apply to / },
      WAIT,
    );
    await waitFor(() => {
      expect(apply).toBeEnabled();
    });
    await user.click(apply);
    const dialog = await screen.findByRole("alertdialog");
    expect(
      await within(dialog).findByText(
        "You already applied to this role at this company.",
      ),
    ).toBeVisible();
    expect(
      within(dialog).queryByRole("button", { name: /^Apply to \d/ }),
    ).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(confirm.starts).toEqual([]);
    expect(api.starts).toEqual([]);
    // Still on the list: the dialog's clicks never opened the posting.
    expect(screen.getByRole("table", { name: "Postings" })).toBeVisible();
  });
});
