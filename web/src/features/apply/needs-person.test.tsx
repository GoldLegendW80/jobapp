import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeApplication } from "@/test/apply";
import { FREE, fakeScreens } from "@/test/browser";
import { makeDetail } from "@/test/jobs";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { NeedsPerson, NeedsYouDot } from "./needs-person";
import { PAYMENT_SENTENCE, needOf } from "./needs-person-state";

const DASHES = /[–—]| - /;
const REASONS = [
  "captcha",
  "email_code",
  "required_left",
  "payment_requested",
  "signin",
  "form_changed",
  "no_adapter",
  null,
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("what the application needs", () => {
  it.each(REASONS)("says it for %s without a dash as a separator", (needs) => {
    const need = needOf(fakeApplication("needs_person", { needs }));
    expect(need.title).not.toMatch(DASHES);
    expect(need.what).not.toMatch(DASHES);
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
  });

  it("shows nothing outside needs_person and posting_closed", () => {
    renderWithQuery(
      <NeedsPerson application={fakeApplication("ready")} job={makeDetail()} />,
    );
    expect(screen.queryByTestId("needs-person")).toBeNull();
  });
});

describe("the job page", () => {
  it("says a captcha needs the person and opens the live view of the assistant's lease", async () => {
    const opened = fakeScreens();
    server.use(
      http.get("*/api/browser", () =>
        HttpResponse.json({
          ...FREE,
          holder: "you",
          lease: {
            id: "7b2e3d4c-5f60-4b7c-8d9e-0f1a2b3c4d5e",
            purpose: "apply_fill",
            automation: true,
            state: "active",
            wait_reason: null,
            wait_site: null,
            wait_deadline: null,
            started_at: "2026-10-19T10:00:00Z",
            active_s: 30,
            max_s: 900,
          },
        }),
      ),
    );
    renderWithQuery(
      <NeedsPerson
        application={fakeApplication("needs_person", { needs: "captcha" })}
        job={makeDetail()}
      />,
    );
    expect(screen.getByText("A captcha needs you")).toBeInTheDocument();
    await waitFor(() => {
      expect(opened).toHaveLength(1);
    });
    expect(
      screen.getByRole("button", { name: "Take over" }),
    ).toBeInTheDocument();
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
