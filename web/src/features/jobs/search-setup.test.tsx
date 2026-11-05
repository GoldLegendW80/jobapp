import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, it, vi } from "vitest";

import {
  EMPTY,
  interactiveSettingsSchema,
  type Search,
} from "@/features/profile/api";
import {
  fromSearchForm,
  searchSchema,
  toSearchForm,
} from "@/features/profile/editor-forms";
import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { phoneScreen } from "@/test/media";
import { fakeProfile, profileBody, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { PROTECTED, type Protected } from "./site-permissions";

beforeEach(() => {
  fakeSession(admin);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function open(
  search: Search = structuredClone(EMPTY.search),
  initial: Partial<Record<Protected, Partial<FakePermission>>> = {},
) {
  const profile = fakeProfile({ search }, 6);
  const permissions = permissionApi(profile, initial);
  fakeJobsApi([makeJob(1)]);
  // Browser B17: no website signed in yet (each website row has its Sign in).
  server.use(
    http.get("*/api/browser/sites", () => HttpResponse.json({ sites: [] })),
  );
  const starts: unknown[] = [];
  server.use(
    http.post("*/api/jobs/sweeps", async ({ request }) => {
      starts.push(await request.json());
      return HttpResponse.json(
        { detail: "Browser permission is required." },
        { status: 403 },
      );
    }),
  );
  const view = renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Find jobs" }));
  const dialog = await screen.findByRole("dialog", { name: "Find jobs" });
  return {
    profile,
    starts,
    user,
    dialog,
    writes: permissions.writes,
    states: permissions.states,
    client: view.client,
  };
}

const LABELS: Record<Protected, string> = {
  linkedin: "LinkedIn",
  welcome_to_the_jungle: "Welcome to the Jungle",
  indeed: "Indeed",
  freework: "Free-Work",
};
const READERS: readonly Protected[] = ["linkedin", "freework"];
const permissionPath = (kind: Protected) =>
  kind === "linkedin"
    ? "*/api/browser/linkedin"
    : `*/api/opportunities/platforms/${kind}`;

interface FakePermission {
  enabled: boolean;
  accepted: boolean;
  version: number;
  query: string | null;
  paused_until: string | null;
  /** The status GET answers instead of the state, when set. */
  fails: number | null;
}

/** The consent routes as the api answers them: a stale base revision or sentence version is a
 * 409, the reader fields are refused on Indeed and Welcome to the Jungle, each write moves the
 * profile revision, and LinkedIn and Free-Work keep their permission on a reader row. */
function permissionApi(
  profile: FakeProfile,
  initial: Partial<Record<Protected, Partial<FakePermission>>> = {},
) {
  const writes: { kind: Protected; body: Record<string, unknown> }[] = [];
  const states = Object.fromEntries(
    PROTECTED.map((kind) => [
      kind,
      {
        enabled: false,
        accepted: false,
        version: 4,
        query: "junior engineer",
        paused_until: null,
        fails: null,
        ...initial[kind],
      },
    ]),
  ) as Record<Protected, FakePermission>;
  for (const kind of PROTECTED) {
    const state = states[kind];
    const response = () => ({
      enabled: state.enabled,
      accepted: state.accepted,
      allowed: state.enabled && state.accepted && !state.paused_until,
      text: `Current ${kind} risk sentence, version ${state.version}.`,
      text_version: state.version,
      query: state.query,
      location: null,
      paused_until: state.paused_until,
      needs_signin: false,
    });
    server.use(
      http.get(permissionPath(kind), () =>
        state.fails
          ? HttpResponse.json(
              { detail: "Unavailable." },
              { status: state.fails },
            )
          : HttpResponse.json(response()),
      ),
      http.put(permissionPath(kind), async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        writes.push({ kind, body });
        if (body.base_revision !== profile.revision)
          return HttpResponse.json(
            { detail: "Your profile changed in another tab. Try again." },
            { status: 409 },
          );
        if (
          (body.accept_version !== undefined &&
            body.accept_version !== state.version) ||
          (body.enabled && !state.accepted && body.accept_version === undefined)
        )
          return HttpResponse.json(
            { detail: "The risk sentence changed: read it again." },
            { status: 409 },
          );
        // A copy: the bodies of the profile saves recorded before stay as they were sent.
        const search = structuredClone(profile.sections.search) as Search;
        profile.sections.search = search;
        if (READERS.includes(kind)) {
          if (body.enabled && !state.query && !body.query)
            return HttpResponse.json(
              { detail: "Type the words to search." },
              { status: 422 },
            );
          const rows = search.browser_sources ?? [];
          const existing = rows.find((row) => row.kind === kind);
          if (body.enabled) {
            state.query = state.query ?? String(body.query);
            search.browser_sources = [
              ...rows.filter((row) => row.kind !== kind),
              {
                kind: kind as "linkedin" | "freework",
                query: state.query,
                location: existing?.location ?? null,
                enabled: true,
                interactive_only:
                  existing?.enabled === true
                    ? existing.interactive_only
                    : body.interactive_only === true,
              },
            ];
          } else if (existing) existing.enabled = false;
        } else if (
          ["query", "location", "interactive_only"].some((key) => key in body)
        )
          return HttpResponse.json(
            { detail: "This platform consent does not accept reader intent." },
            { status: 422 },
          );
        state.enabled = body.enabled === true;
        if (body.accept_version === state.version) state.accepted = true;
        profile.revision += 1;
        return HttpResponse.json(response());
      }),
    );
  }
  return { writes, states };
}

function site(platform: Protected | "custom", url: string, enabled = true) {
  return { platform, url, enabled, mode: "llm_browser" as const };
}

const PRESET_URLS: Record<Protected, string> = {
  linkedin: "https://www.linkedin.com/",
  welcome_to_the_jungle: "https://www.welcometothejungle.com/en/jobs",
  indeed: "https://www.indeed.com/",
  freework: "https://www.free-work.com/",
};

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((ready) => {
    resolve = ready;
  });
  return { promise, resolve };
}

async function saveAndStart(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
) {
  await user.click(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  );
}

/** The list of addresses, once it has a line. */
function listOf(dialog: HTMLElement) {
  return within(dialog).getByRole("list", { name: "Your list" });
}

/** Whether a line named `name` is in the list (its Remove button is there). */
function inList(dialog: HTMLElement, name: string): boolean {
  const list = within(dialog).queryByRole("list", { name: "Your list" });
  return (
    list !== null &&
    within(list).queryByRole("button", { name: `Remove ${name}` }) !== null
  );
}

/** The line of the list named `name`. */
function rowOf(dialog: HTMLElement, name: string): HTMLElement {
  const button = within(listOf(dialog)).getByRole("button", {
    name: `Remove ${name}`,
  });
  const row = button.closest("li");
  if (!row) throw new Error(`no line for ${name}`);
  return row;
}

/** What a tick of the old box did: adds the website from its example, or removes its line. */
async function toggle(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  name: string,
) {
  if (inList(dialog, name)) {
    await user.click(
      within(listOf(dialog)).getByRole("button", { name: `Remove ${name}` }),
    );
    return;
  }
  await user.click(within(dialog).getByRole("button", { name: `Add ${name}` }));
}

/** Opens "More options" when folded. */
async function openMore(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
) {
  const more = within(dialog).getByRole("button", { name: "More options" });
  if (more.getAttribute("aria-expanded") !== "true") await user.click(more);
}

async function instructionsBox(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
) {
  await openMore(user, dialog);
  return within(dialog).getByRole("textbox", {
    name: "Instructions for the assistant",
  });
}

it("opens setup before any save or start and closes without saving", async () => {
  const { profile, starts, user, dialog } = await open();
  // An empty list, the examples of every kind, and what an address can be.
  expect(
    within(dialog).getByText(/Nothing yet\. Add an address above/),
  ).toBeVisible();
  for (const name of [
    "LinkedIn",
    "Welcome to the Jungle",
    "Indeed",
    "Free-Work",
    "Arbeitnow",
    "Doctolib on Ashby",
  ])
    expect(
      within(dialog).getByRole("button", { name: `Add ${name}` }),
    ).toBeVisible();
  expect(dialog).toHaveTextContent(
    "A company job board, like jobs.ashbyhq.com/doctolib",
  );
  expect(dialog).toHaveTextContent(
    "Any careers page, like https://www.example.com/careers",
  );
  await toggle(user, dialog, "Indeed");
  expect(inList(dialog, "Indeed")).toBe(true);
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(profile.saves).toEqual([]);
  expect(starts).toEqual([]);
  expect(screen.getByRole("button", { name: "Find jobs" })).toHaveFocus();
});

it("saves the reviewed sites and criteria before starting with the returned revision", async () => {
  const existing = structuredClone(EMPTY.search);
  existing.sweep_schedule.enabled = true;
  const { profile, starts, writes, user, dialog } = await open(existing);
  await toggle(user, dialog, "Free-Work");
  await user.type(
    await instructionsBox(user, dialog),
    "Read junior roles carefully.",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  );
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  const saved = profile.saves[0]!.body;
  expect(saved.base_revision).toBe(6);
  // The ticked Free-Work is its permission, written by its own route on the saved revision.
  expect(writes).toEqual([
    {
      kind: "freework",
      body: {
        enabled: true,
        accept_version: 4,
        interactive_only: true,
        base_revision: 7,
      },
    },
  ]);
  expect(saved.data).not.toHaveProperty("platform_consents");
  expect(saved.data).toMatchObject({
    sweep_schedule: existing.sweep_schedule,
    interactive_sites: [
      {
        platform: "freework",
        url: "https://www.free-work.com/",
        enabled: true,
        mode: "llm_browser",
      },
    ],
    interactive_instructions: "Read junior roles carefully.",
  });
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Browser permission is required.",
  );
  expect(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  ).toBeEnabled();
});

