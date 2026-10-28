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
  const dialog = await screen.findByRole("dialog", { name: "Find jobs setup" });
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
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
}

it("opens setup before any save or start and closes without saving", async () => {
  const { profile, starts, user, dialog } = await open();
  for (const name of [
    "LinkedIn",
    "Welcome to the Jungle",
    "Indeed",
    "Free-Work",
  ])
    expect(within(dialog).getByRole("checkbox", { name })).not.toBeChecked();
  expect(dialog).toHaveTextContent("LLM with Playwright");
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(profile.saves).toEqual([]);
  expect(starts).toEqual([]);
  expect(screen.getByRole("button", { name: "Find jobs" })).toHaveFocus();
});

it("saves the reviewed sites and criteria before starting with the returned revision", async () => {
  const existing = structuredClone(EMPTY.search);
  existing.boards = [
    {
      company: "Example Robotics",
      ats: "lever",
      org: "example",
      enabled: true,
      extra_exclude: { advanced_regex: false, terms: [] },
    },
  ];
  existing.feeds = [
    {
      kind: "remoteok",
      enabled: true,
      terms: { advanced_regex: false, terms: ["engineering"] },
    },
  ];
  existing.browser_sources = [
    { kind: "vie", query: "engineer", location: "Paris", enabled: true },
  ];
  existing.sweep_schedule.enabled = true;
  const { profile, starts, writes, user, dialog } = await open(existing);
  await user.click(within(dialog).getByRole("checkbox", { name: "Free-Work" }));
  await user.type(
    within(dialog).getByRole("textbox", {
      name: "Instructions for this search",
    }),
    "Read junior roles carefully.",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
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
    boards: existing.boards,
    feeds: existing.feeds,
    browser_sources: existing.browser_sources,
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
    within(dialog).getByRole("button", { name: "Save and Start" }),
  ).toBeEnabled();
});

it("shows the four plain lists and saves the unwanted places before starting", async () => {
  const { profile, starts, user, dialog } = await open();
  for (const label of [
    "Job titles",
    "Unwanted titles",
    "Places",
    "Unwanted places",
  ])
    expect(
      within(dialog).getByRole("textbox", { name: `New term for ${label}` }),
    ).toBeVisible();
  // Words only, never a regular expression switch, folded or not (owner, 2026-10-28).
  expect(
    within(dialog).queryByRole("switch", { name: /^Regular expressions for/ }),
  ).toBeNull();
  expect(within(dialog).queryByText(/regular expression/i)).toBeNull();
  expect(
    within(dialog).queryByRole("switch", { name: /regular expressions\) for/ }),
  ).toBeNull();
  await user.type(
    within(dialog).getByRole("textbox", {
      name: "New term for Unwanted places",
    }),
    "Canada{enter}",
  );
  await user.click(within(dialog).getByRole("checkbox", { name: "Free-Work" }));
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
  // Revision 7 is the save; the ticked Free-Work's permission (jobs J17) makes 8.
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    location_exclude: { advanced_regex: false, terms: ["Canada"] },
    title_include: { advanced_regex: false, terms: [] },
  });
});

