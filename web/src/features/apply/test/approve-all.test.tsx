import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeAnswer, leftAnswer } from "@/test/apply";
import { fakeQueue, queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

import type { Approval } from "../api/apply-api";
import { readyForAll, readyResultLine } from "../helpers/queue-state";

const PATH = "/applications";
/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

beforeEach(() => {
  fakeSession(member);
});

const view = (overrides: Partial<Approval> = {}): Approval => ({
  state: "ready",
  dry_run: false,
  answers_hash: "hash-1",
  answers: [fakeAnswer("email", "Email", "e2e-alex@example.org")],
  documents: { cv: { version_id: "v", number: 3 } },
  screenshots: [],
  ...overrides,
});

describe("which forms Approve all ready names", () => {
  const ready = queueItem(1, "ready");
  it("takes a complete Review first form", () => {
    expect(readyForAll(ready, view(), 0)).toBe(true);
  });
  it.each([
    ["the form is not read yet", ready, undefined, 0],
    ["an answer is typed and not saved", ready, view(), 1],
    [
      "a required answer is left",
      ready,
      view({ answers: [leftAnswer("why", "Why?", { required: true })] }),
      0,
    ],
    [
      "the queue counts a required field",
      { ...ready, required_left: 1 },
      view(),
      0,
    ],
    ["its documents need a look", { ...ready, documents_review: true }, view()],
    ["no document is attached", ready, view({ documents: {} }), 0],
    ["it is an e-mail application", { ...ready, ats: "email" }, view(), 0],
    ["it is a rehearsal", { ...ready, rehearsal: true }, view(), 0],
    ["it is a dry run", ready, view({ dry_run: true }), 0],
    ["Full auto decides it", { ...ready, mode: "full_auto" }, view(), 0],
    [
      "the apply agent filled it (A20: Confirm on the job page)",
      { ...ready, review_agent: true },
      view(),
      0,
    ],
    ["it is under way", queueItem(1, "approved"), view(), 0],
    ["the form moved on since", ready, view({ state: "approved" }), 0],
  ])("leaves it out when %s", (_why, item, approval, unsaved = 0) => {
    expect(readyForAll(item, approval, unsaved)).toBe(false);
  });
  it("says each result in words", () => {
    const one = { company: "Example Robotics", title: "Data Engineer" };
    expect(
      readyResultLine(one, {
        application_id: "a",
        approved: true,
        state: "approved",
        code: null,
        detail: null,
      }),
    ).toBe("Example Robotics, Data Engineer: approved, it is being sent.");
    expect(
      readyResultLine(one, {
        application_id: "a",
        approved: false,
        state: "ready",
        code: "busy",
        detail: null,
      }),
    ).toBe(
      "Example Robotics, Data Engineer: left out. It was not approved: open it to see why.",
    );
  });
});

describe("Approve all ready on the queue page", () => {
  it("names every company and document, then approves exactly those", async () => {
    const items = [
      queueItem(1, "ready", { company: "Example Robotics" }),
      queueItem(2, "ready", { company: "Example Logistics" }),
      queueItem(3, "ready", { company: "Example Mail", ats: "email" }),
      queueItem(4, "queued"),
    ];
    const api = fakeQueue(items);
    const user = userEvent.setup();
    renderApp(PATH);

    const button = await screen.findByRole("button", {
      name: "Approve all ready (2)",
    });
    // The e-mail application keeps its own Approve, where its recipient is confirmed.
    expect(
      screen.getByText("2 forms complete. 1 more needs you first."),
    ).toBeInTheDocument();
    await user.click(button);
    const dialog = await screen.findByRole("alertdialog", {
      name: "Submit 2 applications?",
    });
    const list = within(dialog).getByRole("list", {
      name: "Applications to submit",
    });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Example Robotics, Robotics Engineer 1Sends: CV version 3 and Letter version 2",
      "Example Logistics, Robotics Engineer 2Sends: CV version 3 and Letter version 2",
    ]);
    expect(dialog).toHaveTextContent("It cannot be taken back.");
    expect(document.body.textContent).not.toMatch(SEPARATOR);

    await user.click(
      within(dialog).getByRole("button", { name: "Approve and submit all" }),
    );
    await waitFor(() => {
      expect(api.batches).toEqual([
        [
          { id: items[0]!.id, hash: api.hash(items[0]!.id) },
          { id: items[1]!.id, hash: api.hash(items[1]!.id) },
        ],
      ]);
    });
    // One click is the approval of exactly those: no single approve was sent.
    expect(api.approves).toEqual([]);
    await screen.findByText("2 forms approved.");
    expect(
      screen.getByText(
        "Example Robotics, Robotics Engineer 1: approved, it is being sent.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /Approve all ready/ }),
      ).toBeNull();
    });
  });

  it("leaves out a form that changed after the confirmation opened, and says so", async () => {
    const items = [queueItem(1, "ready"), queueItem(2, "ready")];
    const api = fakeQueue(items);
    const user = userEvent.setup();
    renderApp(PATH);
    await user.click(
      await screen.findByRole("button", { name: "Approve all ready (2)" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    // Another tab changes the second form: its hash is no longer the one shown.
    api.versions[items[1]!.id] = 2;
    await user.click(
      within(dialog).getByRole("button", { name: "Approve and submit all" }),
    );
    await screen.findByText("1 form approved, 1 left out.");
    expect(
      screen.getByText(
        /Example Robotics, Robotics Engineer 2: left out\. The form or your documents changed since you looked\./,
      ),
    ).toBeInTheDocument();
    expect(api.items.map((item) => item.state)).toEqual(["approved", "ready"]);
  });

  it("hides the button for a single complete form", async () => {
    fakeQueue([
      queueItem(1, "ready"),
      queueItem(2, "ready", { documents_review: true }),
    ]);
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await waitFor(() => {
      expect(
        within(card).getByRole("button", { name: "Approve and submit" }),
      ).toBeEnabled();
    });
    expect(
      screen.queryByRole("button", { name: /Approve all ready/ }),
    ).toBeNull();
  });

  it("sends nothing on Cancel", async () => {
    const two = fakeQueue([queueItem(1, "ready"), queueItem(2, "ready")]);
    const user = userEvent.setup();
    renderApp(PATH);
    await user.click(
      await screen.findByRole("button", { name: "Approve all ready (2)" }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(two.batches).toEqual([]);
    expect(two.items.map((item) => item.state)).toEqual(["ready", "ready"]);
  });

  it("waits for an answer typed and not saved in a card", async () => {
    const item = queueItem(1, "ready", { left: 1 });
    fakeQueue([item, queueItem(2, "ready"), queueItem(3, "ready")], {
      [item.id]: [leftAnswer("notice", "Notice period")],
    });
    const user = userEvent.setup();
    renderApp(PATH);
    await screen.findByRole("button", { name: "Approve all ready (3)" });
    const card = screen.getByRole("article", { name: /Robotics Engineer 1/ });
    await user.click(
      within(card).getByRole("button", { name: "Review the answers" }),
    );
    await user.type(
      within(card).getByRole("textbox", { name: "Answer for Notice period" }),
      "One month",
    );
    await screen.findByRole("button", { name: "Approve all ready (2)" });
  });
});

describe("Remember this answer for next forms", () => {
  it("is ticked by default and saves the answer to Saved answers once saved", async () => {
    const item = queueItem(1, "needs_person", { left: 1, required_left: 1 });
    const api = fakeQueue([item], {
      [item.id]: [
        leftAnswer("notice", "Notice period", {
          required: true,
          remember: "on",
        }),
      ],
    });
    const user = userEvent.setup();
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    const box = await within(card).findByRole("checkbox", {
      name: "Remember this answer for next forms",
    });
    expect(box).toBeChecked();
    await user.type(
      within(card).getByRole("textbox", { name: "Answer for Notice period" }),
      "One month",
    );
    await user.click(within(card).getByRole("button", { name: "Save answer" }));
    await waitFor(() => {
      expect(api.remembers).toEqual([
        { id: item.id, field_key: "notice", value: "One month" },
      ]);
    });
    // The answer is saved on the form first, then remembered: two separate calls.
    expect(api.puts).toEqual([
      { id: item.id, field: "notice", value: "One month" },
    ]);
    await within(card).findByText(/Saved for next forms/);
  });

  it("is unticked when the question names the company, and an unticked box saves nothing more", async () => {
    const item = queueItem(1, "needs_person", { left: 1, required_left: 1 });
    const api = fakeQueue([item], {
      [item.id]: [
        leftAnswer("before", "Have you worked at Example Robotics?", {
          kind: "yes_no",
          required: true,
          remember: "off",
        }),
      ],
    });
    const user = userEvent.setup();
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    const box = await within(card).findByRole("checkbox", {
      name: "Remember this answer for next forms",
    });
    expect(box).not.toBeChecked();
    await user.click(within(card).getByRole("radio", { name: "No" }));
    await waitFor(() => {
      expect(api.puts).toEqual([
        { id: item.id, field: "before", value: false },
      ]);
    });
    expect(api.remembers).toEqual([]);
  });

  it("is not offered for a question the app never fills from Saved answers", async () => {
    const item = queueItem(1, "needs_person", { left: 1, required_left: 1 });
    fakeQueue([item], {
      [item.id]: [
        leftAnswer("health", "Do you have a health condition?", {
          required: true,
          remember: null,
        }),
      ],
    });
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await within(card).findByRole("textbox", {
      name: "Answer for Do you have a health condition?",
    });
    expect(within(card).queryByRole("checkbox")).toBeNull();
  });

  it("says the answer stayed on this form when it could not be remembered", async () => {
    const item = queueItem(1, "needs_person", { left: 1, required_left: 1 });
    const api = fakeQueue([item], {
      [item.id]: [
        leftAnswer("notice", "Notice period", {
          required: true,
          remember: "on",
        }),
      ],
    });
    const user = userEvent.setup();
    renderApp(PATH);
    const card = await screen.findByRole("article", {
      name: /Robotics Engineer 1/,
    });
    await user.type(
      await within(card).findByRole("textbox", {
        name: "Answer for Notice period",
      }),
      "One month",
    );
    await waitFor(() => {
      expect(
        within(card).getByRole("button", { name: "Save answer" }),
      ).toBeEnabled();
    });
    api.next = {
      code: "not_answered",
      detail:
        "Save your answer to this question first, then the app can remember it.",
    };
    await user.click(within(card).getByRole("button", { name: "Save answer" }));
    await within(card).findByText(
      /Saved on this form only\. Save your answer to this question first/,
    );
  });
});
