import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "@/test/server";
import { fakeSession, member } from "@/test/auth";
import {
  fakeAnswer,
  fakeApplication,
  fakeApply,
  leftAnswer,
} from "@/test/apply";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";

import { approvalKey } from "./apply-api";
import { APPLY_POLL_MS } from "./apply-state";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const PATH = `/jobs/${JOB_ID}`;

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

beforeEach(() => {
  fakeSession(member);
  fakeProfile();
  fakeJobDetail(
    makeDetail({
      id: JOB_ID,
      company: "Example Robotics",
      title: "Robotics Engineer",
    }),
  );
});

async function openSection() {
  const user = userEvent.setup();
  const app = renderApp(PATH);
  const heading = await screen.findByRole("heading", { name: "Apply" });
  const section = heading.closest("section") as HTMLElement;
  return { user, section, ...app };
}

/** The second click: the confirmation names the action and its button makes the call. */
async function confirm(
  user: ReturnType<typeof userEvent.setup>,
  button: string,
) {
  const dialog = await screen.findByRole("alertdialog");
  await user.click(within(dialog).getByRole("button", { name: button }));
}

const filled = () => [
  fakeAnswer("first_name", "First name", "Harald"),
  fakeAnswer("email", "Email", "harald@example.org"),
  leftAnswer("salary", "Salary expectations"),
];

describe("starting", () => {
  it("offers Apply (Review first) and Apply myself, and starts a Review first application", async () => {
    const api = fakeApply({ application: null });
    const { user, section } = await openSection();
    expect(
      await within(section).findByText(/Nothing is sent without your approval/),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Apply myself" }),
    ).toBeEnabled();
    await user.click(
      within(section).getByRole("button", { name: "Apply (Review first)" }),
    );
    expect(
      await within(section).findByText(/Waiting for a browser/),
    ).toBeInTheDocument();
    expect(within(section).getByTestId("live-view-slot")).toBeInTheDocument();
    expect(api.started).toBe(1);
  });

  it("shows the refusal sentence of the api when the role is excluded", async () => {
    const api = fakeApply({ application: null });
    api.startRefusal = {
      code: "never_apply_role",
      detail:
        "Your rules exclude this role, so the app will not apply for you. You can still apply yourself.",
    };
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", {
        name: "Apply (Review first)",
      }),
    );
    expect(
      await within(section).findByText(/Your rules exclude this role/),
    ).toBeInTheDocument();
    expect(api.started).toBe(0);
  });

  it("comes back to a fill in progress and follows it to the review", async () => {
    const api = fakeApply({
      application: fakeApplication("filling"),
    });
    const { section } = await openSection();
    expect(
      await within(section).findByText(/Filling the form/),
    ).toBeInTheDocument();
    expect(within(section).getByTestId("live-view-slot")).toBeInTheDocument();
    api.application = fakeApplication("ready");
    api.answers = filled();
    expect(
      await within(section).findByText(/waiting for your approval/, undefined, {
        timeout: APPLY_POLL_MS * 4,
      }),
    ).toBeInTheDocument();
    expect(within(section).queryByTestId("live-view-slot")).toBeNull();
  });
});