it("keeps a conflicting draft editable and requires reviewing the current profile", async () => {
  const { profile, starts, user, dialog } = await open();
  profile.revision += 1;
  await user.click(within(dialog).getByRole("checkbox", { name: "LinkedIn" }));
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("changed");
  expect(
    within(dialog).getByRole("checkbox", { name: "LinkedIn" }),
  ).toBeChecked();
  expect(starts).toEqual([]);
  expect(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  ).toBeDisabled();
  await user.click(
    within(dialog).getByRole("button", { name: "Load current settings" }),
  );
  expect(
    within(dialog).getByRole("checkbox", { name: "LinkedIn" }),
  ).not.toBeChecked();
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
  const dialog = await screen.findByRole("dialog", { name: "Find jobs setup" });
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "could not",
  );
  expect(
    within(dialog).queryByRole("button", { name: "Save and Start" }),
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
    await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
    await user.click(
      within(dialog).getByRole("button", { name: "Save and Start" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "could not be saved",
    );
    expect(starts).toEqual([]);
    expect(
      within(dialog).getByRole("checkbox", { name: "Indeed" }),
    ).toBeChecked();
  },
);

it("reopens saved sites and instructions and refuses an unadded custom draft", async () => {
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
  expect(
    within(dialog).getByText("https://careers.example.org/"),
  ).toBeVisible();
  expect(
    within(dialog).getByRole("textbox", {
      name: "Instructions for this search",
    }),
  ).toHaveValue("Junior roles only.");
  await user.type(
    within(dialog).getByRole("textbox", { name: "Custom website URL" }),
    "https://another.example.org/",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  );
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Add the custom website",
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
      within(dialog).getByRole("button", { name: "Save and Start" }),
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
  "Ticking LinkedIn also allows Easy Apply with your LinkedIn account. Each application still needs your approval before it is sent.";

it("shows each job website's current risk sentence beside its box and no separate permission step", async () => {
  const { dialog } = await open();
  for (const kind of PROTECTED) {
    const box = within(dialog).getByRole("checkbox", { name: LABELS[kind] });
    await waitFor(() => {
      expect(box).toHaveAccessibleDescription(
        kind === "linkedin"
          ? `Current ${kind} risk sentence, version 4. ${EASY_APPLY}`
          : `Current ${kind} risk sentence, version 4.`,
      );
    });
  }
  expect(
    within(dialog).queryByRole("button", {
      name: "Review website permissions",
    }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByRole("button", { name: /Enable .* browsing/ }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByRole("checkbox", { name: /I accept/ }),
  ).not.toBeInTheDocument();
  expect(dialog).not.toHaveTextContent(/[–—]| - /);
});

it("says beside LinkedIn's box, and only there, that ticking it also allows Easy Apply", async () => {
  const { dialog } = await open({
    ...EMPTY.search,
    interactive_sites: [
      site("custom", "https://fr.linkedin.com/jobs/"),
      site("custom", "https://fr.indeed.com/emplois"),
      site("custom", "https://careers.example.org/"),
    ],
  });
  const linkedin = within(dialog).getByRole("checkbox", { name: "LinkedIn" });
  // The line is there while the server's sentence loads, and once it is shown.
  expect(linkedin).toHaveAccessibleDescription(new RegExp(EASY_APPLY));
  await within(dialog).findAllByText(
    "Current linkedin risk sentence, version 4.",
  );
  for (const name of ["LinkedIn", "https://fr.linkedin.com/jobs/"])
    expect(
      within(dialog).getByRole("checkbox", { name }),
    ).toHaveAccessibleDescription(
      new RegExp(
        `^Current linkedin risk sentence, version 4\\. .*${EASY_APPLY}`,
      ),
    );
  for (const name of [
    "Welcome to the Jungle",
    "Indeed",
    "Free-Work",
    "https://fr.indeed.com/emplois",
    "https://careers.example.org/",
  ])
    expect(
      within(dialog).getByRole("checkbox", { name }),
    ).not.toHaveAccessibleDescription(/Easy Apply/);
  expect(dialog).not.toHaveTextContent(/[–—]| - /);
});

it.each(PROTECTED)(
  "records the %s permission of a ticked website after the reviewed save",
  async (kind) => {
    const { profile, starts, writes, user, dialog } = await open();
    const box = within(dialog).getByRole("checkbox", { name: LABELS[kind] });
    await within(dialog).findByText(
      `Current ${kind} risk sentence, version 4.`,
    );
    await user.click(box);
    expect(dialog).toHaveTextContent(
      "Saving with this box ticked gives this permission.",
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
  "takes the %s permission back when its ticked box is unticked and saved",
  async (kind) => {
    const { starts, writes, user, dialog } = await open(
      { ...EMPTY.search, interactive_sites: [site(kind, PRESET_URLS[kind])] },
      { [kind]: { enabled: true, accepted: true } },
    );
    await within(dialog).findByText("Permission given.");
    await user.click(
      within(dialog).getByRole("checkbox", { name: LABELS[kind] }),
    );
    expect(dialog).toHaveTextContent("Saving removes this permission.");
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
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  await user.click(within(dialog).getByRole("checkbox", { name: "LinkedIn" }));
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
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  await user.click(within(dialog).getByRole("checkbox", { name: "LinkedIn" }));
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
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  await user.click(within(dialog).getByRole("checkbox", { name: "LinkedIn" }));
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
  "leaves a %s permission given for its reader alone when its box was never ticked",
  async (kind) => {
    const { starts, writes, user, dialog } = await open(
      structuredClone(EMPTY.search),
      {
        [kind]: { enabled: true, accepted: true },
      },
    );
    await within(dialog).findByText(
      "Your permission given elsewhere stays on.",
    );
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 7 }]);
    });
    expect(writes).toEqual([]);
  },
);

it("takes back a websites only LinkedIn permission whose box is unticked", async () => {
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
  await within(dialog).findByText("Saving removes this permission.");
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
    await within(dialog).findByText(
      `Current ${kind} risk sentence, version 4.`,
    );
    await user.click(
      within(dialog).getByRole("checkbox", { name: LABELS[kind] }),
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

it("refuses to save a LinkedIn tick without words for its reader row", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    structuredClone(EMPTY.search),
    { linkedin: { query: null } },
  );
  await within(dialog).findByText("Current linkedin risk sentence, version 4.");
  await user.click(within(dialog).getByRole("checkbox", { name: "LinkedIn" }));
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "LinkedIn needs words to search: add a title to include",
  );
  expect(profile.saves).toEqual([]);
  expect(writes).toEqual([]);
  expect(starts).toEqual([]);
});

it("asks to read a risk sentence that changed after it was shown, then gives the new version", async () => {
  const { profile, starts, writes, states, user, dialog } = await open();
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  states.indeed.version = 5;
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The Indeed risk sentence beside its box is new. Read it, then Save and Start again.",
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

it("refuses a ticked website whose risk sentence cannot be loaded, and saves once it is unticked", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    structuredClone(EMPTY.search),
    { welcome_to_the_jungle: { fails: 503 } },
  );
  const box = within(dialog).getByRole("checkbox", {
    name: "Welcome to the Jungle",
  });
  await waitFor(() => {
    expect(box).toHaveAccessibleDescription(
      "The Welcome to the Jungle risk sentence could not be loaded, so its permission cannot be given now.",
    );
  });
  await user.click(box);
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Untick Welcome to the Jungle or try again later.",
  );
  expect(profile.saves).toEqual([]);
  expect(starts).toEqual([]);
  await user.click(box);
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(writes).toEqual([]);
});

it("makes a stale consent revision a conflict, never a silent retry", async () => {
  const { profile, starts, writes, user, dialog } = await open();
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
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
    within(dialog).getByRole("button", { name: "Save and Start" }),
  ).toBeDisabled();
  expect(
    within(dialog).getByRole("button", { name: "Load current settings" }),
  ).toBeVisible();
  expect(
    within(dialog).getByRole("checkbox", { name: "Indeed" }),
  ).toBeChecked();
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
    await within(dialog).findByText("Current indeed risk sentence, version 4.");
    await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
    const instructions = within(dialog).getByRole("textbox", {
      name: "Instructions for this search",
    });
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
    expect(
      within(dialog).getByRole("checkbox", { name: "Indeed" }),
    ).toBeChecked();
    expect(
      within(dialog).getByRole("button", { name: "Save and Start" }),
    ).toBeDisabled();
    expect(starts).toEqual([]);
  },
);

it.each(["network", "malformed shape", "server error"])(
  "blocks Start after an unconfirmed %s consent answer",
  async (fault) => {
    const { profile, starts, user, dialog } = await open();
    await within(dialog).findByText("Current indeed risk sentence, version 4.");
    await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
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
      within(dialog).getByRole("button", { name: "Save and Start" }),
    ).toBeDisabled();
    expect(starts).toEqual([]);
  },
);

it("keeps the draft editable after a consent route refuses its body", async () => {
  const { starts, user, dialog } = await open();
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
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
    within(dialog).getByRole("button", { name: "Save and Start" }),
  ).toBeEnabled();
});

