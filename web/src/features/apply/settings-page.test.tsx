import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import {
  answerText,
  capSentence,
  capsKey,
  holdText,
  readCap,
  readFit,
  type ApplySettings,
  type CapDraft,
} from "./settings-api";

const PATH = "/settings/applications";
/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const defaults: ApplySettings = {
  mode_after_like: "review",
  full_auto_available: false,
  paused: false,
  min_fit: 80,
  min_fit_chosen: false,
  safety_ceiling: 30,
  hold_s: 900,
  hold_min_s: 900,
  company_days: 30,
  show_postings_from: 60,
  write_documents_from: 65,
  rehearsals: 3,
  rehearsals_done: 0,
  full_auto_since: null,
};

function fakeSettings(initial: Partial<ApplySettings> = {}) {
  const state = {
    settings: { ...defaults, ...initial },
    puts: [] as unknown[],
  };
  server.use(
    http.get("*/api/apply/settings", () => HttpResponse.json(state.settings)),
    http.put("*/api/apply/settings", async ({ request }) => {
      const body = (await request.json()) as Partial<ApplySettings>;
      state.puts.push(body);
      state.settings = {
        ...state.settings,
        ...body,
        min_fit_chosen: state.settings.min_fit_chosen || "min_fit" in body,
      };
      return HttpResponse.json(state.settings);
    }),
  );
  return state;
}

type Row = CapDraft & { used: number };
type Answer = { status: number; body: Record<string, string> };

const revisionOf = (n: number) => String(n).padStart(64, "0");

/**
 * The caps routes as the api answers them: every answer gives every row a new id (the PUT deletes
 * and inserts the whole list), and a PUT with another revision than the stored one is a 409.
 */
function fakeCaps(initial: Row[]) {
  const state = {
    rows: initial,
    turn: 1,
    serial: 0,
    gets: 0,
    puts: [] as Array<{ items: CapDraft[]; revision: string }>,
    next: null as Answer | null,
  };
  const out = () => ({
    items: state.rows.map((row) => ({ id: `cap-${++state.serial}`, ...row })),
    revision: revisionOf(state.turn),
  });
  server.use(
    http.get("*/api/apply/caps", () => {
      state.gets += 1;
      return HttpResponse.json(out());
    }),
    http.put("*/api/apply/caps", async ({ request }) => {
      const body = (await request.json()) as (typeof state.puts)[number];
      state.puts.push(body);
      const next = state.next;
      if (next) {
        state.next = null;
        return HttpResponse.json(next.body, { status: next.status });
      }
      if (body.revision !== revisionOf(state.turn))
        return HttpResponse.json(
          { detail: STALE, code: "stale" },
          { status: 409 },
        );
      state.rows = body.items.map((item) => ({
        ...item,
        used: state.rows.find((row) => row.label === item.label)?.used ?? 0,
      }));
      state.turn += 1;
      return HttpResponse.json(out());
    }),
  );
  return state;
}

const STALE =
  "Your caps changed in another tab. The page now shows the current ones.";

type Entry = { question_key: string; question: string; answer: unknown };

function fakeBank(initial: Entry[]) {
  const state = {
    entries: initial,
    puts: [] as Array<{ question_key: string; answer: string | null }>,
    gets: 0,
  };
  const out = () => ({
    items: state.entries.map((entry) => ({
      ...entry,
      used_count: 2,
      updated_at: "2026-10-15T08:00:00Z",
    })),
  });
  server.use(
    http.get("*/api/apply/answer-bank", () => {
      state.gets += 1;
      return HttpResponse.json(out());
    }),
    http.put("*/api/apply/answer-bank", async ({ request }) => {
      const body = (await request.json()) as (typeof state.puts)[number];
      state.puts.push(body);
      const found = state.entries.find(
        (entry) => entry.question_key === body.question_key,
      );
      if (!found)
        return HttpResponse.json(
          { detail: "No such saved answer." },
          { status: 404 },
        );
      state.entries =
        body.answer === null
          ? state.entries.filter((entry) => entry !== found)
          : state.entries.map((entry) =>
              entry === found ? { ...entry, answer: body.answer } : entry,
            );
      return HttpResponse.json(out());
    }),
  );
  return state;
}

