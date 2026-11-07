import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useQuery } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getJob, jobKey } from "@/features/jobs/api/jobs-api";
import { APP_ID, fakeApplication } from "@/test/apply";
import { FREE, fakeScreens, fakeWindows, windowIn } from "@/test/browser";
import { makeDetail } from "@/test/jobs";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import {
  NeedsPerson,
  NeedsYouDot,
} from "@/components/shared/apply/needs-person";
import { LiveViewSlot } from "@/components/shared/apply/live-view-slot";
import type { ApplicationEvent } from "@/features/apply/api/apply-api";
import {
  PAYMENT_SENTENCE,
  needOf,
  retrySentence,
  waitOf,
} from "@/features/apply/helpers/needs-person-state";

const DASHES = /[–—]| - /;
const REASONS = [
  "captcha",
  "email_code",
  "required_left",
  "payment_requested",
  "signin",
  "form_changed",
  "no_adapter",
  "forbidden",
  "linkedin_paused",
  null,
];

afterEach(() => {
  vi.restoreAllMocks();
});

const LEASE_START = "2026-10-19T10:00:00Z";
const OF_APP = { application: APP_ID };
/** Another application of the person, filled in their browser beside this one. */
const OTHER_APP = "00000000-0000-4000-9000-0000000000a2";
const WAITING: ApplicationEvent = {
  at: "2026-10-19T10:01:00Z",
  kind: "waiting_person",
  data: { needs: "captcha" },
};

/** The person's browser held by an automation lease of `purpose`, for `application`. */
function leaseOf(
  purpose: string,
  application: string | null = APP_ID,
  signingIn: boolean | string = false,
) {
  server.use(
    http.get("*/api/browser", () =>
      HttpResponse.json({
        ...FREE,
        holder: "you",
        lease: {
          id: "7b2e3d4c-5f60-4b7c-8d9e-0f1a2b3c4d5e",
          purpose,
          automation: true,
          state: signingIn ? "waiting_person" : "active",
          wait_reason:
            typeof signingIn === "string"
              ? signingIn
              : signingIn
                ? "signin"
                : null,
          wait_host: signingIn ? "jobs.example.org" : null,
          wait_minutes_left: signingIn ? 30 : null,
          wait_deadline: null,
          started_at: LEASE_START,
          active_s: 30,
          max_s: 900,
          application_id: application,
        },
      }),
    ),
  );
}

function captchaPage(events?: ApplicationEvent[]) {
  renderWithQuery(
    <NeedsPerson
      application={fakeApplication("needs_person", { needs: "captcha" })}
      job={makeDetail()}
      events={events}
    />,
  );
}

describe("what the application needs", () => {
  it.each(REASONS)("says it for %s without a dash as a separator", (needs) => {
    const need = needOf(fakeApplication("needs_person", { needs }));
    expect(need.title).not.toMatch(DASHES);
    expect(need.what).not.toMatch(DASHES);
  });

  it("says LinkedIn is paused for seven days and nothing was sent", () => {
    const need = needOf(
      fakeApplication("needs_person", { needs: "linkedin_paused" }),
    );
    expect(need.title).toBe("LinkedIn is paused for seven days");
    expect(need.what).toContain("Nothing was sent");
  });

  it("names the site of a sign in wall", () => {
    const need = needOf(fakeApplication("needs_person", { needs: "signin" }));
    expect(need.title).toBe("Needs you: sign in to boards.example.org");
  });

  it("gives the spec's sentence for a payment", () => {
    const need = needOf(
      fakeApplication("needs_person", { needs: "payment_requested" }),
    );
    expect(need.what).toContain(PAYMENT_SENTENCE);
    expect(need.what).toContain("No card detail was typed");
  });

  it("reads the run's wait from the events", () => {
    expect(waitOf([])).toBeNull();
    expect(waitOf([WAITING])).toEqual({ since: WAITING.at, over: false });
    const over = {
      at: "2026-10-19T10:31:00Z",
      kind: "person_not_back",
      data: {},
    };
    expect(waitOf([WAITING, over])).toEqual({ since: WAITING.at, over: true });
    const again = { ...WAITING, at: "2026-10-19T10:40:00Z" };
    expect(waitOf([WAITING, over, again])).toEqual({
      since: again.at,
      over: false,
    });
  });

  it("says when a queued retry is due", () => {
    const retry = {
      at: "2026-10-19T10:00:00Z",
      kind: "retry",
      data: { attempt: 1, delay_s: 600, due: "2026-10-19T10:10:00Z" },
    };
    expect(retrySentence([retry])).toMatch(/tries again at \d/);
    expect(retrySentence([retry])).not.toMatch(DASHES);
    expect(retrySentence([])).toBe(
      "The last attempt did not finish. The assistant tries again by itself.",
    );
  });

  it("shows nothing outside needs_person and posting_closed", () => {
    renderWithQuery(
      <NeedsPerson application={fakeApplication("ready")} job={makeDetail()} />,
    );
    expect(screen.queryByTestId("needs-person")).toBeNull();
  });
});