it("writes no permission when the reviewed profile save is stale", async () => {
  const { profile, starts, writes, user, dialog } = await open();
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  profile.revision += 1;
  await saveAndStart(user, dialog);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Your profile changed in another tab.",
  );
  expect(writes).toEqual([]);
  expect(starts).toEqual([]);
});

it("gives the platform's permission for a website of her own at its address, once for two rows", async () => {
  const { profile, starts, writes, user, dialog } = await open();
  await within(dialog).findByText("Current indeed risk sentence, version 4.");
  await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
  await user.type(
    within(dialog).getByRole("textbox", { name: "Custom website URL" }),
    "https://fr.indeed.com/emplois",
  );
  await user.click(within(dialog).getByRole("button", { name: "Add website" }));
  const row = within(dialog)
    .getAllByRole("listitem")
    .find((item) => item.textContent.includes("https://fr.indeed.com/"));
  expect(row).toBeDefined();
  expect(row).toHaveTextContent(
    "This address is Indeed's: its search needs Indeed's permission. Saving with this box ticked gives this permission.",
  );
  expect(row).toHaveTextContent("Current indeed risk sentence, version 4.");
  // An address of no job website has no sentence and needs no permission.
  await user.type(
    within(dialog).getByRole("textbox", { name: "Custom website URL" }),
    "https://careers.example.org/",
  );
  await user.click(within(dialog).getByRole("button", { name: "Add website" }));
  expect(
    within(dialog).getByRole("checkbox", { name: "Indeed" }),
  ).toBeChecked();
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

it("unticks her own Indeed address with Indeed's box and takes the permission back", async () => {
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
  const box = within(dialog).getByRole("checkbox", { name: "Indeed" });
  const own = within(dialog).getByRole("checkbox", {
    name: "https://fr.indeed.com/",
  });
  expect(box).toBeChecked();
  expect(own).toBeChecked();
  await within(dialog).findAllByText(/Permission given\./);
  await user.click(box);
  expect(box).not.toBeChecked();
  expect(own).not.toBeChecked();
  expect(
    within(dialog).getByRole("checkbox", {
      name: "https://careers.example.org/",
    }),
  ).toBeChecked();
  expect(dialog).toHaveTextContent("Saving removes this permission.");
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 8 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [
      site("custom", "https://fr.indeed.com/", false),
      site("custom", "https://careers.example.org/"),
    ],
  });
  expect(writes).toEqual([
    { kind: "indeed", body: { enabled: false, base_revision: 7 } },
  ]);
});