it("shows the four plain lists, the unwanted ones under More options, and saves the unwanted places", async () => {
  const { profile, starts, user, dialog } = await open();
  for (const label of ["Job titles", "Places"])
    expect(
      within(dialog).getByRole("textbox", { name: `New term for ${label}` }),
    ).toBeVisible();
  for (const label of ["Unwanted titles", "Unwanted places"])
    expect(
      within(dialog).queryByRole("textbox", { name: `New term for ${label}` }),
    ).toBeNull();
  await openMore(user, dialog);
  for (const label of ["Unwanted titles", "Unwanted places"])
    expect(
      within(dialog).getByRole("textbox", { name: `New term for ${label}` }),
    ).toBeVisible();
  // Words only, never a regular expression switch, folded or not (owner, 2026-10-28).
  expect(
    within(dialog).queryByRole("switch", { name: /^Regular expressions for/ }),
  ).toBeNull();
  expect(within(dialog).queryByText(/regular expression/i)).toBeNull();
  await user.type(
    within(dialog).getByRole("textbox", {
      name: "New term for Unwanted places",
    }),
    "Canada{enter}",
  );
  await toggle(user, dialog, "Free-Work");
  await saveAndStart(user, dialog);
  // Revision 7 is the save; Free-Work's permission (jobs J17) makes 8.
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    location_exclude: { advanced_regex: false, terms: ["Canada"] },
    title_include: { advanced_regex: false, terms: [] },
  });
});

it("saves the contract types ticked under More options, in the order shown, and unticks one", async () => {
  const { profile, starts, user, dialog } = await open();
  await openMore(user, dialog);
  const types = within(dialog).getByRole("group", { name: "Contract types" });
  for (const name of ["Freelance mission", "Permanent", "Fixed term"])
    await user.click(within(types).getByRole("checkbox", { name }));
  await user.click(within(types).getByText("Fixed term"));
  expect(
    within(types).getByRole("checkbox", { name: "Fixed term" }),
  ).not.toBeChecked();
  expect(
    within(types).getByRole("checkbox", { name: "Freelance mission" }),
  ).toBeChecked();
  await toggle(user, dialog, "Free-Work");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    contract_types: ["CDI", "Freelance"],
  });
});

it("keeps a conflicting draft editable and requires reviewing the current profile", async () => {
  const { profile, starts, user, dialog } = await open();
  profile.revision += 1;
  await toggle(user, dialog, "LinkedIn");
  await user.click(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  );
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("changed");
  expect(inList(dialog, "LinkedIn")).toBe(true);
  expect(starts).toEqual([]);
  expect(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  ).toBeDisabled();
  await user.click(
    within(dialog).getByRole("button", { name: "Load current settings" }),
  );
  expect(inList(dialog, "LinkedIn")).toBe(false);
});

it("does not save or start while loading the profile fails", async () => {
  server.use(
    http.get("*/api/profile", () =>
      HttpResponse.json({ detail: "Unavailable" }, { status: 503 }),
    ),
  );
  fakeJobsApi([makeJob(1)]);
  renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  await userEvent.click(screen.getByRole("button", { name: "Find jobs" }));
  const dialog = await screen.findByRole("dialog", { name: "Find jobs" });
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "could not",
  );
  expect(
    within(dialog).queryByRole("button", { name: "Save and start search" }),
  ).not.toBeInTheDocument();
});