describe("the review", () => {
  it("highlights the fields left for the person and blocks Approve with the field named", async () => {
    fakeApply({
      application: fakeApplication("ready"),
      answers: [
        fakeAnswer("first_name", "First name", "Harald"),
        leftAnswer("visa", "Work permit number", { required: true }),
        leftAnswer("pronouns", "Pronouns"),
      ],
    });
    const { section } = await openSection();
    const alert = await within(section).findByRole("alert");
    expect(alert).toHaveTextContent("2 fields are left for you");
    expect(alert).toHaveTextContent("Work permit number (required)");
    expect(alert).toHaveTextContent("Pronouns (optional)");
    const rows = within(section)
      .getAllByRole("listitem")
      .filter((li) => li.dataset.left);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("You answer")).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Approve and submit" }),
    ).toBeDisabled();
    expect(section).toHaveTextContent("Answer Work permit number first");
  });

  it("saves an answer typed for a left field, reads the new hash, and then approves exactly that hash", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: [leftAnswer("visa", "Work permit number", { required: true })],
    });
    const { user, section } = await openSection();
    const input = await within(section).findByRole("textbox", {
      name: "Answer for Work permit number",
    });
    await user.type(input, "none needed");
    expect(
      within(section).getByRole("button", { name: "Approve and submit" }),
    ).toBeDisabled();
    expect(section).toHaveTextContent("Save your answers first");
    await user.click(
      within(section).getByRole("button", { name: "Save answer" }),
    );
    await waitFor(() => {
      expect(
        within(section).getByRole("button", { name: "Approve and submit" }),
      ).toBeEnabled();
    });
    expect(api.puts).toEqual([
      { field: "visa", body: { value: "none needed" } },
    ]);
    expect(within(section).queryByRole("alert")).toBeNull();

    await user.click(
      within(section).getByRole("button", { name: "Approve and submit" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(
      "Submit this application to Example Robotics for Robotics Engineer?",
    );
    expect(dialog).toHaveTextContent("CV version 3");
    expect(dialog).toHaveTextContent("Letter version 2");
    expect(api.approves).toEqual([]);
    await user.click(
      within(dialog).getByRole("button", { name: "Approve and submit" }),
    );
    await waitFor(() => {
      expect(api.approves).toEqual(["hash-2"]);
    });
    expect(await within(section).findByText(/Approved\./)).toBeInTheDocument();
  });

  it("saves a yes or no with two buttons and a choice with its select, as the value", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: [
        leftAnswer("remote", "Open to remote work", { kind: "yes_no" }),
        leftAnswer("start", "Start date", {
          kind: "select",
          options: ["Now", "In a month"],
        }),
      ],
    });
    const { user, section } = await openSection();
    const group = await within(section).findByRole("group", {
      name: "Answer for Open to remote work",
    });
    await user.click(within(group).getByRole("button", { name: "Yes" }));
    await user.selectOptions(
      await within(section).findByRole("combobox", {
        name: "Answer for Start date",
      }),
      "In a month",
    );
    await waitFor(() => {
      expect(api.puts).toEqual([
        { field: "remote", body: { value: true } },
        { field: "start", body: { value: "In a month" } },
      ]);
    });
  });

  it("lets the person correct an answer the assistant gave", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: [fakeAnswer("city", "City", "Bordeaux", { source: "agent" })],
    });
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", { name: "Edit: City" }),
    );
    const input = within(section).getByRole("textbox", {
      name: "Answer for City",
    });
    await user.clear(input);
    await user.type(input, "Paris");
    await user.click(
      within(section).getByRole("button", { name: "Save answer" }),
    );
    await waitFor(() => {
      expect(api.puts).toEqual([{ field: "city", body: { value: "Paris" } }]);
    });
    expect(await within(section).findByText("Paris")).toBeInTheDocument();
  });

  it("shows the current form when the api says it is stale, and approves only the new hash", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: filled().slice(0, 2),
    });
    api.stale = {
      answers: [
        fakeAnswer("first_name", "First name", "Harald"),
        fakeAnswer("email", "Email", "new@example.org"),
      ],
    };
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", {
        name: "Approve and submit",
      }),
    );
    let dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Approve and submit" }),
    );
    expect(
      await within(section).findByText(/changed since you looked/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(within(section).getByText("new@example.org")).toBeInTheDocument();
    await user.click(
      within(section).getByRole("button", { name: "Approve and submit" }),
    );
    dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Approve and submit" }),
    );
    await waitFor(() => {
      expect(api.approves).toEqual(["hash-1", "hash-2"]);
    });
  });

  it("rejects the form as filled", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: filled().slice(0, 2),
    });
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", { name: "Reject" }),
    );
    expect(api.decisions).toEqual([]);
    await confirm(user, "Reject the form");
    await waitFor(() => {
      expect(api.decisions).toEqual(["reject"]);
    });
    expect(
      await within(section).findByRole("button", {
        name: "Apply (Review first)",
      }),
    ).toBeInTheDocument();
  });

  it("shows the screenshot of the form and opens it full size", async () => {
    fakeApply({
      application: fakeApplication("ready"),
      answers: filled().slice(0, 2),
    });
    const { user, section } = await openSection();
    const open = await within(section).findByRole("button", {
      name: /Enlarge: The form before the click/,
    });
    expect(within(open).getByRole("img")).toHaveAttribute(
      "src",
      `/api/applications/00000000-0000-4000-9000-0000000000a1/screenshots/shot-before`,
    );
    await user.click(open);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("the destructive buttons", () => {
  it.each([
    [
      "ready",
      "Reject",
      "Reject this filled form?",
      "Nothing is sent. You can start again.",
    ],
    [
      "needs_person",
      "Cancel application",
      "Cancel this application?",
      "Nothing is sent.",
    ],
    [
      "submit_unknown",
      "It was submitted",
      "Mark this application as submitted?",
      "It cannot be undone, and the app will refuse another application for this posting.",
    ],
    [
      "submit_unknown",
      "Try again",
      "Fill and approve again?",
      "the company may receive two applications",
    ],
  ])(
    "%s: %s opens a confirmation with its own sentence and sends nothing until it is confirmed",
    async (state, button, title, sentence) => {
      const api = fakeApply({
        application: fakeApplication(state, { needs: "captcha" }),
        answers: filled().slice(0, 2),
      });
      const { user, section } = await openSection();
      await user.click(
        await within(section).findByRole("button", { name: button }),
      );
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent(title);
      expect(dialog).toHaveTextContent(sentence);
      expect(dialog.textContent).not.toMatch(SEPARATOR);
      expect(api.decisions).toEqual([]);
      await user.click(within(dialog).getByRole("button", { name: "Not yet" }));
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).toBeNull();
      });
      expect(api.decisions).toEqual([]);
      // Escape is a no too.
      await user.click(within(section).getByRole("button", { name: button }));
      await screen.findByRole("alertdialog");
      await user.keyboard("{Escape}");
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).toBeNull();
      });
      expect(api.decisions).toEqual([]);
    },
  );

  it("reads the application again when a decision is refused, and shows where it stands", async () => {
    const api = fakeApply({
      application: fakeApplication("needs_person", { needs: "captcha" }),
      answers: [],
    });
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", {
        name: "Cancel application",
      }),
    );
    api.application = fakeApplication("submitted");
    api.decisionRefusal = {
      detail: "The click already happened.",
      code: "not_cancellable",
      state: "submitted",
    };
    await confirm(user, "Cancel the application");
    expect(
      await within(section).findByText("The click already happened."),
    ).toBeInTheDocument();
    expect(await within(section).findByText(/Applied\./)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

describe("what is shown and what can be typed", () => {
  it("shows a long answer whole, never cut", async () => {
    const long = Array.from(
      { length: 150 },
      (_, index) => `word${String(index).padStart(3, "0")}`,
    ).join(" ");
    expect(long.length).toBeGreaterThan(1000);
    fakeApply({
      application: fakeApplication("ready"),
      answers: [
        fakeAnswer("why", "Why do you want to join?", long, {
          kind: "textarea",
          source: "agent",
        }),
      ],
    });
    const { section } = await openSection();
    const value = await within(section).findByText(long);
    expect(value.textContent).toBe(long);
    expect(value.className).not.toMatch(/line-clamp|truncate/);
  });

  it("offers no input for a skipped widget, a file or a list of choices, and never blocks Approve for them", async () => {
    fakeApply({
      application: fakeApplication("ready"),
      answers: [
        leftAnswer("skipped:0:upload", "Cover photo", { required: false }),
        leftAnswer("resume", "Resume file", { kind: "file", required: true }),
        leftAnswer("tags", "Languages spoken", {
          kind: "multi",
          required: true,
        }),
      ],
    });
    const { section } = await openSection();
    const alert = await within(section).findByRole("alert");
    expect(alert).toHaveTextContent("3 fields are left for you");
    expect(alert).toHaveTextContent(
      "Cover photo (optional), answer on the site",
    );
    expect(alert).toHaveTextContent(
      "Resume file (required), answer on the site",
    );
    const rows = within(section)
      .getAllByRole("listitem")
      .filter((li) => li.dataset.left);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(within(row).getByText("You answer")).toBeInTheDocument();
      expect(within(row).queryByRole("textbox")).toBeNull();
      expect(within(row).queryByRole("button")).toBeNull();
      expect(row).toHaveTextContent("Not filled by the assistant.");
    }
    expect(rows[1]).toHaveTextContent("or reject this form");
    expect(
      within(section).getByRole("button", { name: "Approve and submit" }),
    ).toBeEnabled();
    expect(section.textContent).not.toMatch(SEPARATOR);
  });

  it.each(["text", "textarea", "select", "combobox", "single", "date"])(
    "gives a %s field a box to type in",
    async (kind) => {
      fakeApply({
        application: fakeApplication("ready"),
        answers: [leftAnswer("q", "Question", { kind, required: true })],
      });
      const { section } = await openSection();
      expect(
        await within(section).findByRole("textbox", {
          name: "Answer for Question",
        }),
      ).toBeInTheDocument();
    },
  );

  it("does not offer Edit on a file or a skipped answer the assistant did fill", async () => {
    fakeApply({
      application: fakeApplication("ready"),
      answers: [
        fakeAnswer("resume", "Resume file", "cv.pdf", { kind: "file" }),
        fakeAnswer("skipped:1:x", "Odd widget", "x"),
        fakeAnswer("city", "City", "Bordeaux"),
      ],
    });
    const { section } = await openSection();
    await within(section).findByText("Bordeaux");
    expect(
      within(section).getAllByRole("button", { name: /^Edit: / }),
    ).toHaveLength(1);
  });

  it("shows the approval the api answered with after a save, without reading it again", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: [leftAnswer("visa", "Work permit number", { required: true })],
    });
    const { user, section } = await openSection();
    await user.type(
      await within(section).findByRole("textbox", {
        name: "Answer for Work permit number",
      }),
      "none needed",
    );
    await user.click(
      within(section).getByRole("button", { name: "Save answer" }),
    );
    expect(await within(section).findByText("none needed")).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Approve and submit" }),
    ).toBeEnabled();
    expect(api.reads).toBe(1);
  });

  it("keeps Approve disabled while the approval is being read again", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: filled().slice(0, 2),
    });
    const { client, section } = await openSection();
    const approve = await within(section).findByRole("button", {
      name: "Approve and submit",
    });
    expect(approve).toBeEnabled();
    let release = () => {};
    api.hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    void client.invalidateQueries({
      queryKey: approvalKey("00000000-0000-4000-9000-0000000000a1"),
    });
    await waitFor(() => {
      expect(approve).toBeDisabled();
    });
    release();
    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
  });

  it("makes an open editor follow the server value when the form is replaced", async () => {
    const api = fakeApply({
      application: fakeApplication("ready"),
      answers: [fakeAnswer("city", "City", "Bordeaux", { source: "agent" })],
    });
    const { client, user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", { name: "Edit: City" }),
    );
    const input = within(section).getByRole("textbox", {
      name: "Answer for City",
    });
    await user.type(input, " centre");
    expect(
      within(section).getByRole("button", { name: "Approve and submit" }),
    ).toBeDisabled();
    api.answers = [fakeAnswer("city", "City", "Lyon", { source: "agent" })];
    api.version += 1;
    await client.invalidateQueries({
      queryKey: approvalKey("00000000-0000-4000-9000-0000000000a1"),
    });
    await waitFor(() => {
      expect(
        within(section).getByRole("textbox", { name: "Answer for City" }),
      ).toHaveValue("Lyon");
    });
    expect(
      within(section).getByRole("button", { name: "Approve and submit" }),
    ).toBeEnabled();
  });
});

