import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApplication, fakeApply } from "@/test/apply";
import { fakeSession, member } from "@/test/auth";
import { connected, fakeScreens } from "@/test/browser";
import { waitForOnFakeClock } from "@/test/clock";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { APPLY_POLL_MS } from "./apply-state";
import type { ManualStatus } from "./manual-api";
import { outcomeLine, REOPEN_MS } from "./manual-state";

const JOB_ID = "00000000-0000-4000-9000-000000000001";
const PATH = `/jobs/${JOB_ID}`;
const SEPARATOR = /[–—]| - /;

const manualApp = (state: string) => fakeApplication(state, { mode: "manual" });

function status(over: Partial<ManualStatus> = {}): ManualStatus {
  return {
    state: "filling",
    phase: "open",
    position: null,
    documents: ["cv", "letter"],
    confirmation_seen: false,
    chooser: false,
    idle_left_s: 240,
    idle_warning: false,
    busy: null,
    last: null,
    warnings: [],
    ...over,
  };
}

/** The Apply myself routes, in memory; every write is recorded. */
function fakeManual(api: ReturnType<typeof fakeApply>, initial: ManualStatus) {
  const state = {
    status: initial,
    opens: 0,
    actions: [] as string[],
    records: [] as unknown[],
    closes: 0,
    started: [] as unknown[],
    other: [] as string[],
  };
  server.use(
    http.post("*/api/postings/:id/applications", async ({ request }) => {
      state.started.push(await request.json());
      api.application = manualApp("queued");
      return HttpResponse.json(
        { ...api.application, warnings: [] },
        { status: 201 },
      );
    }),
    http.get("*/api/applications/:id/manual", () =>
      HttpResponse.json(state.status),
    ),
    http.post("*/api/applications/:id/manual/open", () => {
      state.opens += 1;
      return HttpResponse.json(state.status, { status: 202 });
    }),
    http.post("*/api/applications/:id/manual/actions", async ({ request }) => {
      const body = (await request.json()) as { action: string };
      state.actions.push(body.action);
      state.status = { ...state.status, busy: body.action };
      return HttpResponse.json(state.status, { status: 202 });
    }),
    http.post("*/api/applications/:id/manual/record", async ({ request }) => {
      state.records.push(await request.json());
      api.application = manualApp("submitted");
      return HttpResponse.json(api.application);
    }),
    http.post("*/api/applications/:id/manual/close", () => {
      state.closes += 1;
      api.application = manualApp("cancelled");
      return HttpResponse.json(api.application);
    }),
    // Anything that could click the form's Submit for the person: never called in this mode.
    http.post("*/api/applications/:id/:action", ({ params }) => {
      state.other.push(String(params.action));
      return HttpResponse.json({ detail: "no", code: "no" }, { status: 409 });
    }),
  );
  return state;
}

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

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function openSection() {
  const user = userEvent.setup();
  renderApp(PATH);
  const heading = await screen.findByRole("heading", { name: "Apply" });
  return { user, section: heading.closest("section") as HTMLElement };
}

async function panel() {
  return screen.findByTestId("manual-panel");
}

describe("starting Apply myself", () => {
  it("starts a manual application and asks for the browser", async () => {
    fakeScreens();
    const api = fakeApply({ application: null });
    const manual = fakeManual(
      api,
      status({ state: "queued", phase: "waiting", position: 2 }),
    );
    const { user, section } = await openSection();
    await user.click(
      await within(section).findByRole("button", { name: "Apply myself" }),
    );
    expect(manual.started).toEqual([{ mode: "manual" }]);
    expect(
      await within(section).findByText(/Opening your browser on the form/),
    ).toBeInTheDocument();
    expect(
      await within(await panel()).findByText(
        "Waiting for your browser, place 2 in line.",
      ),
    ).toBeInTheDocument();
    expect(manual.opens).toBe(1);
    expect(within(section).queryByTestId("live-view-slot")).toBeNull();
    expect(
      within(section).queryByRole("button", { name: "Cancel application" }),
    ).toBeNull();
  });

  it("asks for the browser again while it waits, to keep its place", async () => {
    vi.useFakeTimers();
    const api = fakeApply({ application: manualApp("queued") });
    const manual = fakeManual(
      api,
      status({ state: "queued", phase: "waiting", position: 1 }),
    );
    renderApp(PATH);
    await waitForOnFakeClock(() => {
      expect(manual.opens).toBe(1);
    });
    await waitForOnFakeClock(() => {
      expect(manual.opens).toBe(2);
    }, REOPEN_MS / 4);
  });

  it("shows the never apply warning of the start", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    fakeManual(
      api,
      status({
        warnings: [
          {
            code: "never_apply_role",
            sentence: "Your rules exclude this role.",
          },
        ],
      }),
    );
    await openSection();
    expect(
      await within(await panel()).findByText(/Your rules exclude this role\./),
    ).toBeInTheDocument();
  });
});