it("leaves interactive fields out of the legacy Search editor for server preservation", () => {
  const search: Search = {
    ...EMPTY.search,
    interactive_sites: [
      {
        platform: "indeed",
        url: "https://www.indeed.com/",
        enabled: true,
        mode: "llm_browser",
      },
    ],
    interactive_instructions: "Read postings",
  };
  const saved = fromSearchForm(searchSchema.parse(toSearchForm(search)));
  expect(saved).not.toHaveProperty("interactive_sites");
  expect(saved).not.toHaveProperty("interactive_instructions");
});

it.each([
  "https://example.org",
  "http://example.org",
  "https://jobs.example.org/a",
  "https://example.org:8443/",
  "https://EXAMPLE.org/",
  "https://example.org./",
  "https://example.org/a?role=junior",
  "https://example.org/#jobs",
  "https://example.org/a%20b",
  "https://example.org/café",
  "https://exämple.org/",
  "https://a-b.example.org/",
  "https://www.linkedin.com/jobs/",
  "https://www.indeed.com/",
  "https://www.free-work.com/",
  "https://www.welcometothejungle.com/",
  "https://careers.example.org/engineer",
  "https://example.org:443/",
  "http://example.org:80/",
  "https://example.org/a/b?x=1&y=2",
])("accepts a public URL shape, subject to server validation: %s", (url) => {
  expect(
    interactiveSettingsSchema.safeParse({
      interactive_sites: [
        { platform: "custom", url, enabled: true, mode: "llm_browser" },
      ],
      interactive_instructions: "",
    }).success,
  ).toBe(true);
});

it.each([
  "",
  "example.org",
  "ftp://example.org/",
  "javascript:alert(1)",
  "data:text/html,test",
  "https://user@example.org/",
  "https://user:pass@example.org/",
  "https://127.0.0.1/",
  "https://10.0.0.1/",
  "https://[::1]/",
  "https://[::ffff:127.0.0.1]/",
  "https://localhost/",
  "https://jobs.local/",
  "https://jobs.internal/",
  "https://jobs.lan/",
  "https://jobs.home/",
  "https://jobs.onion/",
  "https://foo_bar.example.org/",
  "https://-jobs.example.org/",
  "https://example.org/" + "x".repeat(2000),
])("refuses an unsafe or oversized URL shape: %s", (url) => {
  expect(
    interactiveSettingsSchema.safeParse({
      interactive_sites: [
        { platform: "custom", url, enabled: true, mode: "llm_browser" },
      ],
      interactive_instructions: "",
    }).success,
  ).toBe(false);
});

it("bounds websites, canonical duplicate shapes and instructions", () => {
  const site = {
    platform: "custom",
    url: "https://example.org/",
    enabled: true,
    mode: "llm_browser",
  };
  const parse = (sites: unknown[], instructions = "") =>
    interactiveSettingsSchema.safeParse({
      interactive_sites: sites,
      interactive_instructions: instructions,
    });
  expect(
    parse([site, { ...site, url: "https://EXAMPLE.org:443/#jobs" }]).success,
  ).toBe(false);
  expect(
    parse(
      Array.from({ length: 11 }, (_, at) => ({
        ...site,
        url: `https://site${at}.example.org/`,
      })),
    ).success,
  ).toBe(false);
  expect(parse([], "x".repeat(2001)).success).toBe(false);
  expect(parse([], "x\u0000").success).toBe(false);
  expect(parse([], "  café\r\nremote  ")).toMatchObject({
    success: true,
    data: { interactive_instructions: "café\nremote" },
  });
});

it.each([422, 503])(
  "keeps the draft and sends no Start after failed profile save %s",
  async (status) => {
    const { starts, user, dialog } = await open();
    server.use(
      http.put("*/api/profile/search", () =>
        HttpResponse.json(
          { detail: "Search settings could not be saved." },
          { status },
        ),
      ),
    );
    await toggle(user, dialog, "Indeed");
    await user.click(
      within(dialog).getByRole("button", { name: "Save and start search" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "could not be saved",
    );
    expect(starts).toEqual([]);
    expect(inList(dialog, "Indeed")).toBe(true);
  },
);

it("reopens saved sites and instructions and refuses an address typed and not added", async () => {
  const settings: Search = {
    ...EMPTY.search,
    interactive_sites: [
      {
        platform: "custom",
        url: "https://careers.example.org/",
        enabled: true,
        mode: "llm_browser",
      },
    ],
    interactive_instructions: "Junior roles only.",
  };
  const { starts, user, dialog } = await open(settings);
  expect(inList(dialog, "careers.example.org")).toBe(true);
  expect(await instructionsBox(user, dialog)).toHaveValue("Junior roles only.");
  await user.type(
    within(dialog).getByRole("textbox", { name: "Website address" }),
    "https://another.example.org/",
  );
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Add the address you typed, or clear it, before saving.",
  );
  expect(starts).toEqual([]);
});

it("keeps a server-owned consent-only source out of the existing reader choices", async () => {
  const settings: Search = {
    ...EMPTY.search,
    browser_sources: [
      {
        kind: "linkedin",
        query: "junior engineer",
        location: null,
        enabled: true,
        interactive_only: true,
      },
    ],
  };
  const { dialog } = await open(settings);
  expect(
    within(dialog).queryByText("LinkedIn (existing reader)"),
  ).not.toBeInTheDocument();
});

it.each(["linkedin", "freework"] as const)(
  "keeps the protected %s reader saved as it is, unshown, on save",
  async (kind) => {
    const source = {
      kind,
      query: "junior engineer",
      location: null,
      enabled: true,
    };
    const settings: Search = { ...EMPTY.search, browser_sources: [source] };
    const { profile, starts, user, dialog } = await open(settings);
    const label = kind === "linkedin" ? "LinkedIn" : "Free-Work";
    expect(
      within(dialog).queryByText(`${label} (existing reader)`),
    ).not.toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Save and start search" }),
    );
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 7 }]);
    });
    expect(profile.saves[0]!.body.data).toMatchObject({
      browser_sources: [source],
    });
  },
);

// Jobs J17 (owner, 2026-10-27): ticking a job website and saving is the permission to search
// it, written by the platform's own consent route after the reviewed profile save.

