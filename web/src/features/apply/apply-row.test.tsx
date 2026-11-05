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
  it("applies in one click with the posting page's call, then links to the application", async () => {
    const api = fakeApplications();
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
    await waitFor(() => {
      expect(api.starts).toEqual([{ id: JOB, body: { mode: "apply" } }]);
    });
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

  it("says the api's refusal and starts nothing else", async () => {
    fakeApplications();
    server.use(
      http.post("*/api/postings/:id/applications", () =>
        HttpResponse.json(
          {
            detail: "You already applied to this role at this company.",
            code: "same_role",
            application_id: null,
          },
          { status: 409 },
        ),
      ),
    );
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
    expect(
      await screen.findByText(
        "You already applied to this role at this company.",
      ),
    ).toBeVisible();
  });
});
