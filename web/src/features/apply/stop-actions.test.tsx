import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeApplication } from "@/test/apply";
import { fakeQueue, queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { makeDetail } from "@/test/jobs";
import { renderApp, renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { statusLine } from "./apply-state";
import { NeedsPerson } from "./needs-person";
import { needOf } from "./needs-person-state";

/**
 * No dead ends (owner, 2026-11-01: "When I'm here, how can I continue applying ??"): every stop
 * the app cannot get past alone says what happened in one plain sentence and offers "Apply on
 * the site myself", "I applied" and, where a new fill can help, "Try again", on the job page and
 * on the review queue's card.
 */

const DASHES = /[–—]| - /;
const GOES_ON = /When you are done, the assistant goes on/;
const FORM =
  "https://www.arbeitnow.com/jobs/companies/example/solution-engineer-1";

/** Each stop, and whether "Try again" can help it. */
const STOPS: [string, boolean][] = [
  ["submit_not_designated", true],
  ["no_adapter", true],
  ["forbidden", true],
  ["board_hops", false],
  ["board_link_missing", false],
];

beforeEach(() => {
  fakeSession(member);
});

function stoppedApplication(needs: string) {
  return fakeApplication("needs_person", {
    mode: "direct",
    ats: "other",
    needs,
    form_url: FORM,
  });
}

describe("the job page on a stop", () => {
  it.each(STOPS)(
    "says what happened for %s and offers a way forward",
    (needs, retry) => {
      const application = stoppedApplication(needs);
      renderWithQuery(
        <NeedsPerson application={application} job={makeDetail()} />,
      );
      const need = needOf(application);
      expect(screen.getByText(need.title)).toBeInTheDocument();
      expect(screen.getByText(need.what)).toBeInTheDocument();
      expect(need.what).toMatch(/nothing was sent/i);
      expect(`${need.title} ${need.what}`).not.toMatch(DASHES);
      expect(statusLine(application)).not.toMatch(GOES_ON);
      expect(statusLine(application)).not.toMatch(DASHES);
      const site = screen.getByRole("link", {
        name: /Apply on the site myself/,
      });
      expect(site).toHaveAttribute("href", FORM);
      expect(site).toHaveAttribute("target", "_blank");
      expect(site).toHaveAttribute("rel", "noopener noreferrer");
      expect(
        screen.getByRole("button", { name: "I applied" }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Try again" }) !== null).toBe(
        retry,
      );
    },
  );

  it("still says the assistant goes on after a step the person clears", () => {
    const application = fakeApplication("needs_person", {
      mode: "direct",
      needs: "captcha",
    });
    expect(statusLine(application)).toMatch(GOES_ON);
  });

  it("opens the posting when the form address is not a web address", () => {
    const application = stoppedApplication("no_adapter");
    renderWithQuery(
      <NeedsPerson
        application={{ ...application, form_url: "mailto:jobs@example.org" }}
        job={makeDetail({ url: "https://jobs.example.org/robotics/1" })}
      />,
    );
    expect(
      screen.getByRole("link", { name: /Apply on the site myself/ }),
    ).toHaveAttribute("href", "https://jobs.example.org/robotics/1");
  });

  it("records I applied and Try again through the api", async () => {
    const calls: string[] = [];
    server.use(
      http.post("*/api/applications/:id/:action", ({ params }) => {
        calls.push(String(params.action));
        return HttpResponse.json(
          fakeApplication(params.action === "applied" ? "submitted" : "queued"),
        );
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(
      <NeedsPerson
        application={stoppedApplication("submit_not_designated")}
        job={makeDetail()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(calls).toEqual(["retry"]);
    });
    await user.click(screen.getByRole("button", { name: "I applied" }));
    await waitFor(() => {
      expect(calls).toEqual(["retry", "applied"]);
    });
  });

  it("shows the api's refusal of I applied", async () => {
    server.use(
      http.post("*/api/applications/:id/applied", () =>
        HttpResponse.json(
          {
            detail: "This application is already recorded as sent.",
            code: "already_recorded",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(
      <NeedsPerson
        application={stoppedApplication("no_adapter")}
        job={makeDetail()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "I applied" }));
    expect(
      await screen.findByText(/already recorded as sent/),
    ).toBeInTheDocument();
  });
});

describe("the review queue's card on a stop", () => {
  it.each(STOPS)("offers a way forward for %s", async (needs, retry) => {
    const item = queueItem(1, "needs_person", {
      mode: "direct",
      ats: "other",
      needs,
      form_url: FORM,
    });
    fakeQueue([item]);
    renderApp("/applications");
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    const line = statusLine(item) ?? "";
    expect(within(card).getByText(line)).toBeInTheDocument();
    expect(line).not.toMatch(GOES_ON);
    expect(line).not.toMatch(DASHES);
    expect(
      within(card).getByRole("link", { name: /Apply on the site myself/ }),
    ).toHaveAttribute("href", FORM);
    expect(
      within(card).getByRole("button", { name: "I applied" }),
    ).toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: "Try again" }) !== null,
    ).toBe(retry);
  });

  it("I applied takes the card out of the queue", async () => {
    const item = queueItem(1, "needs_person", {
      mode: "direct",
      ats: "other",
      needs: "submit_not_designated",
      form_url: FORM,
    });
    const queue = fakeQueue([item]);
    let applied = 0;
    server.use(
      http.post("*/api/applications/:id/applied", () => {
        applied += 1;
        queue.items = [];
        return HttpResponse.json(fakeApplication("submitted"));
      }),
    );
    const user = userEvent.setup();
    renderApp("/applications");
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(within(card).getByRole("button", { name: "I applied" }));
    await waitFor(() => {
      expect(applied).toBe(1);
    });
    await waitFor(() => {
      expect(
        screen.queryByRole("article", { name: /Robotics Engineer 1/ }),
      ).not.toBeInTheDocument();
    });
  });
});