const EASY_APPLY =
  "Adding LinkedIn also allows Easy Apply with your LinkedIn account. Each application still needs your approval before it is sent.";

it("shows each job website's current risk sentence in its line once added, and no separate permission step", async () => {
  const { user, dialog } = await open();
  for (const kind of PROTECTED) {
    await toggle(user, dialog, LABELS[kind]);
    const row = rowOf(dialog, LABELS[kind]);
    expect(
      await within(row).findByText(`Current ${kind} risk sentence, version 4.`),
    ).toBeVisible();
    if (kind === "linkedin") expect(row).toHaveTextContent(EASY_APPLY);
    else expect(row).not.toHaveTextContent(/Easy Apply/);
  }
  expect(
    within(dialog).queryByRole("button", {
      name: "Review website permissions",
    }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByRole("checkbox", { name: /I accept/ }),
  ).not.toBeInTheDocument();
  expect(dialog).not.toHaveTextContent(/[–—]| - /);
});

it("says in a LinkedIn line, and only there, that adding it also allows Easy Apply", async () => {
  const { dialog } = await open({
    ...EMPTY.search,
    interactive_sites: [
      site("custom", "https://fr.linkedin.com/jobs/"),
      site("custom", "https://fr.indeed.com/emplois"),
      site("custom", "https://careers.example.org/"),
    ],
  });
  await within(dialog).findAllByText(
    "Current linkedin risk sentence, version 4.",
  );
  expect(rowOf(dialog, "fr.linkedin.com/jobs")).toHaveTextContent(EASY_APPLY);
  for (const name of ["fr.indeed.com/emplois", "careers.example.org"])
    expect(rowOf(dialog, name)).not.toHaveTextContent(/Easy Apply/);
  // An address of hers at LinkedIn is LinkedIn: the example is not offered again.
  expect(
    within(dialog).queryByRole("button", { name: "Add LinkedIn" }),
  ).toBeNull();
  expect(dialog).not.toHaveTextContent(/[–—]| - /);
});

it.each(PROTECTED)(
  "records the %s permission of a website added to the list after the reviewed save",
  async (kind) => {
    const { profile, starts, writes, user, dialog } = await open();
    await toggle(user, dialog, LABELS[kind]);
    await within(dialog).findByText(
      `Current ${kind} risk sentence, version 4.`,
    );
    expect(dialog).toHaveTextContent(
      "Saving with this website in your list gives this permission.",
    );
    expect(writes).toEqual([]);
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 8 }]);
    });
    expect(profile.saves).toHaveLength(1);
    expect(profile.saves[0]!.body.base_revision).toBe(6);
    expect(profile.saves[0]!.body.data).toMatchObject({
      interactive_sites: [site(kind, PRESET_URLS[kind])],
    });
    expect(profile.saves[0]!.body.data).not.toHaveProperty("platform_consents");
    expect(writes).toEqual([
      {
        kind,
        body: {
          enabled: true,
          accept_version: 4,
          ...(READERS.includes(kind) ? { interactive_only: true } : {}),
          base_revision: 7,
        },
      },
    ]);
  },
);

it.each(PROTECTED)(
  "takes the %s permission back when its line is removed and saved",
  async (kind) => {
    const { starts, writes, user, dialog } = await open(
      { ...EMPTY.search, interactive_sites: [site(kind, PRESET_URLS[kind])] },
      { [kind]: { enabled: true, accepted: true } },
    );
    await within(dialog).findByText("Permission given.");
    await toggle(user, dialog, LABELS[kind]);
    expect(dialog).toHaveTextContent(
      `Saving takes back your permission to search ${LABELS[kind]}.`,
    );
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 8 }]);
    });
    expect(writes).toEqual([
      { kind, body: { enabled: false, base_revision: 7 } },
    ]);
  },
);

it("takes a permission back before it gives one", async () => {
  const { starts, writes, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [site("indeed", PRESET_URLS.indeed)],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  await within(dialog).findByText("Permission given.");
  await toggle(user, dialog, "Indeed");
  await toggle(user, dialog, "LinkedIn");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 9 }]);
  });
  expect(writes).toEqual([
    { kind: "indeed", body: { enabled: false, base_revision: 7 } },
    {
      kind: "linkedin",
      body: {
        enabled: true,
        accept_version: 4,
        interactive_only: true,
        base_revision: 8,
      },
    },
  ]);
});

it("leaves Indeed taken back and LinkedIn off when LinkedIn's grant after it fails", async () => {
  const { starts, writes, states, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [site("indeed", PRESET_URLS.indeed)],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  server.use(
    http.put("*/api/browser/linkedin", () =>
      HttpResponse.json({ detail: "Unavailable." }, { status: 503 }),
    ),
  );
  await within(dialog).findByText("Permission given.");
  await toggle(user, dialog, "Indeed");
  await toggle(user, dialog, "LinkedIn");
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The LinkedIn permission was not confirmed.",
  );
  expect(writes).toEqual([
    { kind: "indeed", body: { enabled: false, base_revision: 7 } },
  ]);
  expect(states.indeed.enabled).toBe(false);
  expect(states.linkedin.enabled).toBe(false);
  expect(starts).toEqual([]);
});

it("gives LinkedIn no permission when taking Indeed's back fails first", async () => {
  const { starts, writes, states, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [site("indeed", PRESET_URLS.indeed)],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  const failed: unknown[] = [];
  server.use(
    http.put(permissionPath("indeed"), async ({ request }) => {
      failed.push(await request.json());
      return HttpResponse.json({ detail: "Unavailable." }, { status: 503 });
    }),
  );
  await within(dialog).findByText("Permission given.");
  await toggle(user, dialog, "Indeed");
  await toggle(user, dialog, "LinkedIn");
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The Indeed permission was not confirmed.",
  );
  expect(failed).toEqual([{ enabled: false, base_revision: 7 }]);
  expect(writes).toEqual([]);
  expect(states.linkedin.enabled).toBe(false);
  expect(starts).toEqual([]);
});

it("writes nothing for a ticked website whose permission is current", async () => {
  const { starts, writes, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [site("indeed", PRESET_URLS.indeed)],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  await within(dialog).findByText("Permission given.");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(writes).toEqual([]);
});

it.each(READERS)(
  "leaves a %s permission given for its reader alone when it was never in the list",
  async (kind) => {
    const { starts, writes, user, dialog } = await open(
      structuredClone(EMPTY.search),
      {
        [kind]: { enabled: true, accepted: true },
      },
    );
    expect(dialog).not.toHaveTextContent(/Saving takes back/);
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 7 }]);
    });
    expect(writes).toEqual([]);
  },
);