it.each(OWN_ADDRESSES)(
  "unticks the %s row and every address of hers there with its box: %s, %s",
  async (kind, first, second) => {
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
    const box = within(dialog).getByRole("checkbox", { name: LABELS[kind] });
    await within(dialog).findAllByText(/Permission given\./);
    await user.click(box);
    expect(box).not.toBeChecked();
    for (const name of [first, second])
      expect(within(dialog).getByRole("checkbox", { name })).not.toBeChecked();
    expect(
      within(dialog).getByRole("checkbox", {
        name: "https://careers.example.org/",
      }),
    ).toBeChecked();
    await saveAndStart(user, dialog);
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 8 }]);
    });
    expect(profile.saves[0]!.body.data).toMatchObject({
      interactive_sites: [
        site("custom", first, false),
        site("custom", second, false),
        site("custom", "https://careers.example.org/"),
      ],
    });
    expect(writes).toEqual([
      { kind, body: { enabled: false, base_revision: 7 } },
    ]);
  },
);

it("adds www.indeed.com when Indeed's box is ticked again after unticking her own Indeed address", async () => {
  const { profile, starts, writes, user, dialog } = await open(
    {
      ...EMPTY.search,
      interactive_sites: [site("custom", "https://fr.indeed.com/")],
    },
    { indeed: { enabled: true, accepted: true } },
  );
  const box = within(dialog).getByRole("checkbox", { name: "Indeed" });
  await within(dialog).findAllByText(/Permission given\./);
  await user.click(box);
  expect(box).not.toBeChecked();
  await user.click(box);
  expect(box).toBeChecked();
  expect(
    within(dialog).getByRole("checkbox", { name: "https://fr.indeed.com/" }),
  ).not.toBeChecked();
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [
      site("custom", "https://fr.indeed.com/", false),
      site("indeed", PRESET_URLS.indeed),
    ],
  });
  expect(writes).toEqual([]);
});

it("turns a website of her own off and on with its box", async () => {
  const { profile, starts, user, dialog } = await open({
    ...EMPTY.search,
    interactive_sites: [site("custom", "https://careers.example.org/")],
  });
  const box = within(dialog).getByRole("checkbox", {
    name: "https://careers.example.org/",
  });
  expect(box).toBeChecked();
  await user.click(box);
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toHaveLength(1);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    interactive_sites: [site("custom", "https://careers.example.org/", false)],
  });
});

// Owner, 2026-10-28: the setup is the websites and the criteria. Boards, feeds and readers are
// neither shown nor searched, and stay saved as they are; there is no "Websites only" choice.
it("shows no other search source, no Websites only and no link, and keeps them saved", async () => {
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
  });
  expect(
    within(dialog).queryByRole("region", { name: "Other search sources" }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole("checkbox", { name: "Websites only" }),
  ).toBeNull();
  for (const text of [/API board/, /\(feed\)/, /fixed reader/, /Remote OK/])
    expect(within(dialog).queryByText(text)).toBeNull();
  expect(within(dialog).queryByRole("link")).toBeNull();
  await saveAndStart(user, dialog);
  await waitFor(() => {
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  expect(profile.saves[0]!.body.data).toMatchObject({
    boards: [board],
    feeds: [feed],
    browser_sources: [reader],
  });
});
