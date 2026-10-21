import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeAnswer, leftAnswer } from "@/test/apply";
import { fakeQueue, queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const PATH = "/applications";
/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const card = (title: string) =>
  screen.getByRole("article", { name: new RegExp(title) });
const section = (name: RegExp) => screen.getByRole("region", { name });

beforeEach(() => {
  fakeSession(member);
});

describe("the review queue page", () => {
  it("lists what waits for the person first, then what is under way", async () => {
    fakeQueue([
      queueItem(1, "ready", {
        title: "Robotics Engineer",
        company: "Example Robotics",
        shot_id: "shot-1",
        left: 2,
        required_left: 0,
        documents: {
          cv: { number: 3, fit: "one_page", corrections: 0 },
          letter: { number: 2, fit: "overflow", corrections: 1 },
        },
        documents_review: true,
        reason: {
          code: "documents_changed",
          sentence:
            "A newer version of your documents was written after this form was filled, so nothing was sent.",
        },
      }),
      queueItem(2, "queued", {
        title: "Field Engineer",
        needs: "documents:consent",
        documents: {},
      }),
    ]);
    renderApp(PATH);

    const waiting = await screen.findByRole("region", {
      name: "Waiting for you (1)",
    });
    const ready = await within(waiting).findByRole("article", {
      name: /Robotics Engineer/,
    });
    expect(
      within(ready).getByRole("link", { name: "Robotics Engineer" }),
    ).toHaveAttribute("href", `/jobs/${queueItem(1, "ready").posting_id}`);
    expect(ready).toHaveTextContent("Example Robotics");
    expect(ready).toHaveTextContent("waiting for your approval");
    expect(ready).toHaveTextContent("Review these documents before approval.");
    expect(ready).toHaveTextContent(
      "Why it came back: A newer version of your documents was written",
    );
    const documents = within(ready).getByRole("region", { name: "Documents" });
    expect(
      within(documents).getByRole("link", {
        name: "CV version 3: fits on one page",
      }),
    ).toHaveAttribute(
      "href",
      `/jobs/${queueItem(1, "ready").posting_id}/documents/cv`,
    );
    within(documents).getByRole("link", {
      name: "Letter version 2: runs over one page, 1 correction to look at",
    });
    expect(ready).toHaveTextContent("2 fields left for you, all optional.");
    within(ready).getByRole("img", { name: "The form before the click" });

    const underWay = section(/Under way \(1\)/);
    const queued = within(underWay).getByRole("article", {
      name: /Field Engineer/,
    });
    expect(queued).toHaveTextContent(
      "Waiting for your CV and letter to be written.",
    );
    expect(queued).toHaveTextContent(
      "The documents show here once the form is filled.",
    );
    within(queued).getByRole("button", { name: "Cancel application" });
    expect(
      within(queued).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
    expect(document.title).toMatch(/^Application queue/);
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("edits an answer, then approves the form as it is now", async () => {
    const item = queueItem(1, "ready", { left: 1, required_left: 1 });
    const api = fakeQueue([item], {
      [item.id]: [
        fakeAnswer("email", "Email", "e2e-alex@example.org"),
        leftAnswer("why", "Why Example Robotics?", { required: true }),
      ],
    });
    const user = userEvent.setup();
    renderApp(PATH);
    const ready = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });

    // A required field is left: the answers are open and Approve waits for it.
    const toggle = within(ready).getByRole("button", {
      name: "Hide the answers",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const approve = within(ready).getByRole("button", {
      name: "Approve and submit",
    });
    await within(ready).findByText(/Answer Why Example Robotics\? first/);
    expect(approve).toBeDisabled();

    await user.type(
      within(ready).getByRole("textbox", {
        name: "Answer for Why Example Robotics?",
      }),
      "I build agents.",
    );
    expect(approve).toBeDisabled();
    await within(ready).findByText(/Save your answers first/);
    await user.click(
      within(ready).getByRole("button", { name: "Save answer" }),
    );
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    expect(api.puts).toEqual([
      { id: item.id, field: "why", value: "I build agents." },
    ]);
    // The queue now reads nothing required left: the answers stay open under the person's hands.
    await waitFor(() => {
      expect(within(ready).queryByText(/field left for you/)).toBeNull();
    });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(ready).getByText("I build agents.")).toBeVisible();

    await user.click(approve);
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(
      "Submit this application to Example Robotics for Robotics Engineer 1?",
    );
    expect(dialog).toHaveTextContent("CV version 3");
    expect(dialog).toHaveTextContent("Letter version 2");
    await user.click(
      within(dialog).getByRole("button", { name: "Approve and submit" }),
    );

    // The hash of the form after the answer, never the first one.
    await waitFor(() => {
      expect(api.approves).toEqual([
        { id: item.id, hash: `hash-${item.id}-2` },
      ]);
    });
    const underWay = await screen.findByRole("region", {
      name: "Under way (1)",
    });
    await within(underWay).findByText(/Approved\. The form is filled again/);
  });

  it.each([
    [409, "stale", "This answer changed on another page."],
    [422, "wrong_type", "That answer cannot be saved."],
  ] as const)(
    "keeps an unsaved answer and explains a %s refusal",
    async (status, code, detail) => {
      const item = queueItem(1, "ready", { left: 1, required_left: 1 });
      fakeQueue([item], {
        [item.id]: [
          leftAnswer("why", "Why Example Robotics?", { required: true }),
        ],
      });
      server.use(
        http.put("*/api/applications/:id/answers/:field", () =>
          HttpResponse.json({ detail, code }, { status }),
        ),
      );
      const user = userEvent.setup();
      renderApp(PATH);
      const ready = await screen.findByRole("article", {
        name: /Robotics Engineer 1/,
      });
      const answer = within(ready).getByRole("textbox", {
        name: "Answer for Why Example Robotics?",
      });
      await user.type(answer, "I build agents.");
      await user.click(
        within(ready).getByRole("button", { name: "Save answer" }),
      );
      await within(ready).findByText(detail);
      expect(answer).toHaveValue("I build agents.");
    },
  );

  it("moves focus to the next queue card after approval", async () => {
    const first = queueItem(1, "ready");
    const second = queueItem(2, "ready");
    fakeQueue([first, second], {
      [first.id]: [fakeAnswer("email", "Email", "e2e-alex@example.org")],
      [second.id]: [fakeAnswer("email", "Email", "e2e-alex@example.org")],
    });
    const user = userEvent.setup();
    renderApp(PATH);
    const firstCard = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(
      within(firstCard).getByRole("button", { name: "Approve and submit" }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Approve and submit",
      }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Robotics Engineer 2" }),
      ).toHaveFocus();
    });
  });

  it("shows the form as it is now when the approval is stale, and sends the new hash", async () => {
    const item = queueItem(1, "ready");
    const api = fakeQueue([item], {
      [item.id]: [fakeAnswer("email", "Email", "e2e-alex@example.org")],
    });
    api.stale = [fakeAnswer("email", "Email", "e2e-other@example.org")];
    const user = userEvent.setup();
    renderApp(PATH);
    const ready = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    const approve = within(ready).getByRole("button", {
      name: "Approve and submit",
    });
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    await user.click(approve);
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Approve and submit",
      }),
    );
    await within(ready).findByText(/changed since you looked/);
    await within(ready).findByText("e2e-other@example.org");
    expect(
      within(ready).getByRole("button", { name: "Hide the answers" }),
    ).toHaveAttribute("aria-expanded", "true");

    await user.click(approve);
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Approve and submit",
      }),
    );
    await waitFor(() => {
      expect(api.approves.map((one) => one.hash)).toEqual([
        `hash-${item.id}-1`,
        `hash-${item.id}-2`,
      ]);
    });
  });

  it("rejects after a confirmation, and skips the posting only when asked", async () => {
    const api = fakeQueue([
      queueItem(1, "ready"),
      queueItem(2, "needs_person"),
    ]);
    const user = userEvent.setup();
    renderApp(PATH);
    const first = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });

    // "Not yet" sends nothing.
    await user.click(within(first).getByRole("button", { name: "Reject" }));
    let dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(
      "Reject the application to Example Robotics for Robotics Engineer 1?",
    );
    await user.click(within(dialog).getByRole("button", { name: "Not yet" }));
    expect(api.rejects).toEqual([]);

    await user.click(within(first).getByRole("button", { name: "Reject" }));
    dialog = await screen.findByRole("alertdialog");
    const skip = within(dialog).getByRole("checkbox", {
      name: /Skip this posting too/,
    });
    expect(skip).not.toBeChecked();
    await user.click(skip);
    await user.click(within(dialog).getByRole("button", { name: "Reject" }));
    await screen.findByText(
      "Rejected Robotics Engineer 1 at Example Robotics, and the posting is skipped. Nothing was sent.",
    );
    await waitFor(() => {
      expect(
        screen.queryByRole("article", { name: /Robotics Engineer 1/ }),
      ).toBeNull();
    });
    expect(
      screen.getByRole("heading", { name: "Application queue" }),
    ).toHaveFocus();

    // The next one opens with the box clear again: skipping is never carried over.
    const second = card("Robotics Engineer 2");
    expect(
      within(second).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
    within(second).getByRole("link", { name: "Open the posting" });
    await user.click(within(second).getByRole("button", { name: "Reject" }));
    dialog = await screen.findByRole("alertdialog");
    expect(
      within(dialog).getByRole("checkbox", { name: /Skip this posting too/ }),
    ).not.toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Reject" }));
    await screen.findByText(
      "Rejected Robotics Engineer 2 at Example Robotics. Nothing was sent.",
    );
    expect(api.rejects).toEqual([
      { id: queueItem(1, "ready").id, skip_posting: true },
      { id: queueItem(2, "ready").id, skip_posting: false },
    ]);
    await screen.findByText(/Your queue is empty/);
  });

  it("shows the api's sentence when a decision is refused", async () => {
    const api = fakeQueue([queueItem(1, "ready")]);
    api.next = {
      detail: "This application can no longer be stopped or rejected.",
      code: "not_cancellable",
    };
    const user = userEvent.setup();
    renderApp(PATH);
    const ready = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(within(ready).getByRole("button", { name: "Reject" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Reject",
      }),
    );
    await within(ready).findByText(
      "This application can no longer be stopped or rejected.",
    );
  });

  it("cancels an item under way after a confirmation", async () => {
    const item = queueItem(1, "filling");
    const api = fakeQueue([item]);
    const user = userEvent.setup();
    renderApp(PATH);
    const filling = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(
      within(filling).getByRole("button", { name: "Cancel application" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Cancel the application" }),
    );
    await screen.findByText(/Your queue is empty/);
    expect(api.cancels).toEqual([item.id]);
  });

  it("offers a retry when the queue cannot be loaded", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/apply/queue", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json({ items: [], waiting_on_you: 0 });
      }),
    );
    const user = userEvent.setup();
    renderApp(PATH);
    await screen.findByText(/The queue could not be loaded/);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText(/Your queue is empty/);
    expect(
      within(screen.getByRole("main")).getByRole("link", { name: "Jobs" }),
    ).toHaveAttribute("href", "/jobs");
  });
});

describe("the app shell's Applications link", () => {
  it("shows how many wait for the person, and follows the queue page", async () => {
    const api = fakeQueue([
      queueItem(1, "ready"),
      queueItem(2, "submit_unknown"),
      queueItem(3, "filling"),
    ]);
    const user = userEvent.setup();
    renderApp("/runs");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const link = await within(nav).findByRole("link", {
      name: /^Applications\s*,\s*2 waiting for you$/,
    });
    expect(link).toHaveAttribute("href", PATH);

    await user.click(link);
    const first = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.click(within(first).getByRole("button", { name: "Reject" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Reject",
      }),
    );
    await within(nav).findByRole("link", {
      name: /^Applications\s*,\s*1 waiting for you$/,
    });
    expect(api.rejects).toHaveLength(1);
  });

  it("shows no count when nothing waits", async () => {
    renderApp("/runs");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(
      await within(nav).findByRole("link", { name: "Applications" }),
    ).toHaveAttribute("href", PATH);
  });
});