it("takes back a websites only LinkedIn permission when LinkedIn is not in the list", async () => {
  const { starts, writes, user, dialog } = await open(
    {
      ...EMPTY.search,
      browser_sources: [
        {
          kind: "linkedin",
          query: "junior engineer",
          location: null,
          enabled: true,
          interactive_only: true,
        },
      ],
    },
    { linkedin: { enabled: true, accepted: true } },
  );
  await within(dialog).findByText(
    "Saving takes back your permission to search LinkedIn.",
  );
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(writes).toEqual([
    { kind: "linkedin", body: { enabled: false, base_revision: 7 } },
  ]);
});

it.each(READERS)(
  "gives a new %s reader row the first title as its words",
  async (kind) => {
    const search = structuredClone(EMPTY.search);
    search.title_include.terms = ["Robot Engineer", "Roboticist"];
    const { starts, writes, user, dialog } = await open(search, {
      [kind]: { query: null },
    });
    await toggle(user, dialog, LABELS[kind]);
    await within(dialog).findByText(
      `Current ${kind} risk sentence, version 4.`,
    );
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 8 }]);
    });
    expect(writes[0]?.body).toEqual({
      enabled: true,
      accept_version: 4,
      interactive_only: true,
      query: "Robot Engineer",
      base_revision: 7,
    });
  },
);

it("refuses to save LinkedIn in the list without words for its reader row", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    structuredClone(EMPTY.search),
    { linkedin: { query: null } },
  );
  await toggle(user, dialog, "LinkedIn");
  await within(dialog).findByText("Current linkedin risk sentence, version 4.");
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "LinkedIn needs words to search: add a job title you want",
  );
  expect(profile.saves).toEqual([]);
  expect(writes).toEqual([]);
  expect(starts).toEqual([]);
});

it("asks to read a risk sentence that changed after it was shown, then gives the new version", async () => {
  const { profile, starts, writes, states, user, dialog } = await open();
  await toggle(user, dialog, "Indeed");
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  states.indeed.version = 5;
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The Indeed risk sentence in your list is new. Read it, then save again.",
  );
  expect(
    within(dialog).getByText("Current indeed risk sentence, version 5."),
  ).toBeVisible();
  expect(profile.saves).toEqual([]);
  expect(writes).toEqual([]);
  expect(starts).toEqual([]);
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(writes).toEqual([
    {
      kind: "indeed",
      body: { enabled: true, accept_version: 5, base_revision: 7 },
    },
  ]);
});

it("refuses a website in the list whose risk sentence cannot be loaded, and saves once it is removed", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    structuredClone(EMPTY.search),
    { welcome_to_the_jungle: { fails: 503 } },
  );
  await toggle(user, dialog, "Welcome to the Jungle");
  expect(
    await within(rowOf(dialog, "Welcome to the Jungle")).findByText(
      "The Welcome to the Jungle risk sentence could not be loaded, so its permission cannot be given now.",
    ),
  ).toBeVisible();
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Remove Welcome to the Jungle from your list or try again later.",
  );
  expect(profile.saves).toEqual([]);
  expect(starts).toEqual([]);
  await toggle(user, dialog, "Welcome to the Jungle");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(writes).toEqual([]);
});

it("makes a stale consent revision a conflict, never a silent retry", async () => {
  const { profile, starts, writes, user, dialog } = await open();
  await toggle(user, dialog, "Indeed");
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  server.use(
    http.put("*/api/profile/search", async ({ request }) => {
      const body = (await request.json()) as {
        base_revision: number;
        data: Search;
      };
      profile.saves.push({ section: "search", body });
      profile.sections.search = body.data;
      profile.revision += 1;
      const answer = profileBody(profile);
      profile.revision += 1; // another tab saved right after this save
      return HttpResponse.json(answer);
    }),
  );
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The Indeed permission or its risk sentence changed in another tab.",
  );
  expect(writes).toHaveLength(1);
  expect(starts).toEqual([]);
  expect(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  ).toBeDisabled();
  expect(
    within(dialog).getByRole("button", { name: "Load current settings" }),
  ).toBeVisible();
  expect(inList(dialog, "Indeed")).toBe(true);
});

const OTHER_TAB_EDITS: [string, (search: Search) => void][] = [
  [
    "recency",
    (search) => {
      search.days = 7;
    },
  ],
  [
    "schedule",
    (search) => {
      search.sweep_schedule.enabled = true;
    },
  ],
  [
    "interactive instructions",
    (search) => {
      search.interactive_instructions = "Newer tab instructions.";
    },
  ],
  [
    "interactive website",
    (search) => {
      search.interactive_sites = [site("custom", "https://newer.example.org/")];
    },
  ],
  [
    "compensation",
    (search) => {
      search.compensation.salary = { min: 40000, max: 50000, currency: "EUR" };
    },
  ],
];

it.each(OTHER_TAB_EDITS)(
  "keeps the draft and starts nothing when %s changes in another tab while a permission is saved",
  async (_name, edit) => {
    const { profile, starts, writes, user, dialog } = await open();
    await toggle(user, dialog, "Indeed");
    await within(dialog).findByText("Current indeed risk sentence, version 4.");
    const instructions = await instructionsBox(user, dialog);
    await user.type(instructions, "Keep my draft.");
    const entered = deferred();
    const release = deferred();
    server.use(
      http.get("*/api/profile", async () => {
        entered.resolve();
        await release.promise;
        return HttpResponse.json(profileBody(profile));
      }),
    );
    await saveAndStart(user, dialog);
    await entered.promise;
    expect(writes).toHaveLength(1);
    edit(profile.sections.search as Search);
    profile.revision += 1;
    release.resolve();
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Your search settings changed in another tab while a website permission was saved.",
    );
    expect(instructions).toHaveValue("Keep my draft.");
    expect(inList(dialog, "Indeed")).toBe(true);
    expect(
      within(dialog).getByRole("button", { name: "Save and start search" }),
    ).toBeDisabled();
    expect(starts).toEqual([]);
  },
);