describe("the open session", () => {
  it("keeps manual needs_person controls in the manual panel", async () => {
    const api = fakeApply({
      application: fakeApplication("needs_person", {
        mode: "manual",
        needs: "captcha",
      }),
    });
    const manual = fakeManual(api, status({ state: "needs_person" }));
    const { section } = await openSection();
    expect(
      await within(await panel()).findByRole("button", {
        name: "Close without submitting",
      }),
    ).toBeEnabled();
    expect(within(section).queryByTestId("needs-person")).toBeNull();
    expect(within(section).queryByTestId("live-view-slot")).toBeNull();
    expect(manual.other).toEqual([]);
    expect(api.approves).toEqual([]);
    expect(api.decisions).toEqual([]);
  });

  it("shows the live view with the keyboard and the mouse to the person, and attaches the CV", async () => {
    const opened = fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    const manual = fakeManual(api, status());
    const { user } = await openSection();
    const box = await panel();
    await connected(opened);
    expect(
      await within(box).findByText(/This is your own session/),
    ).toBeInTheDocument();
    expect(within(box).queryByRole("button", { name: /Take over/ })).toBeNull();
    await user.click(within(box).getByRole("button", { name: "Attach my CV" }));
    expect(manual.actions).toEqual(["attach_cv"]);
    expect(
      await within(box).findByText("Attaching your CV."),
    ).toBeInTheDocument();
    expect(
      within(box).getByRole("button", { name: "Attach my letter" }),
    ).toBeDisabled();
    manual.status = status({
      last: { action: "attach_cv", outcome: "attached" },
    });
    expect(
      await within(box).findByText(
        "Your CV is attached to the form.",
        undefined,
        { timeout: APPLY_POLL_MS * 4 },
      ),
    ).toBeInTheDocument();
  });

  it("keeps the person's keys away while the app fills their details", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    const manual = fakeManual(api, status());
    const { user } = await openSection();
    const box = await panel();
    await user.click(
      await within(box).findByRole("button", { name: "Fill my details" }),
    );
    expect(manual.actions).toEqual(["fill_details"]);
    expect(
      await within(box).findByText(/The app is filling your details/),
    ).toBeInTheDocument();
    for (const name of ["Fill my details", "Attach my CV", "Attach my letter"])
      expect(within(box).getByRole("button", { name })).toBeDisabled();
  });

  it("offers a disabled Attach my letter when no letter was placed", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    fakeManual(api, status({ documents: ["cv"] }));
    await openSection();
    const box = await panel();
    expect(
      await within(box).findByRole("button", { name: "Attach my letter" }),
    ).toBeDisabled();
    expect(
      within(box).getByRole("button", { name: "Attach my CV" }),
    ).toBeEnabled();
  });

  it("warns a minute before the idle timeout, and says a file chooser waits", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    fakeManual(
      api,
      status({ idle_left_s: 55, idle_warning: true, chooser: true }),
    );
    await openSection();
    const box = await panel();
    expect(
      await within(box).findByText(
        "Your browser closes in 55 seconds unless you click or type in it.",
      ),
    ).toBeInTheDocument();
    expect(
      within(box).getByText(/The form asks for a file/),
    ).toBeInTheDocument();
  });

  it("records it in one click once the page showed a confirmation", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    const manual = fakeManual(api, status({ confirmation_seen: true }));
    const { user, section } = await openSection();
    const box = await panel();
    await user.click(
      await within(box).findByRole("button", { name: "Record as applied" }),
    );
    expect(manual.records).toEqual([{ seen: true }]);
    expect(
      await within(section).findByText(/You sent this form yourself/),
    ).toBeInTheDocument();
    expect(within(section).getByText(/Applied\./)).toBeInTheDocument();
    expect(manual.other).toEqual([]);
  });

  it("records I submitted it after a confirmation, with seen false", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    const manual = fakeManual(api, status());
    const { user } = await openSection();
    const box = await panel();
    expect(
      within(box).queryByRole("button", { name: "Record as applied" }),
    ).toBeNull();
    await user.click(
      await within(box).findByRole("button", { name: "I submitted it" }),
    );
    expect(manual.records).toEqual([]);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Record as sent" }),
    );
    await waitFor(() => {
      expect(manual.records).toEqual([{ seen: false }]);
    });
    expect(manual.other).toEqual([]);
  });

  it("closes without submitting after a confirmation", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    const manual = fakeManual(api, status());
    const { user, section } = await openSection();
    const box = await panel();
    await user.click(
      await within(box).findByRole("button", {
        name: "Close without submitting",
      }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Close without submitting" }),
    );
    expect(
      await within(section).findByText(/You closed the last session/),
    ).toBeInTheDocument();
    expect(manual.closes).toBe(1);
    expect(manual.records).toEqual([]);
    expect(within(section).queryByTestId("manual-panel")).toBeNull();
  });

  it("writes no separator dash in what it shows", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    fakeManual(
      api,
      status({
        confirmation_seen: true,
        idle_warning: true,
        idle_left_s: 30,
        chooser: true,
        last: { action: "attach_letter", outcome: "pick" },
        warnings: [
          {
            code: "never_apply_role",
            sentence: "Your rules exclude this role.",
          },
        ],
      }),
    );
    const { section } = await openSection();
    await within(await panel()).findByText(/Your browser closes in 30/);
    expect(section.textContent).not.toMatch(SEPARATOR);
  });
});