const robotics: Row = {
  label: "Example Robotics",
  max_count: 3,
  window_days: 90,
  same_role_days: 180,
  used: 1,
};
const northwind: Row = {
  label: "Northwind Labs",
  max_count: 2,
  window_days: 30,
  same_role_days: null,
  used: 0,
};

beforeEach(() => {
  fakeSession(member);
});

async function openPage() {
  const user = userEvent.setup();
  const app = renderApp(PATH);
  await screen.findByRole("heading", { name: "Apply automatically from" });
  return { user, ...app };
}

const cardOf = (heading: string) =>
  screen
    .getByRole("heading", { name: heading })
    .closest<HTMLElement>("[data-slot=card]") as HTMLElement;

describe("the Applications page", () => {
  it("has a distinct settings title and heading", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    await openPage();
    expect(document.title).toMatch(/^Application settings/);
    expect(
      screen.getByRole("heading", { name: "Application settings" }),
    ).toBeVisible();
  });

  it("labels the three thresholds apart and changes only the third", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    await openPage();
    expect(
      within(cardOf("Show postings from")).getByText("60"),
    ).toBeInTheDocument();
    expect(
      within(cardOf("Write documents from")).getByText("65"),
    ).toBeInTheDocument();
    expect(within(cardOf("Show postings from")).queryByRole("textbox")).toBe(
      null,
    );
    expect(within(cardOf("Write documents from")).queryByRole("textbox")).toBe(
      null,
    );
    expect(
      within(cardOf("Apply automatically from")).getByRole("textbox", {
        name: "Fit score, 0 to 100",
      }),
    ).toHaveValue("80");
    expect(
      screen.getByText(/You have not chosen one yet, so 80 applies/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open Profile, Search" }),
    ).toHaveAttribute("href", "/profile?tab=search");
  });

  it("says Not set for a threshold the person has not set", async () => {
    fakeSettings({ show_postings_from: null, write_documents_from: null });
    fakeCaps([]);
    fakeBank([]);
    await openPage();
    expect(
      within(cardOf("Show postings from")).getByText("Not set"),
    ).toBeInTheDocument();
    expect(
      within(cardOf("Write documents from")).getByText("Not set"),
    ).toBeInTheDocument();
  });

  it("explains what a Like does before Full auto turns on, then turns it on", async () => {
    const api = fakeSettings({ min_fit: 72, hold_s: 1200 });
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    expect(screen.getByRole("radio", { name: "Review first" })).toBeChecked();
    const full = screen.getByRole("radio", { name: "Full auto" });
    expect(full).toBeEnabled();
    expect(full).toHaveAccessibleDescription(/Your Like is your approval/);

    await user.click(full);
    const panel = screen.getByRole("region", { name: "Turn on Full auto" });
    expect(panel).toHaveTextContent(
      "The app will submit applications for you when a posting's fit is at least 72, at least 20 minutes after each Like unless you cancel, and never more than one application per company in 30 days.",
    );
    expect(panel).toHaveTextContent(
      'Your next 3 Likes are rehearsals: the app fills the form and checks it, submits nothing, and asks you "Does this look right?" in Applications.',
    );
    expect(panel).toHaveTextContent(
      "A company cap you set on this page replaces the one per company rule for that company.",
    );
    const waits = within(panel).getByRole("list", {
      name: "These wait for you in Applications instead:",
    });
    expect(
      within(waits)
        .getAllByRole("listitem")
        .map((one) => one.textContent),
    ).toEqual([
      "a posting whose fit is below 72",
      "a site Full auto does not handle yet",
      "a question left, a captcha or a sign in",
      "a free text answer the app wrote itself, unless the app's own check of it passed",
      "a term your never apply rules exclude, in the posting's text",
      "a CV or letter that runs over its page or has a correction to look at",
      "a second application to the same company within 30 days",
      "anything past the safety ceiling of 30 submissions a day",
    ]);
    expect(api.puts).toEqual([]);

    await user.click(
      within(panel).getByRole("button", { name: "Keep Review first" }),
    );
    expect(screen.getByRole("radio", { name: "Review first" })).toBeChecked();
    expect(api.puts).toEqual([]);

    await user.click(full);
    await user.click(screen.getByRole("button", { name: "Turn on Full auto" }));
    await screen.findByText("Full auto is on.");
    expect(api.puts).toEqual([{ mode_after_like: "full_auto" }]);
    expect(screen.getByRole("radio", { name: "Full auto" })).toBeChecked();
    expect(
      screen.getByRole("region", { name: "Full auto is on" }),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("turns Full auto on by keyboard", async () => {
    const api = fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    screen.getByRole("radio", { name: "Review first" }).focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("radio", { name: "Full auto" })).toHaveFocus();
    const turnOn = await screen.findByRole("button", {
      name: "Turn on Full auto",
    });
    turnOn.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(api.puts).toEqual([{ mode_after_like: "full_auto" }]);
    });
  });

  it("turns Full auto off at once with Review first, and says where held items go", async () => {
    const api = fakeSettings({
      mode_after_like: "full_auto",
      rehearsals_done: 3,
      paused: true,
    });
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    const on = screen.getByRole("region", { name: "Full auto is on" });
    expect(on).not.toHaveTextContent("rehearsal");
    expect(on).toHaveTextContent("Automatic applications are paused");
    await user.click(screen.getByRole("radio", { name: "Review first" }));
    await screen.findByText(
      "Full auto is off. Held applications wait for you in Applications.",
    );
    expect(api.puts).toEqual([{ mode_after_like: "review" }]);
    expect(screen.getByRole("radio", { name: "Review first" })).toBeChecked();
  });

  it("names the rehearsals left once Full auto is on", async () => {
    fakeSettings({ mode_after_like: "full_auto", rehearsals_done: 2 });
    fakeCaps([]);
    fakeBank([]);
    await openPage();
    expect(
      screen.getByRole("region", { name: "Full auto is on" }),
    ).toHaveTextContent("Your next Like is a rehearsal:");
  });

  it("keeps the safety ceiling read only and saves the hold, never below the minimum", async () => {
    const api = fakeSettings({ hold_min_s: 600 });
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    const advanced = screen.getByText("Advanced");
    expect(advanced.closest("details")).not.toHaveAttribute("open");
    await user.click(advanced);
    expect(advanced.closest("details")).toHaveAttribute("open");
    expect(
      screen.getByText(/At most 30 submissions a day in Full auto/),
    ).toBeVisible();
    expect(screen.getByText(/15 minutes between a Full auto/)).toBeVisible();
    expect(screen.getByText(/Never less than 10 minutes/)).toBeVisible();
    const box = screen.getByRole("textbox", { name: "Hold length in minutes" });
    expect(box).toHaveValue("15");
    for (const wrong of ["9", "1441", "", "ten", "12.5"]) {
      await user.clear(box);
      if (wrong) await user.type(box, wrong);
      await user.click(
        screen.getByRole("button", { name: "Save hold length" }),
      );
      expect(box).toHaveAccessibleDescription(
        "Type a whole number of minutes from 10 to 1440.",
      );
    }
    expect(api.puts).toEqual([]);
    await user.clear(box);
    await user.type(box, "30");
    await user.click(screen.getByRole("button", { name: "Save hold length" }));
    await screen.findByText("Hold length saved.");
    expect(api.puts).toEqual([{ hold_s: 1800 }]);
    expect(screen.getByText(/30 minutes between a Full auto/)).toBeVisible();
  });

  it("shows the api's sentence when the hold is refused", async () => {
    fakeSettings();
    server.use(
      http.put("*/api/apply/settings", () =>
        HttpResponse.json(
          {
            detail: "The hold cannot be shorter than the server's minimum.",
            code: "hold_too_short",
          },
          { status: 422 },
        ),
      ),
    );
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    await user.click(screen.getByText("Advanced"));
    const box = screen.getByRole("textbox", { name: "Hold length in minutes" });
    await user.clear(box);
    await user.type(box, "20");
    await user.click(screen.getByRole("button", { name: "Save hold length" }));
    expect(
      await screen.findByText(
        "The hold cannot be shorter than the server's minimum.",
      ),
    ).toBeVisible();
  });

  it("saves Apply automatically from, and refuses a value out of range before any call", async () => {
    const api = fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    const box = screen.getByRole("textbox", { name: "Fit score, 0 to 100" });
    for (const wrong of ["101", "8.5", "", "eighty", "-5"]) {
      await user.clear(box);
      if (wrong) await user.type(box, wrong);
      await user.click(screen.getByRole("button", { name: "Save threshold" }));
      expect(box).toHaveAccessibleDescription(
        "Type a whole number from 0 to 100.",
      );
      expect(box).toHaveFocus();
    }
    expect(api.puts).toEqual([]);
    await user.clear(box);
    await user.type(box, "85{Enter}");
    await waitFor(() => {
      expect(api.puts).toEqual([{ min_fit: 85 }]);
    });
    expect(await screen.findByText("Threshold saved.")).toBeInTheDocument();
    expect(screen.queryByText(/You have not chosen one yet/)).toBe(null);
    expect(
      screen.getByRole("button", { name: "Save threshold" }),
    ).toBeDisabled();
  });

  it("turns the pause switch on and off from the keyboard", async () => {
    const api = fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    const pause = screen.getByRole("switch", {
      name: "Pause automatic applications",
    });
    expect(pause).not.toBeChecked();
    pause.focus();
    await user.keyboard(" ");
    await waitFor(() => {
      expect(pause).toBeChecked();
    });
    expect(pause).toHaveAccessibleDescription(/^Paused\./);
    await user.keyboard(" ");
    await waitFor(() => {
      expect(pause).not.toBeChecked();
    });
    expect(api.puts).toEqual([{ paused: true }, { paused: false }]);
  });

  it("shows why the pause could not be saved", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    server.use(
      http.put("*/api/apply/settings", () =>
        HttpResponse.json({ detail: "Slow down." }, { status: 429 }),
      ),
    );
    const { user } = await openPage();
    await user.click(
      screen.getByRole("switch", { name: "Pause automatic applications" }),
    );
    expect(await screen.findByText("Slow down.")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "Pause automatic applications" }),
    ).not.toBeChecked();
  });

  it("is reached from the Settings item of the user menu", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    const user = userEvent.setup();
    const app = renderApp("/account");
    await user.click(
      await screen.findByRole("button", { name: /Account menu/ }),
    );
    await user.click(await screen.findByRole("menuitem", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Apply automatically from" });
    expect(app.location()).toBe(PATH);
  });

  it("uses no dash as a separator", async () => {
    fakeSettings({ paused: true });
    fakeCaps([robotics, northwind]);
    fakeBank([
      {
        question_key: "notice period",
        question: "Notice period",
        answer: "One month",
      },
    ]);
    const { user } = await openPage();
    await screen.findByText(/Notice period/);
    await user.click(screen.getByText("Advanced"));
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });
});