it.each(["network", "malformed shape", "server error"])(
  "blocks Start after an unconfirmed %s consent answer",
  async (fault) => {
    const { profile, starts, user, dialog } = await open();
    await toggle(user, dialog, "Indeed");
    await within(dialog).findByText("Current indeed risk sentence, version 4.");
    server.use(
      http.put(permissionPath("indeed"), () => {
        profile.revision += 1; // it may have committed before its answer was lost
        if (fault === "network") return HttpResponse.error();
        if (fault === "server error")
          return HttpResponse.json({ detail: "Failed." }, { status: 502 });
        return HttpResponse.json({ enabled: true });
      }),
    );
    await saveAndStart(user, dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The Indeed permission was not confirmed.",
    );
    expect(
      within(dialog).getByRole("button", { name: "Save and start search" }),
    ).toBeDisabled();
    expect(starts).toEqual([]);
  },
);

it("keeps the draft editable after a consent route refuses its body", async () => {
  const { starts, user, dialog } = await open();
  await toggle(user, dialog, "Indeed");
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  server.use(
    http.put(permissionPath("indeed"), () =>
      HttpResponse.json(
        {
          detail:
            "Your search settings do not validate. Fix them in your profile first.",
        },
        { status: 422 },
      ),
    ),
  );
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The Indeed permission was not saved. Your search settings do not validate.",
  );
  expect(starts).toEqual([]);
  expect(
    within(dialog).getByRole("button", { name: "Save and start search" }),
  ).toBeEnabled();
});

it("writes no permission when the reviewed profile save is stale", async () => {
  const { profile, starts, writes, user, dialog } = await open();
  await toggle(user, dialog, "Indeed");
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  profile.revision += 1;
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Your profile changed in another tab.",
  );
  expect(writes).toEqual([]);
  expect(starts).toEqual([]);
});

it("gives the platform's permission for a website of her own at its address, once for two lines", async () => {
  const { profile, starts, writes, user, dialog } = await open();
  await toggle(user, dialog, "Indeed");
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  const address = within(dialog).getByRole("textbox", {
    name: "Website address",
  });
  await user.type(address, "https://fr.indeed.com/emplois");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  const row = rowOf(dialog, "fr.indeed.com/emplois");
  expect(row).toHaveTextContent(
    "This address is Indeed's: its search needs Indeed's permission.",
  );
  expect(row).toHaveTextContent("Current indeed risk sentence, version 4.");
  // An address of no job website has no sentence and needs no permission.
  await user.type(address, "https://careers.example.org/");
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  expect(rowOf(dialog, "careers.example.org")).not.toHaveTextContent(
    /risk sentence/,
  );
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(profile.saves).toHaveLength(1);
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [
      site("indeed", PRESET_URLS.indeed),
      site("custom", "https://fr.indeed.com/emplois"),
      site("custom", "https://careers.example.org/"),
    ],
  });
  expect(writes).toEqual([
    {
      kind: "indeed",
      body: { enabled: true, accept_version: 4, base_revision: 7 },
    },
  ]);
});

// Her own addresses at each job website, in the shapes the server reads as that website.
const OWN_ADDRESSES: [Protected, string, string][] = [
  ["linkedin", "https://fr.linkedin.com/jobs/", "https://lnkd.in/jobs"],
  [
    "welcome_to_the_jungle",
    "https://app.welcometothejungle.com/fr/jobs",
    "https://wttj.co/jobs",
  ],
  ["indeed", "https://fr.indeed.com/", "https://indeed.fr/emplois"],
  ["freework", "https://fr.free-work.com/jobs", "https://www.freework.fr/"],
];

it("removes her own Indeed address and takes the permission back", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [
        site("custom", "https://fr.indeed.com/"),
        site("custom", "https://careers.example.org/"),
      ],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  await within(dialog).findAllByText(/Permission given\./);
  await toggle(user, dialog, "fr.indeed.com");
  expect(inList(dialog, "fr.indeed.com")).toBe(false);
  expect(inList(dialog, "careers.example.org")).toBe(true);
  expect(dialog).toHaveTextContent(
    "Saving takes back your permission to search Indeed.",
  );
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [site("custom", "https://careers.example.org/")],
  });
  expect(writes).toEqual([
    { kind: "indeed", body: { enabled: false, base_revision: 7 } },
  ]);
});

it.each(OWN_ADDRESSES)(
  "keeps the %s permission while an address of hers there is listed, and takes it back once none is: %s, %s",
  async (kind, first, second) => {
    const short = (url: string) =>
      url.replace(/^https:\/\/(www\.)?/, "").replace(/\/$/, "");
    const { profile, starts, writes, user, dialog } = await open(
      {
        ...EMPTY.search,
        interactive_sites: [
          site(kind, PRESET_URLS[kind]),
          site("custom", first),
          site("custom", second),
          site("custom", "https://careers.example.org/"),
        ],
      },
      { [kind]: { enabled: true, accepted: true } },
    );
    await within(dialog).findAllByText(/Permission given\./);
    await toggle(user, dialog, LABELS[kind]);
    expect(dialog).not.toHaveTextContent(/Saving takes back/);
    await toggle(user, dialog, short(first));
    expect(dialog).not.toHaveTextContent(/Saving takes back/);
    await toggle(user, dialog, short(second));
    expect(dialog).toHaveTextContent(
      `Saving takes back your permission to search ${LABELS[kind]}.`,
    );
    expect(inList(dialog, "careers.example.org")).toBe(true);
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 8 }]);
    });
    expect(profile.saves[0]!.body.data).toMatchObject({
      interactive_sites: [site("custom", "https://careers.example.org/")],
    });
    expect(writes).toEqual([
      { kind, body: { enabled: false, base_revision: 7 } },
    ]);
  },
);

it("offers Indeed again once her own Indeed address is removed, and adds www.indeed.com", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [site("custom", "https://fr.indeed.com/")],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  await within(dialog).findAllByText(/Permission given\./);
  expect(
    within(dialog).queryByRole("button", { name: "Add Indeed" }),
  ).toBeNull();
  await toggle(user, dialog, "fr.indeed.com");
  await toggle(user, dialog, "Indeed");
  expect(inList(dialog, "Indeed")).toBe(true);
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [site("indeed", PRESET_URLS.indeed)],
  });
  expect(writes).toEqual([]);
});

it("removes a website of her own, and searches a paused one again on request", async () => {
  const { profile, starts, user, dialog } = await open({
    ...EMPTY.search,
    interactive_sites: [
      site("custom", "https://careers.example.org/"),
      site("custom", "https://jobs.example.net/", false),
    ],
  });
  const paused = rowOf(dialog, "jobs.example.net");
  expect(paused).toHaveTextContent("Paused");
  await user.click(
    within(paused).getByRole("button", { name: "Search it again" }),
  );
  expect(rowOf(dialog, "jobs.example.net")).not.toHaveTextContent("Paused");
  await toggle(user, dialog, "careers.example.org");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toHaveLength(1);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [site("custom", "https://jobs.example.net/")],
  });
});