describe("a failing refresh", () => {
  it("keeps the section and says so when one poll fails", async () => {
    fakeApply({ application: fakeApplication("filling") });
    const { section } = await openSection();
    expect(
      await within(section).findByText(/Filling the form/),
    ).toBeInTheDocument();
    server.use(
      http.get("*/api/applications", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    expect(
      await within(section).findByText(
        /Could not refresh just now/,
        undefined,
        {
          timeout: 9000,
        },
      ),
    ).toBeInTheDocument();
    expect(within(section).getByText(/Filling the form/)).toBeInTheDocument();
    expect(within(section).queryByText(/could not be loaded/)).toBeNull();
  }, 15_000);

  it("says what a queued application waits for", async () => {
    fakeApply({
      application: fakeApplication("queued", { needs: "documents:letter" }),
    });
    const { section } = await openSection();
    expect(
      await within(section).findByText(/Waiting for your CV and letter/),
    ).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/within a minute/);
  });
});

describe("after the click", () => {
  it("shows Applied with both screenshots and the run report", async () => {
    const api = fakeApply({
      application: fakeApplication("submitted", {
        submitted_at: "2026-10-15T08:02:30Z",
      }),
      answers: filled().slice(0, 2),
    });
    api.screenshots.push({
      id: "shot-after",
      kind: "after",
      created_at: "2026-10-15T08:02:30Z",
    });
    api.events.push(
      { at: "2026-10-15T08:02:20Z", kind: "click", data: {} },
      {
        at: "2026-10-15T08:02:30Z",
        kind: "screenshot",
        data: { kind: "after" },
      },
    );
    const { section } = await openSection();
    expect(await within(section).findByText(/Applied\./)).toBeInTheDocument();
    expect(
      await within(section).findByRole("img", {
        name: "The page after the click",
      }),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("img", { name: "The form before the click" }),
    ).toBeInTheDocument();
    const report = await within(section).findByRole("region", {
      name: "Run report",
    });
    expect(report).toHaveTextContent("Took 2 min 30 s");
    expect(report).toHaveTextContent("Submit clicked");
    expect(report).toHaveTextContent("Screenshot after the click");
    expect(
      within(section).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
  });

  it("offers It was submitted and Try again when the outcome is unknown", async () => {
    const api = fakeApply({
      application: fakeApplication("submit_unknown"),
      answers: filled().slice(0, 2),
    });
    const { user, section } = await openSection();
    expect(
      await within(section).findByText(/We could not confirm this application/),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
    await user.click(
      within(section).getByRole("button", { name: "It was submitted" }),
    );
    expect(api.decisions).toEqual([]);
    await confirm(user, "Mark as submitted");
    await waitFor(() => {
      expect(api.decisions).toEqual(["submitted"]);
    });
    expect(await within(section).findByText(/Applied\./)).toBeInTheDocument();
  });

  it("offers Try again when the site refused the form", async () => {
    const api = fakeApply({
      application: fakeApplication("refused_by_site"),
      answers: filled().slice(0, 2),
    });
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", { name: "Try again" }),
    );
    expect(api.decisions).toEqual([]);
    await confirm(user, "Fill and approve again");
    await waitFor(() => {
      expect(api.decisions).toEqual(["retry"]);
    });
    expect(
      await within(section).findByText(/waiting for your approval/),
    ).toBeInTheDocument();
  });
});

describe("other states", () => {
  it("says what the person must do when the run needs them, and offers only Cancel", async () => {
    const api = fakeApply({
      application: fakeApplication("needs_person", { needs: "captcha" }),
      answers: [leftAnswer("q", "Why us?", { required: true })],
    });
    const { user, section } = await openSection();
    expect(
      await within(section).findByText(/asks for a captcha/),
    ).toBeInTheDocument();
    expect(
      within(section).queryByRole("button", { name: "Approve and submit" }),
    ).toBeNull();
    await user.click(
      within(section).getByRole("button", { name: "Cancel application" }),
    );
    expect(api.decisions).toEqual([]);
    await confirm(user, "Cancel the application");
    await waitFor(() => {
      expect(api.decisions).toEqual(["cancel"]);
    });
  });

  it("offers no action on a state it does not know", async () => {
    fakeApply({ application: fakeApplication("brand_new_state") });
    const { section } = await openSection();
    expect(
      await within(section).findByText(/state this page does not know/),
    ).toBeInTheDocument();
    expect(within(section).queryAllByRole("button")).toHaveLength(0);
  });

  it("says why the last attempt ended and lets the person apply again", async () => {
    fakeApply({
      application: fakeApplication("failed", { error_code: "interrupted" }),
    });
    const { section } = await openSection();
    expect(
      await within(section).findByText(
        /failed: it was interrupted\. Nothing was sent/,
      ),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Apply (Review first)" }),
    ).toBeEnabled();
  });

  it.each([
    ["queued", {}],
    ["filling", {}],
    ["needs_person", { needs: "signin" }],
    ["ready", {}],
    ["approved", {}],
    ["submitted", {}],
    ["submit_unknown", {}],
    ["refused_by_site", {}],
    ["failed", { error_code: "internal_error" }],
    ["blocked", {}],
    ["posting_closed", {}],
    ["cancelled", {}],
    ["rehearsed", {}],
  ])(
    "writes no separator dash in the text of the %s state",
    async (state, extra) => {
      fakeApply({
        application: fakeApplication(state, extra),
        answers: [...filled(), leftAnswer("q", "Question", { required: true })],
      });
      const { section } = await openSection();
      await waitFor(() => {
        expect(section.textContent).not.toMatch(/Loading the/);
      });
      expect(section.textContent).not.toMatch(SEPARATOR);
    },
  );
});

describe("Full auto's hold on the job page", () => {
  it("offers Cancel, and nothing to approve, while the app still decides on a Full auto item", async () => {
    const api = fakeApply({
      application: fakeApplication("ready", { mode: "full_auto" }),
      answers: filled().slice(0, 2),
    });
    const { user, section } = await openSection();
    expect(
      await within(section).findByText(
        /Full auto: filled. The app checks it now/,
      ),
    ).toBeInTheDocument();
    for (const name of ["Approve and submit", "Reject"]) {
      expect(within(section).queryByRole("button", { name })).toBeNull();
    }
    expect(within(section).queryByRole("timer")).toBeNull();
    const cancels = within(section).getAllByRole("button", {
      name: "Cancel application",
    });
    expect(cancels).toHaveLength(1);
    await user.click(cancels[0]!);
    expect(api.decisions).toEqual([]);
    await confirm(user, "Cancel the application");
    await waitFor(() => {
      expect(api.decisions).toEqual(["cancel"]);
    });
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("offers Reject, not Cancel, on a Review first form waiting for the approval", async () => {
    fakeApply({
      application: fakeApplication("ready"),
      answers: filled().slice(0, 2),
    });
    const { section } = await openSection();
    await within(section).findByRole("button", { name: "Reject" });
    expect(
      within(section).queryByRole("button", { name: "Cancel application" }),
    ).toBeNull();
  });

  it("counts the hold down on the server's clock, with one Cancel that stops it", async () => {
    const api = fakeApply({
      application: fakeApplication("held", { mode: "full_auto" }),
    });
    api.detail = {
      hold_until: "2001-02-03T10:05:00Z",
      now: "2001-02-03T10:00:00Z",
    };
    const { user, section } = await openSection();
    const timer = await within(section).findByRole("timer");
    expect(timer.textContent).toMatch(/^(5:00|4:5\d)$/);
    expect(section).toHaveTextContent("Full auto: your Like approved it.");
    const cancels = within(section).getAllByRole("button", {
      name: "Cancel application",
    });
    expect(cancels).toHaveLength(1);
    await user.click(cancels[0]!);
    await confirm(user, "Cancel the application");
    await waitFor(() => {
      expect(api.decisions).toEqual(["cancel"]);
    });
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });
});