describe("the caps editor", () => {
  it("lists each cap as a sentence with what it counts now", async () => {
    fakeSettings();
    fakeCaps([robotics, northwind]);
    fakeBank([]);
    await openPage();
    const list = await screen.findByRole("list", { name: "Your caps" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent(
      "Example Robotics: at most 3 applications in 90 days, and 180 days before the same role",
    );
    expect(rows[0]).toHaveTextContent("1 of 3 used in the last 90 days");
    expect(rows[1]).toHaveTextContent(
      "Northwind Labs: at most 2 applications in 30 days",
    );
  });

  it("adds a cap from the keyboard with the revision it showed", async () => {
    fakeSettings();
    const api = fakeCaps([robotics]);
    fakeBank([]);
    const { user } = await openPage();
    await user.click(await screen.findByRole("button", { name: "Add a cap" }));
    const form = screen.getByRole("form", { name: "New cap" });
    expect(
      within(form).getByRole("textbox", { name: "Company" }),
    ).toHaveFocus();
    await user.keyboard("Northwind Labs");
    await user.tab();
    await user.keyboard("2");
    await user.tab();
    await user.keyboard("30{Enter}");
    expect(await screen.findByText("Cap added.")).toBeInTheDocument();
    expect(api.puts).toEqual([
      {
        items: [
          {
            label: "Example Robotics",
            max_count: 3,
            window_days: 90,
            same_role_days: 180,
          },
          {
            label: "Northwind Labs",
            max_count: 2,
            window_days: 30,
            same_role_days: null,
          },
        ],
        revision: revisionOf(1),
      },
    ]);
    expect(
      within(screen.getByRole("list", { name: "Your caps" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(2);
    expect(screen.queryByRole("form", { name: "New cap" })).toBe(null);
  });

  it("refuses a cap the api would refuse before any call", async () => {
    fakeSettings();
    const api = fakeCaps([]);
    fakeBank([]);
    const { user } = await openPage();
    await user.click(await screen.findByRole("button", { name: "Add a cap" }));
    const form = screen.getByRole("form", { name: "New cap" });
    await user.type(
      within(form).getByRole("textbox", { name: "Company" }),
      "!!!",
    );
    await user.type(
      within(form).getByRole("textbox", {
        name: "At most this many applications",
      }),
      "0",
    );
    await user.type(
      within(form).getByRole("textbox", { name: "In this many days" }),
      "3651",
    );
    await user.type(
      within(form).getByRole("textbox", {
        name: "Days before the same role again (optional)",
      }),
      "1.5",
    );
    await user.click(within(form).getByRole("button", { name: "Add cap" }));
    const company = within(form).getByRole("textbox", { name: "Company" });
    expect(company).toHaveAccessibleDescription(
      "Name the company with letters or digits.",
    );
    expect(company).toHaveFocus();
    expect(
      within(form).getByRole("textbox", {
        name: "At most this many applications",
      }),
    ).toHaveAccessibleDescription("Type a whole number from 1 to 100.");
    expect(
      within(form).getByRole("textbox", { name: "In this many days" }),
    ).toHaveAccessibleDescription(
      "Type a whole number of days from 1 to 3650.",
    );
    expect(api.puts).toEqual([]);
  });

  it("shows the 422 sentence of the api and keeps what was typed", async () => {
    fakeSettings();
    const api = fakeCaps([robotics]);
    fakeBank([]);
    api.next = {
      status: 422,
      body: {
        detail: "Two caps name the same company. Keep one of them.",
        code: "same_company",
      },
    };
    const { user } = await openPage();
    await user.click(await screen.findByRole("button", { name: "Add a cap" }));
    const form = screen.getByRole("form", { name: "New cap" });
    await user.keyboard("example robotics inc");
    await user.tab();
    await user.keyboard("1");
    await user.tab();
    await user.keyboard("10{Enter}");
    expect(
      await screen.findByText(
        "Two caps name the same company. Keep one of them.",
      ),
    ).toBeInTheDocument();
    expect(within(form).getByRole("textbox", { name: "Company" })).toHaveValue(
      "example robotics inc",
    );
  });

  it("reloads the caps on a 409 stale and says so, then saves on the current list", async () => {
    fakeSettings();
    const api = fakeCaps([robotics]);
    fakeBank([]);
    const { user } = await openPage();
    await screen.findByRole("list", { name: "Your caps" });
    // Another tab adds a cap: the stored list moves on.
    api.rows = [robotics, northwind];
    api.turn = 2;
    await user.click(
      screen.getByRole("button", { name: "Edit the cap of Example Robotics" }),
    );
    const form = screen.getByRole("form", {
      name: "Edit the cap of Example Robotics",
    });
    const most = within(form).getByRole("textbox", {
      name: "At most this many applications",
    });
    await user.clear(most);
    await user.type(most, "5");
    await user.click(within(form).getByRole("button", { name: "Save cap" }));
    expect(await screen.findByText(STALE)).toBeInTheDocument();
    expect(
      await screen.findByText(/Northwind Labs: at most 2 applications/),
    ).toBeInTheDocument();
    expect(api.gets).toBe(2);
    // The edit stays open with what was typed, and saves on the list now shown.
    expect(most).toHaveValue("5");
    await user.click(within(form).getByRole("button", { name: "Save cap" }));
    expect(await screen.findByText("Cap saved.")).toBeInTheDocument();
    expect(api.puts.at(-1)).toEqual({
      items: [
        {
          label: "Example Robotics",
          max_count: 5,
          window_days: 90,
          same_role_days: 180,
        },
        {
          label: "Northwind Labs",
          max_count: 2,
          window_days: 30,
          same_role_days: null,
        },
      ],
      revision: revisionOf(2),
    });
  });

  it("never keys a row on its id: a reload with new ids keeps an edit and its typed values", async () => {
    fakeSettings();
    const api = fakeCaps([robotics, northwind]);
    fakeBank([]);
    const { user, client } = await openPage();
    await screen.findByRole("list", { name: "Your caps" });
    await user.click(
      screen.getByRole("button", { name: "Edit the cap of Example Robotics" }),
    );
    let form = screen.getByRole("form", {
      name: "Edit the cap of Example Robotics",
    });
    let days = within(form).getByRole("textbox", { name: "In this many days" });
    await user.clear(days);
    await user.type(days, "60{Enter}");
    await waitFor(() => {
      expect(
        screen.queryByRole("form", {
          name: "Edit the cap of Example Robotics",
        }),
      ).toBe(null);
    });
    expect(api.puts).toHaveLength(1);
    // Every row now has a new id; the second edit works on the new ones.
    await user.click(
      screen.getByRole("button", { name: "Edit the cap of Northwind Labs" }),
    );
    form = screen.getByRole("form", { name: "Edit the cap of Northwind Labs" });
    days = within(form).getByRole("textbox", { name: "In this many days" });
    await user.clear(days);
    await user.type(days, "45");
    await client.invalidateQueries({ queryKey: capsKey });
    await waitFor(() => {
      expect(api.gets).toBe(2);
    });
    expect(
      within(
        screen.getByRole("form", { name: "Edit the cap of Northwind Labs" }),
      ).getByRole("textbox", { name: "In this many days" }),
    ).toHaveValue("45");
    await user.click(
      within(
        screen.getByRole("form", { name: "Edit the cap of Northwind Labs" }),
      ).getByRole("button", { name: "Save cap" }),
    );
    await waitFor(() => {
      expect(api.puts).toHaveLength(2);
    });
    expect(api.puts[1]?.items).toEqual([
      {
        label: "Example Robotics",
        max_count: 3,
        window_days: 60,
        same_role_days: 180,
      },
      {
        label: "Northwind Labs",
        max_count: 2,
        window_days: 45,
        same_role_days: null,
      },
    ]);
  });

  it("removes a cap only after the confirmation", async () => {
    fakeSettings();
    const api = fakeCaps([robotics, northwind]);
    fakeBank([]);
    const { user } = await openPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Remove the cap of Northwind Labs",
      }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Northwind Labs: at most 2 applications");
    await user.click(within(dialog).getByRole("button", { name: "Not yet" }));
    expect(api.puts).toEqual([]);
    await user.click(
      screen.getByRole("button", { name: "Remove the cap of Northwind Labs" }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Remove cap",
      }),
    );
    expect(await screen.findByText("Cap removed.")).toBeInTheDocument();
    expect(api.puts.at(-1)?.items.map((cap) => cap.label)).toEqual([
      "Example Robotics",
    ]);
  });
});

describe("the saved answers", () => {
  const entries: Entry[] = [
    {
      question_key: "notice period",
      question: "Notice period",
      answer: "One month",
    },
    { question_key: "pronouns", question: "Pronouns", answer: "they" },
  ];

  it("adds the first reusable answer with keyboard focus and returns focus after saving", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    const posts: unknown[] = [];
    server.use(
      http.post("*/api/apply/answer-bank", async ({ request }) => {
        const body = await request.json();
        posts.push(body);
        return HttpResponse.json(
          {
            items: [
              {
                question_key: "how did you hear about us",
                question: "How did you hear about us?",
                answer: "A colleague",
                used_count: 0,
                updated_at: "2026-10-23T08:00:00Z",
              },
            ],
          },
          { status: 201 },
        );
      }),
    );
    const { user } = await openPage();
    const add = await screen.findByRole("button", { name: "Add answer" });
    await user.click(add);
    const question = screen.getByRole("textbox", { name: "Question" });
    expect(question).toHaveFocus();
    expect(question).toHaveAttribute("maxLength", "200");
    await user.click(screen.getByRole("button", { name: "Save new answer" }));
    expect(question).toHaveAccessibleDescription(
      "Type the question as it appears on the form.",
    );
    expect(posts).toEqual([]);
    await user.type(question, "How did you hear about us?");
    await user.type(
      screen.getByRole("textbox", { name: "Reusable answer" }),
      "A colleague",
    );
    await user.click(screen.getByRole("button", { name: "Save new answer" }));
    expect(await screen.findByText("A colleague")).toBeInTheDocument();
    expect(posts).toEqual([
      { question: "How did you hear about us?", answer: "A colleague" },
    ]);
    expect(add).toHaveFocus();
  });

  it("keeps a duplicate draft, reloads the list and permits retry after an error", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank(entries);
    server.use(
      http.post("*/api/apply/answer-bank", () =>
        HttpResponse.json(
          {
            detail:
              "You already saved this question. Edit its answer in the list.",
            code: "saved_question_exists",
          },
          { status: 409 },
        ),
      ),
    );
    const { user } = await openPage();
    await user.click(await screen.findByRole("button", { name: "Add answer" }));
    await user.type(
      screen.getByRole("textbox", { name: "Question" }),
      "Notice period",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Reusable answer" }),
      "Two months",
    );
    await user.click(screen.getByRole("button", { name: "Save new answer" }));
    expect(
      await screen.findByText(
        "You already saved this question. Edit its answer in the list.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Reusable answer" }),
    ).toHaveValue("Two months");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Add answer" })).toHaveFocus();
  });

  it("protects the pending draft and keeps it available after a request failure", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/apply/answer-bank", async () => {
        await held;
        return HttpResponse.json(
          { detail: "The saved answer could not be added." },
          { status: 503 },
        );
      }),
    );
    const { user } = await openPage();
    await user.click(await screen.findByRole("button", { name: "Add answer" }));
    const question = screen.getByRole("textbox", { name: "Question" });
    const answer = screen.getByRole("textbox", { name: "Reusable answer" });
    await user.type(question, "An optional question?");
    await user.type(answer, "My reusable answer");
    await user.click(screen.getByRole("button", { name: "Save new answer" }));
    expect(question).toHaveAttribute("readonly");
    expect(answer).toHaveAttribute("readonly");
    expect(
      screen.getByRole("button", { name: "Save new answer" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    release();
    expect(
      await screen.findByText("The saved answer could not be added."),
    ).toBeInTheDocument();
    expect(question).toHaveValue("An optional question?");
    expect(answer).toHaveValue("My reusable answer");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Save new answer" }),
      ).toBeEnabled();
    });
  });

  it("changes one answer, and refuses an empty one before any call", async () => {
    fakeSettings();
    fakeCaps([]);
    const api = fakeBank(entries);
    const { user } = await openPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Edit the answer to Pronouns",
      }),
    );
    const box = screen.getByRole("textbox", { name: "Your answer" });
    expect(box).toHaveFocus();
    await user.clear(box);
    await user.type(box, "   ");
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    expect(box).toHaveAccessibleDescription(
      "Type an answer, or use Forget to remove it.",
    );
    expect(api.puts).toEqual([]);
    await user.clear(box);
    await user.type(box, "  she  ");
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    expect(await screen.findByText("Answer saved.")).toBeInTheDocument();
    expect(api.puts).toEqual([{ question_key: "pronouns", answer: "she" }]);
    expect(
      within(
        screen.getByRole("list", { name: "Your saved answers" }),
      ).getByText("she"),
    ).toBeInTheDocument();
  });

  it("forgets an answer only after the confirmation", async () => {
    fakeSettings();
    fakeCaps([]);
    const api = fakeBank(entries);
    const { user } = await openPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Forget the answer to Notice period",
      }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Forget answer",
      }),
    );
    expect(await screen.findByText("Answer forgotten.")).toBeInTheDocument();
    expect(api.puts).toEqual([{ question_key: "notice period", answer: null }]);
    expect(screen.queryByText("One month")).toBe(null);
  });

  it("reloads the list when the answer was forgotten elsewhere", async () => {
    fakeSettings();
    fakeCaps([]);
    const api = fakeBank(entries);
    const { user } = await openPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Edit the answer to Pronouns",
      }),
    );
    api.entries = api.entries.filter(
      (entry) => entry.question_key !== "pronouns",
    );
    await user.type(screen.getByRole("textbox", { name: "Your answer" }), "!");
    await user.click(screen.getByRole("button", { name: "Save answer" }));
    expect(
      await screen.findByText(/No such saved answer\. The list now shows/),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Edit the answer to Pronouns" }),
      ).toBe(null);
    });
    expect(api.gets).toBe(2);
  });

  it("says when nothing is saved", async () => {
    fakeSettings();
    fakeCaps([]);
    fakeBank([]);
    await openPage();
    expect(await screen.findByText("No saved answer yet.")).toBeInTheDocument();
  });
});