// Owner, 2026-10-28: the setup is the websites and the criteria. Boards, feeds and fixed readers
// are gone: a profile read before the deletion shows none and sends none back; there is no
// "Websites only" choice.
it("shows no other search source, no Websites only and no link, and sends none back", async () => {
  const board = {
    company: "Example Robotics",
    ats: "ashby" as const,
    org: "example-robotics",
    enabled: true,
    extra_exclude: { terms: [], advanced_regex: false },
  };
  const feed = {
    kind: "remoteok" as const,
    terms: { terms: [], advanced_regex: false },
    enabled: true,
  };
  const reader = {
    kind: "google_careers" as const,
    query: "robotics",
    location: null,
    enabled: true,
  };
  const { profile, starts, user, dialog } = await open({
    ...EMPTY.search,
    boards: [board],
    feeds: [feed],
    browser_sources: [reader],
    interactive_sites: [site("custom", "https://careers.example.org/")],
  } as unknown as Search);
  expect(
    within(dialog).queryByRole("region", { name: "Other search sources" }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole("checkbox", { name: "Websites only" }),
  ).toBeNull();
  for (const text of [/API board/, /\(feed\)/, /fixed reader/, /Remote OK/])
    expect(within(dialog).queryByText(text)).toBeNull();
  // No link at all: the old boards' page is gone and no feed asks for a key here.
  expect(within(dialog).queryAllByRole("link")).toEqual([]);
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  const sent = profile.saves[0]!.body.data as Record<string, unknown>;
  expect(sent).not.toHaveProperty("boards");
  expect(sent).not.toHaveProperty("feeds");
  expect(sent.browser_sources ?? []).toEqual([]);
});

it("shows no alert e-mails and keeps the stored ones as they are", async () => {
  const { profile, starts, user, dialog } = await open({
    ...structuredClone(EMPTY.search),
    alert_emails: { indeed: false, linkedin: true },
  });
  expect(
    within(dialog).queryByRole("region", { name: "Alert e-mails" }),
  ).toBeNull();
  expect(within(dialog).queryByText(/alert e-mails/i)).toBeNull();
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  // Left out of the save, so the server keeps them (schemas.py KEPT_WHEN_OMITTED).
  expect(profile.saves[0]!.body.data).not.toHaveProperty("alert_emails");
});

// --- job feeds (api app/jobs/feeds, owner 2026-10-30) ------------------------------------------

it("lists job feeds like any address and saves them with the search", async () => {
  const { profile, starts, user, dialog } = await open({
    ...structuredClone(EMPTY.search),
    job_feeds: ["himalayas"],
  });
  const himalayas = rowOf(dialog, "Himalayas");
  expect(himalayas).toHaveTextContent(
    "Remote jobs open to France, read through its official feed",
  );
  expect(inList(dialog, "Arbeitnow")).toBe(false);
  await toggle(user, dialog, "Arbeitnow");
  // A feed's own website is the feed: typed, Adzuna joins the list.
  await user.type(
    within(dialog).getByRole("textbox", { name: "Website address" }),
    "adzuna.fr",
  );
  await user.click(within(dialog).getByRole("button", { name: "Add" }));
  const adzuna = rowOf(dialog, "Adzuna");
  // No key stored: the line says so, and where to store one.
  expect(
    await within(adzuna).findByText(/Adzuna needs your own free key/),
  ).toBeVisible();
  expect(
    within(adzuna).getByRole("link", { name: "Add it in Account" }),
  ).toHaveAttribute("href", "/account#adzuna-key");
  await toggle(user, dialog, "Himalayas");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  const sent = profile.saves[0]!.body.data as Record<string, unknown>;
  expect(sent.job_feeds).toEqual(["arbeitnow", "adzuna"]);
});

it("says nothing about the key once the Adzuna key is stored", async () => {
  server.use(
    http.get("*/api/jobs/feeds", () =>
      HttpResponse.json({
        feeds: [
          { name: "arbeitnow", needs_key: false },
          { name: "himalayas", needs_key: false },
          {
            name: "adzuna",
            needs_key: true,
            key_stored: true,
            key_last4: "k042",
            key_saved_at: "2026-10-30T10:00:00Z",
          },
        ],
        keys_available: true,
      }),
    ),
  );
  const { dialog } = await open({
    ...structuredClone(EMPTY.search),
    job_feeds: ["adzuna"],
  });
  const adzuna = rowOf(dialog, "Adzuna");
  await waitFor(() => {
    expect(adzuna).toHaveTextContent(
      "Jobs in France, read through its official feed",
    );
  });
  await waitFor(() => {
    expect(within(adzuna).queryByText(/needs your own free key/)).toBeNull();
  });
});

it("saves no job feed when none is ticked", async () => {
  const { profile, starts, user, dialog } = await open({
    ...structuredClone(EMPTY.search),
    interactive_sites: [site("custom", "https://careers.example.org/")],
  });
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  const sent = profile.saves[0]!.body.data as Record<string, unknown>;
  expect(sent.job_feeds).toEqual([]);
});

// LinkedIn searched through its public job search or the browser (owner, 2026-10-30: "Both,
// switchable in setup"); Easy Apply uses the browser either way.
const LINKEDIN_API = "Without signing in, through LinkedIn's public job search";
const LINKEDIN_BROWSER = "Signed in, in your own browser";
const LINKEDIN_TICKED: Search = {
  ...EMPTY.search,
  interactive_sites: [site("linkedin", PRESET_URLS.linkedin)],
};

it("shows LinkedIn's search choice only while LinkedIn is in the list, the public job search first and chosen", async () => {
  const { user, dialog } = await open(structuredClone(EMPTY.search), {
    linkedin: { enabled: true, accepted: true },
  });
  expect(
    within(dialog).queryByRole("radio", { name: LINKEDIN_API }),
  ).not.toBeInTheDocument();
  await toggle(user, dialog, "LinkedIn");
  const choice = within(dialog).getByRole("group", {
    name: "How LinkedIn is searched",
  });
  const radios = within(choice).getAllByRole("radio");
  expect(radios.map((radio) => radio.getAttribute("value"))).toEqual([
    "api",
    "browser",
  ]);
  expect(
    within(choice).getByRole("radio", { name: LINKEDIN_API }),
  ).toBeChecked();
  expect(
    within(choice).getByRole("radio", { name: LINKEDIN_BROWSER }),
  ).not.toBeChecked();
  expect(choice).toHaveTextContent("Easy Apply always uses your browser.");
  // No dash as a separator in what the person reads.
  expect(choice.textContent).not.toMatch(/[–—]| - /);
  await toggle(user, dialog, "LinkedIn");
  expect(
    within(dialog).queryByRole("group", { name: "How LinkedIn is searched" }),
  ).not.toBeInTheDocument();
});

it("saves the browser choice for LinkedIn's search", async () => {
  const { profile, starts, user, dialog } = await open(
    structuredClone(LINKEDIN_TICKED),
    { linkedin: { enabled: true, accepted: true } },
  );
  await user.click(
    within(dialog).getByRole("radio", { name: LINKEDIN_BROWSER }),
  );
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    linkedin_search: "browser",
  });
});

