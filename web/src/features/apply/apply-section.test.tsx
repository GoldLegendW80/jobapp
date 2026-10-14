import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

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

const filled = () => [
  fakeAnswer("first_name", "First name", "Harald"),
  fakeAnswer("email", "Email", "harald@example.org"),
  leftAnswer("salary", "Salary expectations"),
];

describe("starting", () => {
  it("offers Apply (Review first) and a disabled Apply myself, and starts a Review first application", async () => {
    const api = fakeApply({ application: null });
    const { user, section } = await openSection();
    expect(
      await within(section).findByText(/Nothing is sent without your approval/),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: /Apply myself/ }),
    ).toBeDisabled();
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