describe("the helpers", () => {
  it.each([
    ["0", 0],
    ["80", 80],
    [" 100 ", 100],
    ["101", null],
    ["", null],
    ["8.5", null],
    ["-1", null],
    ["1e2", null],
    ["0x10", null],
    ["٨٠", null],
    ["1000", null],
  ])("reads the fit %j as %j", (typed, expected) => {
    expect(readFit(typed)).toBe(expected);
  });

  const fields = {
    label: "Example Robotics",
    max_count: "3",
    window_days: "90",
    same_role_days: "",
  };

  it.each([
    [
      {},
      {
        label: "Example Robotics",
        max_count: 3,
        window_days: 90,
        same_role_days: null,
      },
    ],
    [
      { label: "  Example   Robotics  ", same_role_days: "180" },
      {
        label: "Example Robotics",
        max_count: 3,
        window_days: 90,
        same_role_days: 180,
      },
    ],
    [
      { label: "Ünïcode Söftware" },
      {
        label: "Ünïcode Söftware",
        max_count: 3,
        window_days: 90,
        same_role_days: null,
      },
    ],
    [
      { label: "42" },
      { label: "42", max_count: 3, window_days: 90, same_role_days: null },
    ],
    [
      { max_count: "100", window_days: "3650", same_role_days: "3650" },
      {
        label: "Example Robotics",
        max_count: 100,
        window_days: 3650,
        same_role_days: 3650,
      },
    ],
  ])("accepts the cap %j", (change, draft) => {
    expect(readCap({ ...fields, ...change })).toEqual({ draft });
  });

  it.each([
    [{ label: "" }, "label"],
    [{ label: "   " }, "label"],
    [{ label: "!!!" }, "label"],
    [{ label: "x".repeat(201) }, "label"],
    [{ max_count: "0" }, "max_count"],
    [{ max_count: "101" }, "max_count"],
    [{ max_count: "" }, "max_count"],
    [{ max_count: "2.5" }, "max_count"],
    [{ max_count: "-1" }, "max_count"],
    [{ window_days: "0" }, "window_days"],
    [{ window_days: "3651" }, "window_days"],
    [{ window_days: "1e3" }, "window_days"],
    [{ same_role_days: "0" }, "same_role_days"],
    [{ same_role_days: "3651" }, "same_role_days"],
    [{ same_role_days: "soon" }, "same_role_days"],
  ])("refuses the cap %j on its field %s", (change, field) => {
    const read = readCap({ ...fields, ...change });
    expect("problems" in read && Object.keys(read.problems)).toEqual([field]);
  });

  it("writes the sentence of a cap", () => {
    expect(
      capSentence({
        label: "Example Robotics",
        max_count: 1,
        window_days: 1,
        same_role_days: null,
      }),
    ).toBe("Example Robotics: at most 1 application in 1 day");
  });

  it.each([
    [900, "15 minutes"],
    [60, "1 minute"],
    [90, "90 seconds"],
  ])("writes a hold of %i s as %s", (seconds, text) => {
    expect(holdText(seconds)).toBe(text);
  });

  it.each([
    ["One month", "One month"],
    [true, "Yes"],
    [false, "No"],
    [null, ""],
    [3, "3"],
  ])("shows the saved answer %j as %j", (answer, text) => {
    expect(answerText(answer)).toBe(text);
  });
});
