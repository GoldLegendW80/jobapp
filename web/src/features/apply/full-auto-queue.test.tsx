import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeAnswer } from "@/test/apply";
import { fakeQueue, queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

const PATH = "/applications";
/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;
/** A server clock far from the page's: the countdown must read this one. */
const SERVER_NOW = "2001-02-03T10:00:00Z";
const TEN_MINUTES_LATER = "2001-02-03T10:10:00Z";

beforeEach(() => {
  fakeSession(member);
});

describe("Full auto in the review queue", () => {
  it("asks a rehearsal's question; Yes keeps it in the queue, ready for one click Approve", async () => {
    const item = queueItem(1, "rehearsed", {
      mode: "full_auto",
      dry_run: true,
      rehearsal: true,
      shot_id: "shot-1",
    });
    const api = fakeQueue([item], {
      [item.id]: [fakeAnswer("email", "Email", "e2e-alex@example.org")],
    });
    api.mode = "full_auto";
    const user = userEvent.setup();
    renderApp(PATH);
    const waiting = await screen.findByRole("region", {
      name: "Waiting for you (1)",
    });
    const card = within(waiting).getByRole("article", {
      name: /Robotics Engineer 1/,
    });
    expect(card).toHaveTextContent(
      "A rehearsal ran: the form was filled and checked. Nothing was sent.",
    );
    within(card).getByRole("img", { name: "The form before the click" });
    const question = within(card).getByRole("region", {
      name: "Does this look right?",
    });
    expect(
      within(card).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
    // The answers can be read, not changed: nothing of a rehearsal is edited.
    await user.click(
      within(card).getByRole("button", { name: "Review the answers" }),
    );
    await within(card).findByText("e2e-alex@example.org");
    expect(within(card).queryByRole("button", { name: /^Edit/ })).toBeNull();

    await user.click(
      within(question).getByRole("button", { name: "Yes, it looks right" }),
    );
    await screen.findByText(
      "Thank you. It waits in your queue, ready for your approval.",
    );
    expect(api.rehearsals).toEqual([{ id: item.id, answer: "yes" }]);
    const ready = await screen.findByRole("button", {
      name: "Approve and submit",
    });
    expect(
      screen.queryByRole("region", { name: "Does this look right?" }),
    ).toBeNull();
    await waitFor(() => {
      expect(ready).toBeEnabled();
    });
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("No takes the rehearsal out of the queue and sends nothing", async () => {
    const item = queueItem(1, "rehearsed", {
      mode: "full_auto",
      dry_run: true,
      rehearsal: true,
    });
    const api = fakeQueue([item]);
    const user = userEvent.setup();
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(
      within(card).getByRole("button", { name: "No, something is wrong" }),
    );
    await screen.findByText(
      "Noted. Nothing was sent, and your next Like is a rehearsal again.",
    );
    expect(api.rehearsals).toEqual([{ id: item.id, answer: "no" }]);
    expect(api.approves).toEqual([]);
    await screen.findByText(/Your queue is empty/);
  });

  it("shows the api's sentence when a Yes is refused", async () => {
    const item = queueItem(1, "rehearsed", {
      mode: "full_auto",
      rehearsal: true,
    });
    const api = fakeQueue([item]);
    api.next = {
      detail:
        "Another application to this posting or this form started since the rehearsal.",
      code: "rehearsal_taken",
    };
    const user = userEvent.setup();
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(
      within(card).getByRole("button", { name: "Yes, it looks right" }),
    );
    expect(
      await within(card).findByText(
        "Another application to this posting or this form started since the rehearsal.",
      ),
    ).toBeVisible();
  });

  it("counts a hold down on the server's clock, and Cancel stops it", async () => {
    const item = queueItem(1, "held", {
      mode: "full_auto",
      hold_until: TEN_MINUTES_LATER,
    });
    const api = fakeQueue([item]);
    api.now = SERVER_NOW;
    api.mode = "full_auto";
    const user = userEvent.setup();
    renderApp(PATH);
    const underWay = await screen.findByRole("region", {
      name: "Under way (1)",
    });
    const card = within(underWay).getByRole("article", {
      name: /Robotics Engineer 1/,
    });
    const timer = within(card).getByRole("timer");
    // The page's own clock is years after the server's: read on it, the hold would be over.
    expect(timer.textContent).toMatch(/^(10:00|9:5\d)$/);
    expect(timer).toHaveAccessibleName("about 10 minutes");
    expect(card).toHaveTextContent("unless you cancel");
    const cancels = within(card).getAllByRole("button", {
      name: "Cancel application",
    });
    expect(cancels).toHaveLength(1);

    await user.click(cancels[0]!);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("The hold stops and nothing is sent.");
    await user.click(
      within(dialog).getByRole("button", { name: "Cancel the application" }),
    );
    await screen.findByText("Cancelled during the hold. Nothing was sent.");
    expect(api.cancels).toEqual([item.id]);
    await screen.findByText(/Your queue is empty/);
  });

  it("shows the server's word when a Cancel comes too late", async () => {
    const item = queueItem(1, "held", {
      mode: "full_auto",
      hold_until: SERVER_NOW,
    });
    const api = fakeQueue([item]);
    api.now = SERVER_NOW;
    api.next = {
      detail: "This application can no longer be cancelled.",
      code: "not_cancellable",
    };
    const user = userEvent.setup();
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    expect(card).toHaveTextContent(
      "The hold has ended: the app checks everything once more, then submits.",
    );
    await user.click(
      within(card).getByRole("button", { name: "Cancel application" }),
    );
    await user.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: "Cancel the application",
      }),
    );
    expect(
      await within(card).findByText(
        "This application can no longer be cancelled.",
      ),
    ).toBeVisible();
  });

  it("shows a held item turned into a review item with its reason", async () => {
    fakeQueue([
      queueItem(1, "ready", {
        reason: {
          code: "full_auto_off",
          sentence:
            "Full auto was turned off after this Like, so it waits for you.",
        },
      }),
    ]);
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    expect(card).toHaveTextContent(
      "Why it came back: Full auto was turned off after this Like, so it waits for you.",
    );
    within(card).getByRole("button", { name: "Approve and submit" });
    expect(within(card).queryByRole("timer")).toBeNull();
  });

  it("sums up Full auto's day: submitted, held, sent to review and why", async () => {
    const api = fakeQueue([]);
    api.mode = "full_auto";
    api.summary = {
      since: "2026-10-14T00:00:00Z",
      submitted: 2,
      held: 1,
      sent_to_review: [
        {
          code: "below_min_fit",
          sentence:
            "This posting's fit is below your threshold for Full auto, so it waits for you.",
          count: 2,
        },
        { code: "safety_ceiling", sentence: null, count: 1 },
        { code: "signin", sentence: null, count: 1 },
        { code: "captcha", sentence: null, count: 1 },
        { code: "other", sentence: null, count: 1 },
        { code: "browser_gone", sentence: null, count: 1 },
      ],
    };
    renderApp(PATH);
    const today = await screen.findByRole("region", {
      name: "Full auto today",
    });
    expect(today).toHaveTextContent(
      "2 submitted, 1 held, 7 sent to you for review.",
    );
    const why = within(today).getByRole("list", {
      name: "Why they were sent to you",
    });
    expect(why).toHaveTextContent(
      "This posting's fit is below your threshold for Full auto, so it waits for you. (2)",
    );
    const lines = within(why)
      .getAllByRole("listitem")
      .map((one) => one.textContent);
    expect(lines.slice(1)).toEqual([
      "The app stopped before the click, so it waits for you. (1)",
      "The site asked you to sign in, so it waits for you. (1)",
      "The site asked for a captcha, so it waits for you. (1)",
      "Another reason: open it to see where it stopped. (1)",
      "The app stopped before the click, so it waits for you. (1)",
    ]);
    expect(why).not.toHaveTextContent(/Signin|Captcha\.|Other\.|_/);
    expect(
      screen.getByText(/In Full auto your Like is your approval/),
    ).toBeVisible();
    expect(
      screen.queryByText(/Nothing is sent until you approve it/),
    ).toBeNull();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("shows no summary in Review first when nothing happened today", async () => {
    fakeQueue([]);
    renderApp(PATH);
    await screen.findByText(/Your queue is empty/);
    expect(
      screen.queryByRole("region", { name: "Full auto today" }),
    ).toBeNull();
    await screen.findByText(/Nothing is sent until you approve it/);
  });

  it("follows a Full auto item ready for the app's decision as under way, with nothing to approve", async () => {
    fakeQueue([queueItem(1, "ready", { mode: "full_auto" })]);
    renderApp(PATH);
    const underWay = await screen.findByRole("region", {
      name: "Under way (1)",
    });
    const card = within(underWay).getByRole("article", {
      name: /Robotics Engineer 1/,
    });
    expect(card).toHaveTextContent(
      "Full auto: filled. The app checks it now, then holds it so you can cancel.",
    );
    expect(
      within(card).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
    expect(within(card).queryByRole("button", { name: "Reject" })).toBeNull();
    within(card).getByRole("button", { name: "Cancel application" });
  });
});