describe("outcomeLine", () => {
  it.each([
    ["fill_details", "filled:0", "No empty field asked for your details."],
    [
      "fill_details",
      "filled:1",
      "Filled 1 field with your details. Check them before you send.",
    ],
    [
      "fill_details",
      "filled:3",
      "Filled 3 fields with your details. Check them before you send.",
    ],
    [
      "fill_details",
      "taken:2",
      "You took the keyboard back, so the app stopped after 2 fields.",
    ],
    [
      "fill_details",
      "unreadable",
      "Your details could not be filled. You can type them yourself.",
    ],
    ["attach_cv", "attached", "Your CV is attached to the form."],
    ["attach_letter", "attached", "Your letter is attached to the form."],
    [
      "attach_cv",
      "pick",
      "The app could not tell which field takes your CV. Click that field's button in the form, then click Attach my CV again.",
    ],
    [
      "attach_letter",
      "no_document",
      "No letter was placed in your browser for this posting.",
    ],
    [
      "attach_cv",
      "page_error",
      "Your CV could not be attached. You can try again.",
    ],
  ])("%s %s", (action, outcome, line) => {
    expect(outcomeLine({ action, outcome })).toBe(line);
    expect(line).not.toMatch(SEPARATOR);
  });

  it("says nothing of an action it does not know", () => {
    expect(outcomeLine({ action: "close", outcome: "closed" })).toBeNull();
    expect(outcomeLine(null)).toBeNull();
  });
});

describe("a session that ended before the page confirmed", () => {
  const ended = () =>
    fakeApplication("queued", { mode: "manual", needs: "session_ended" });

  it("opens nothing by itself and offers I submitted it", async () => {
    const api = fakeApply({ application: ended() });
    const manual = fakeManual(api, status({ state: "queued", phase: "ended" }));
    const { user, section } = await openSection();
    expect(
      await within(section).findByText(
        "Your browser closed before the app saw the site's confirmation. Nothing is recorded as sent yet.",
      ),
    ).toBeInTheDocument();
    const box = await panel();
    expect(
      within(box).getByText(/If you sent the form, click I submitted it/),
    ).toBeInTheDocument();
    expect(manual.opens).toBe(0);
    await user.click(
      within(box).getByRole("button", { name: "I submitted it" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Record as sent" }),
    );
    await waitFor(() => {
      expect(manual.records).toEqual([{ seen: false }]);
    });
    expect(manual.opens).toBe(0);
    expect(manual.other).toEqual([]);
  });

  it("asks for the browser again once the person opens it again", async () => {
    const api = fakeApply({ application: ended() });
    const manual = fakeManual(api, status({ state: "queued", phase: "ended" }));
    const { user } = await openSection();
    const box = await panel();
    expect(manual.opens).toBe(0);
    await user.click(
      within(box).getByRole("button", { name: "Open my browser again" }),
    );
    await waitFor(() => {
      expect(manual.opens).toBe(1);
    });
    expect(
      within(box).queryByRole("button", { name: "Open my browser again" }),
    ).toBeNull();
  });
});

describe("a page that showed the site's confirmation", () => {
  it("offers to record it and no Close without submitting", async () => {
    fakeScreens();
    const api = fakeApply({ application: manualApp("filling") });
    fakeManual(api, status({ confirmation_seen: true }));
    await openSection();
    const box = await panel();
    expect(
      await within(box).findByRole("button", { name: "Record as applied" }),
    ).toBeInTheDocument();
    expect(
      within(box).queryByRole("button", { name: "Close without submitting" }),
    ).toBeNull();
  });
});