describe("the job page", () => {
  it("has one person-controlled live view during a sign in handoff", async () => {
    const opened = fakeScreens();
    leaseOf("apply_fill", APP_ID, true);
    renderWithQuery(<LiveViewSlot idle="Filling" leaseOf={OF_APP} />);
    await screen.findByRole("region", { name: "Sign in to jobs.example.org" });
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(
      screen.queryByRole("button", { name: "Use the browser myself" }),
    ).toBeNull();
    expect(screen.getAllByTestId("live-screen")).toHaveLength(1);
  });

  it("opens the second window for the sign in of a fill alone in it", async () => {
    const { opened } = fakeWindows();
    server.use(
      http.get("*/api/browser", () => {
        const alone = {
          id: "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
          purpose: "apply_fill",
          automation: true,
          state: "waiting_person",
          wait_reason: "signin",
          wait_host: "jobs.example.org",
          wait_minutes_left: 30,
          wait_deadline: null,
          started_at: LEASE_START,
          active_s: 30,
          max_s: 900,
          application_id: APP_ID,
          slot: 1,
        };
        return HttpResponse.json({
          ...FREE,
          holder: "you",
          lease: alone,
          slots: [alone],
        });
      }),
    );
    renderWithQuery(<LiveViewSlot idle="Filling" leaseOf={OF_APP} />);
    const banner = await screen.findByRole("region", {
      name: "Sign in to jobs.example.org",
    });
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(windowIn(opened, banner)).toBe(1);
  });

  it("names the site of a sign in wall once, above the later sign in", () => {
    renderWithQuery(
      <NeedsPerson
        application={fakeApplication("needs_person", { needs: "signin" })}
        job={makeDetail()}
      />,
    );
    expect(
      screen.getAllByText(/sign in to boards\.example\.org/i),
    ).toHaveLength(1);
    expect(screen.getAllByText(/sign up yourself/)).toHaveLength(1);
    const banner = screen.getByRole("region", {
      name: "Sign in to boards.example.org",
    });
    expect(banner).toContainElement(
      screen.getByRole("button", { name: "Open the live view to sign in" }),
    );
  });

  it.each(["captcha", "email_code", "forbidden"])(
    "gives the person live input directly during the %s wait on the page",
    async (reason) => {
      const opened = fakeScreens();
      leaseOf("apply_fill", APP_ID, reason);
      renderWithQuery(<LiveViewSlot idle="Waiting" leaseOf={OF_APP} />);
      await waitFor(() => {
        expect(opened).toHaveLength(1);
      });
      expect(opened[0]?.screen.viewOnly).toBe(false);
      expect(
        screen.getByRole("button", { name: "Keyboard" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Use the browser myself" }),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Let the assistant continue" }),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "I'm signed in" }),
      ).toBeNull();
    },
  );

  it.each(["required_left", "payment_requested", "unrecognized"])(
    "keeps the %s wait view only, with Take over",
    async (reason) => {
      const opened = fakeScreens();
      leaseOf("apply_fill", APP_ID, reason);
      renderWithQuery(<LiveViewSlot idle="Waiting" leaseOf={OF_APP} />);
      await waitFor(() => {
        expect(opened).toHaveLength(1);
      });
      expect(opened[0]?.screen.viewOnly).toBe(true);
      expect(screen.queryByRole("button", { name: "Keyboard" })).toBeNull();
      expect(
        screen.getByRole("button", { name: "Use the browser myself" }),
      ).toBeInTheDocument();
    },
  );

  it("gives the input back to the assistant's view when the same lease ends its wait", async () => {
    const opened = fakeScreens();
    leaseOf("apply_fill");
    const { client } = renderWithQuery(
      <LiveViewSlot idle="Waiting" leaseOf={OF_APP} />,
    );
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(opened[0]?.screen.viewOnly).toBe(true);
    leaseOf("apply_fill", APP_ID, "captcha");
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["browser"] });
    });
    await waitFor(() => {
      expect(opened).toHaveLength(2);
    });
    expect(opened[0]?.screen.disconnected).toBe(true);
    expect(opened[1]?.screen.viewOnly).toBe(false);
    leaseOf("apply_fill");
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["browser"] });
    });
    await waitFor(() => {
      expect(opened).toHaveLength(3);
    });
    expect(opened[1]?.screen.disconnected).toBe(true);
    expect(opened[2]?.screen.viewOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Keyboard" })).toBeNull();
  });

  it("says a captcha needs the person and opens the live view of the assistant's lease", async () => {
    const opened = fakeScreens();
    leaseOf("apply_fill");
    captchaPage([WAITING]);
    expect(screen.getByText("A captcha needs you")).toBeInTheDocument();
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(
      screen.getByRole("button", { name: "Use the browser myself" }),
    ).toBeInTheDocument();
  });

  it.each([
    ["a sweep", "sweep", null],
    ["the fill of another posting", "apply_fill", OTHER_APP],
  ])("opens no live view of %s", async (_, purpose, application) => {
    let asked = 0;
    leaseOf(purpose, application);
    server.use(
      http.get("*/api/browser", () => {
        asked += 1;
        return undefined;
      }),
    );
    captchaPage([WAITING]);
    await waitFor(() => {
      expect(asked).toBeGreaterThan(0);
    });
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(
      "The assistant waits for you.",
    );
    expect(
      screen.queryByRole("button", { name: "Use the browser myself" }),
    ).toBeNull();
  });

  it("opens this application's window when two fills run, and gives the person its input", async () => {
    const { opened } = fakeWindows();
    const fill = (id: string, application: string, slot: number) => ({
      id,
      purpose: "apply_fill",
      automation: true,
      state: "active",
      wait_reason: null,
      wait_host: null,
      wait_deadline: null,
      started_at: LEASE_START,
      active_s: 30,
      max_s: 900,
      application_id: application,
      slot,
    });
    const other = fill("1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", OTHER_APP, 0);
    const mine = {
      ...fill("2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e", APP_ID, 1),
      state: "waiting_person",
      wait_reason: "captcha",
    };
    server.use(
      http.get("*/api/browser", () =>
        HttpResponse.json({
          ...FREE,
          holder: "you",
          lease: other,
          slots: [other, mine],
        }),
      ),
    );
    captchaPage([WAITING]);
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(windowIn(opened, screen.getByTestId("live-view-slot"))).toBe(1);
    expect(opened[0]?.screen.viewOnly).toBe(false);
    expect(
      screen.getByRole("button", { name: "Keyboard" }),
    ).toBeInTheDocument();
  });

  it("says the assistant stopped waiting once the wait ended", () => {
    leaseOf("apply_fill");
    const over = {
      at: "2026-10-19T10:31:00Z",
      kind: "person_not_back",
      data: {},
    };
    captchaPage([WAITING, over]);
    expect(screen.getByTestId("live-view-slot")).toHaveTextContent(
      "The assistant stopped waiting",
    );
    expect(
      screen.queryByRole("button", { name: "Use the browser myself" }),
    ).toBeNull();
  });

  it("keeps the words while nobody holds the browser for the person", async () => {
    renderWithQuery(
      <NeedsPerson
        application={fakeApplication("needs_person", { needs: "email_code" })}
        job={makeDetail()}
      />,
    );
    expect(await screen.findByTestId("live-view-slot")).toHaveTextContent(
      "The assistant waits for you.",
    );
  });

  it("opens no live view for a question left", () => {
    renderWithQuery(
      <NeedsPerson
        application={fakeApplication("needs_person", {
          needs: "required_left",
        })}
        job={makeDetail()}
      />,
    );
    expect(screen.getByText("A question needs you")).toBeInTheDocument();
    expect(screen.queryByTestId("live-view-slot")).toBeNull();
  });

  it("proposes to move a closed posting to Closed", async () => {
    const job = makeDetail({ status: "staged" });
    const sent: unknown[] = [];
    server.use(
      http.patch("*/api/jobs/:id", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ ...job, status: "closed" });
      }),
    );
    renderWithQuery(
      <NeedsPerson application={fakeApplication("posting_closed")} job={job} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Mark it closed" }),
    );
    await waitFor(() => {
      expect(sent).toEqual([{ status: "closed", updated_at: job.updated_at }]);
    });
  });

  it("reads the posting again after a stale answer, so a second click carries its new time", async () => {
    const first = makeDetail({ status: "staged" });
    const changed = { ...first, updated_at: "2026-10-19T12:00:00Z" };
    let current = first;
    let reads = 0;
    const sent: { updated_at: string }[] = [];
    server.use(
      http.get("*/api/jobs/:id", () => {
        reads += 1;
        return HttpResponse.json(current);
      }),
      http.patch("*/api/jobs/:id", async ({ request }) => {
        const body = (await request.json()) as { updated_at: string };
        sent.push(body);
        if (body.updated_at !== current.updated_at) {
          return HttpResponse.json(
            { detail: "Changed elsewhere.", updated_at: current.updated_at },
            { status: 409 },
          );
        }
        return HttpResponse.json({ ...current, status: "closed" });
      }),
    );
    function Page() {
      const job = useQuery({
        queryKey: jobKey(first.id),
        queryFn: ({ signal }) => getJob(first.id, signal),
      });
      return job.data ? (
        <NeedsPerson
          application={fakeApplication("posting_closed")}
          job={job.data}
        />
      ) : null;
    }
    renderWithQuery(<Page />);
    const button = await screen.findByRole("button", {
      name: "Mark it closed",
    });
    current = changed;
    await userEvent.click(button);
    await screen.findByText(/Changed elsewhere\./);
    await waitFor(() => {
      expect(reads).toBe(2);
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Mark it closed" }),
    );
    await waitFor(() => {
      expect(sent.map((body) => body.updated_at)).toEqual([
        first.updated_at,
        changed.updated_at,
      ]);
    });
  });

  it("proposes nothing when the posting is already closed", () => {
    renderWithQuery(
      <NeedsPerson
        application={fakeApplication("posting_closed")}
        job={makeDetail({ status: "closed" })}
      />,
    );
    expect(screen.queryByRole("button", { name: "Mark it closed" })).toBeNull();
  });
});

describe("the app shell's dot", () => {
  it("shows while one application needs the person", async () => {
    server.use(
      http.get("*/api/apply/queue/count", () =>
        HttpResponse.json({ waiting_on_you: 2, needs_you: 1 }),
      ),
    );
    renderWithQuery(<NeedsYouDot />);
    expect(await screen.findByTestId("needs-you-dot")).toBeInTheDocument();
    expect(screen.getByText(", 1 needs you")).toBeInTheDocument();
  });

  it("shows nothing when none needs the person", async () => {
    let asked = 0;
    server.use(
      http.get("*/api/apply/queue/count", () => {
        asked += 1;
        return HttpResponse.json({ waiting_on_you: 3, needs_you: 0 });
      }),
    );
    renderWithQuery(<NeedsYouDot />);
    await waitFor(() => {
      expect(asked).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId("needs-you-dot")).toBeNull();
  });
});