it("reopens a saved browser choice as saved", async () => {
  const { dialog } = await open(
    { ...structuredClone(LINKEDIN_TICKED), linkedin_search: "browser" },
    { linkedin: { enabled: true, accepted: true } },
  );
  expect(
    within(dialog).getByRole("radio", { name: LINKEDIN_BROWSER }),
  ).toBeChecked();
  expect(
    within(dialog).getByRole("radio", { name: LINKEDIN_API }),
  ).not.toBeChecked();
});

// The release before the choice refuses the key (a rollback would break the person's profile),
// so the default is not sent for a search that never stored it; the server keeps what is left out.
it("sends no choice for a search saved before it existed and left on the public job search", async () => {
  const { profile, starts, user, dialog } = await open(
    structuredClone(LINKEDIN_TICKED),
    { linkedin: { enabled: true, accepted: true } },
  );
  expect(
    within(dialog).getByRole("radio", { name: LINKEDIN_API }),
  ).toBeChecked();
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).not.toHaveProperty("linkedin_search");
});

it("sends the public job search back once a choice is stored", async () => {
  const { profile, starts, user, dialog } = await open(
    { ...structuredClone(LINKEDIN_TICKED), linkedin_search: "browser" },
    { linkedin: { enabled: true, accepted: true } },
  );
  await user.click(within(dialog).getByRole("radio", { name: LINKEDIN_API }));
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    linkedin_search: "api",
  });
});

it("keeps sending a stored public job search choice", async () => {
  const { profile, starts, user, dialog } = await open(
    { ...structuredClone(LINKEDIN_TICKED), linkedin_search: "api" },
    { linkedin: { enabled: true, accepted: true } },
  );
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    linkedin_search: "api",
  });
});

it("offers Sign in as optional on a website's line, and none on LinkedIn read without signing in", async () => {
  const { user, dialog } = await open(structuredClone(EMPTY.search), {
    linkedin: { enabled: true, accepted: true },
  });
  await toggle(user, dialog, "Indeed");
  const indeed = rowOf(dialog, "Indeed");
  const signIn = within(indeed).getByRole("button", {
    name: "Sign in to www.indeed.com (optional)",
  });
  expect(signIn).toHaveTextContent("Sign in (optional)");
  expect(signIn).toHaveAccessibleDescription(
    "Only if this site shows more postings to members.",
  );
  // LinkedIn read through its public job search never opens the browser: no Sign in on its line.
  await toggle(user, dialog, "LinkedIn");
  const linkedin = rowOf(dialog, "LinkedIn");
  expect(
    within(linkedin).queryByRole("button", { name: /^Sign in/ }),
  ).not.toBeInTheDocument();
  await user.click(
    within(linkedin).getByRole("radio", { name: LINKEDIN_BROWSER }),
  );
  expect(
    within(linkedin).getByRole("button", {
      name: "Sign in to www.linkedin.com (optional)",
    }),
  ).toBeVisible();
});

/** The scrolling middle of the setup, which its footer is outside of. */
const scroller = (element: HTMLElement) => element.closest(".overflow-y-auto");

it("opens as a dialog on a wide screen, its footer outside the part that scrolls", async () => {
  const { dialog } = await open();
  expect(dialog).toHaveAttribute("data-slot", "dialog-content");
  const save = within(dialog).getByRole("button", {
    name: "Save and start search",
  });
  expect(scroller(save)).toBeNull();
  expect(
    scroller(within(dialog).getByLabelText("Website address")),
  ).not.toBeNull();
});

it("opens as a sheet from the bottom on a phone, its footer in view too, and closes as the dialog does", async () => {
  phoneScreen();
  const { user, dialog, profile, starts } = await open();
  expect(dialog).toHaveAttribute("data-slot", "sheet-content");
  expect(
    within(dialog).getByRole("heading", { name: "Find jobs" }),
  ).toBeVisible();
  const save = within(dialog).getByRole("button", {
    name: "Save and start search",
  });
  expect(scroller(save)).toBeNull();
  expect(
    scroller(within(dialog).getByLabelText("Website address")),
  ).not.toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(profile.saves).toEqual([]);
  expect(starts).toEqual([]);
  expect(screen.getByRole("button", { name: "Find jobs" })).toHaveFocus();
});

it("What you want: a job title idea goes into the list in one click", async () => {
  server.use(
    http.get("*/api/profile/title-suggestions", () =>
      HttpResponse.json({
        status: "idle",
        needs: null,
        suggestions: [
          {
            title: "Analytics engineer",
            reason: "Your SQL and dbt work matches this role.",
            language: "en",
          },
        ],
        refreshed_at: "2026-11-05T09:00:00+00:00",
        error: null,
        asks_left: 3,
        asks_per_day: 3,
        estimate: null,
        cost_note: null,
      }),
    ),
  );
  const { dialog, user, profile } = await open();
  await user.click(
    await within(dialog).findByRole("button", {
      name: "Add Analytics engineer to Job titles",
    }),
  );
  expect(
    within(
      within(dialog).getByRole("list", { name: "Terms of Job titles" }),
    ).getByText("Analytics engineer"),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByRole("button", {
      name: "Add Analytics engineer to Job titles",
    }),
  ).toBeNull();
  expect(profile.saves).toEqual([]); // Save and start search stays the person's
});

it("What you want: without a model key, the ideas say where to add one, with no link out of the draft", async () => {
  const { dialog } = await open();
  expect(
    await within(dialog).findByText(
      "Job title ideas from your profile need a model key, added on the Account page.",
    ),
  ).toBeInTheDocument();
  expect(within(dialog).queryAllByRole("link")).toEqual([]);
});
