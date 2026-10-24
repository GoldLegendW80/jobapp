import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, it, vi } from "vitest";

import {
  EMPTY,
  getProfile,
  interactiveSettingsSchema,
  profileKey,
  type Profile,
  type Search,
} from "@/features/profile/api";
import {
  fromSearchForm,
  searchSchema,
  toSearchForm,
} from "@/features/profile/editor-forms";
import { admin, fakeSession } from "@/test/auth";
import { waitForOnFakeClock } from "@/test/clock";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { fakeProfile, profileBody, type FakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { DEFAULT_TIMEOUT_MS } from "@/lib/api";

beforeEach(() => {
  fakeSession(admin);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function open(search: Search = structuredClone(EMPTY.search)) {
  const profile = fakeProfile({ search }, 6);
  fakeJobsApi([makeJob(1)]);
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
    client: view.client,
    router: view.router,
  };
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
  const { profile, starts, user, dialog } = await open(existing);
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
    expect(starts).toEqual([{ profile_revision: 7 }]);
  });
  const saved = profile.saves[0]!.body;
  expect(saved.base_revision).toBe(6);
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
  "keeps the protected %s reader state read-only on generic save",
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
    const control = within(dialog).getByRole("checkbox", {
      name: `${label} (existing reader)`,
    });
    expect(control).toBeChecked();
    expect(control).toBeDisabled();
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

const PERMISSIONS = [
  ["linkedin", "LinkedIn"],
  ["freework", "Free-Work"],
  ["indeed", "Indeed"],
  ["welcome_to_the_jungle", "Welcome to the Jungle"],
] as const;
type PermissionKind = (typeof PERMISSIONS)[number][0];
const permissionPath = (kind: PermissionKind) =>
  kind === "linkedin"
    ? "*/api/browser/linkedin"
    : `*/api/opportunities/platforms/${kind}`;

function permissionApi(profile: FakeProfile, initiallyEnabled = false) {
  const writes: { kind: PermissionKind; body: Record<string, unknown> }[] = [];
  for (const [kind] of PERMISSIONS) {
    let enabled = initiallyEnabled;
    let accepted = initiallyEnabled;
    const response = () => ({
      enabled,
      accepted,
      allowed: enabled && accepted,
      text: `Current ${kind} risk sentence.`,
      text_version: 4,
      query: "junior engineer",
      location: null,
      paused_until: null,
      needs_signin: false,
    });
    server.use(
      http.get(permissionPath(kind), () => HttpResponse.json(response())),
      http.put(permissionPath(kind), async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        writes.push({ kind, body });
        if (body.base_revision !== profile.revision)
          return HttpResponse.json(
            { detail: "Your profile changed in another tab. Review it." },
            { status: 409 },
          );
        if (typeof body.enabled !== "boolean")
          return HttpResponse.json(
            { detail: "Enabled is required." },
            { status: 422 },
          );
        if (
          (body.accept_version !== undefined && body.accept_version !== 4) ||
          (body.enabled && !accepted && body.accept_version !== 4)
        )
          return HttpResponse.json(
            { detail: "Review the current risk sentence." },
            { status: 409 },
          );
        const search = profile.sections.search as Search;
        if (kind === "linkedin" || kind === "freework") {
          const rows = search.browser_sources ?? [];
          const existing = rows.find((row) => row.kind === kind);
          if (body.enabled && !existing && rows.length === 10)
            return HttpResponse.json(
              { detail: "Keep at most 10 browser sources." },
              { status: 422 },
            );
          if (body.enabled) {
            const row = {
              ...existing,
              kind,
              query: String(body.query),
              location: existing?.location ?? null,
              enabled: true,
              interactive_only: body.interactive_only === true,
            };
            search.browser_sources = [
              ...rows.filter((item) => item.kind !== kind),
              row,
            ];
          } else if (existing) existing.enabled = false;
        } else if (
          ["query", "location", "interactive_only"].some((key) => key in body)
        )
          return HttpResponse.json(
            { detail: "This platform consent does not accept reader fields." },
            { status: 422 },
          );
        enabled = body.enabled;
        if (body.accept_version === 4) accepted = true;
        profile.revision += 1;
        return HttpResponse.json(response());
      }),
    );
  }
  return writes;
}

async function reviewPermission(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  label: string,
) {
  await user.click(
    within(dialog).getByRole("button", { name: "Review website permissions" }),
  );
  const card = await within(dialog).findByRole("region", {
    name: `${label} browser permission`,
  });
  await within(card).findByText(new RegExp(`Current .* risk sentence`));
  return card;
}

const MAP_PERMISSIONS: (typeof PERMISSIONS)[number][] = PERMISSIONS.filter(
  ([kind]) => kind === "indeed" || kind === "welcome_to_the_jungle",
);
it.each(MAP_PERMISSIONS)(
  "binds explicit %s Enable and Disable to reviewed revisions",
  async (kind, label) => {
    const { profile, starts, user, dialog } = await open();
    const writes = permissionApi(profile);
    const card = await reviewPermission(user, dialog, label);
    expect(writes).toEqual([]);
    const enable = within(card).getByRole("button", {
      name: `Enable ${label} browsing`,
    });
    expect(enable).toBeDisabled();
    if (kind === "linkedin" || kind === "freework")
      await user.type(
        within(card).getByRole("textbox", { name: `${label} search words` }),
        "junior engineer",
      );
    await user.click(
      within(card).getByRole("checkbox", {
        name: `I accept the ${label} risk above`,
      }),
    );
    await user.click(enable);
    await within(card).findByText("Browsing allowed");
    expect(writes).toEqual([
      {
        kind,
        body: {
          enabled: true,
          base_revision: 6,
          accept_version: 4,
          ...(kind === "linkedin" || kind === "freework"
            ? { query: "junior engineer", interactive_only: true }
            : {}),
        },
      },
    ]);
    await user.click(
      within(card).getByRole("button", { name: `Disable ${label} browsing` }),
    );
    await waitFor(() => {
      expect(writes).toHaveLength(2);
    });
    expect(writes[1]).toEqual({
      kind,
      body: { enabled: false, base_revision: 7 },
    });
    await waitFor(() => {
      expect(
        within(dialog).getByRole("button", { name: "Save and Start" }),
      ).toBeEnabled();
    });
    await user.click(
      within(dialog).getByRole("button", { name: "Save and Start" }),
    );
    await waitFor(() => {
      expect(starts).toEqual([{ profile_revision: 9 }]);
    });
    expect(profile.saves[0]!.body.base_revision).toBe(8);
  },
);

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((ready) => {
    resolve = ready;
  });
  return { promise, resolve };
}

async function acceptPermission(
  user: ReturnType<typeof userEvent.setup>,
  card: HTMLElement,
  label: string,
) {
  const words = within(card).queryByRole("textbox", {
    name: `${label} search words`,
  });
  if (words) {
    await user.clear(words);
    await user.type(words, "junior engineer");
  }
  await user.click(
    within(card).getByRole("checkbox", {
      name: `I accept the ${label} risk above`,
    }),
  );
  await user.click(
    within(card).getByRole("button", { name: `Enable ${label} browsing` }),
  );
}

function reviewedSearch(): Search {
  const search = structuredClone(EMPTY.search);
  search.boards = [
    {
      company: "Example Robotics",
      ats: "lever",
      org: "example",
      enabled: true,
      extra_exclude: { advanced_regex: false, terms: [] },
    },
  ];
  search.feeds = [
    {
      kind: "remoteok",
      enabled: true,
      terms: { advanced_regex: false, terms: ["engineering"] },
    },
  ];
  search.browser_sources = [
    {
      kind: "linkedin",
      query: "reviewed LinkedIn words",
      location: "Paris",
      enabled: false,
    },
    {
      kind: "freework",
      query: "reviewed Free-Work words",
      location: "Lyon",
      enabled: false,
    },
    {
      kind: "site",
      query: "https://careers.example.org/",
      location: null,
      enabled: true,
    },
  ];
  search.interactive_sites = [
    {
      platform: "custom",
      url: "https://careers.example.org/",
      enabled: true,
      mode: "llm_browser",
    },
  ];
  search.interactive_instructions = "Reviewed instructions.";
  return search;
}

const ORDINARY_EDITS: [string, (search: Search) => void][] = [
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
    "board state",
    (search) => {
      search.boards[0]!.enabled = false;
    },
  ],
  [
    "feed terms",
    (search) => {
      search.feeds![0]!.terms.terms = ["newer feed terms"];
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
      search.interactive_sites![0]!.url = "https://newer.example.org/";
    },
  ],
  [
    "legacy LinkedIn query",
    (search) => {
      search.browser_sources![0]!.query = "newer tab words";
    },
  ],
  [
    "legacy Free-Work location",
    (search) => {
      search.browser_sources![1]!.location = "Nantes";
    },
  ],
  [
    "compensation",
    (search) => {
      search.compensation.salary = { min: 40000, max: 50000, currency: "EUR" };
    },
  ],
];

it.each(ORDINARY_EDITS)(
  "retains the draft when %s changes after consent commits",
  async (_name, edit) => {
    const { profile, starts, user, dialog } = await open(reviewedSearch());
    permissionApi(profile);
    const card = await reviewPermission(user, dialog, "Indeed");
    await user.click(within(dialog).getByRole("checkbox", { name: "Indeed" }));
    const instructions = within(dialog).getByRole("textbox", {
      name: "Instructions for this search",
    });
    await user.clear(instructions);
    await user.type(instructions, "Keep my unsaved draft.");
    const entered = deferred();
    const release = deferred();
    server.use(
      http.get("*/api/profile", async () => {
        entered.resolve();
        await release.promise;
        return HttpResponse.json(profileBody(profile));
      }),
    );
    await acceptPermission(user, card, "Indeed");
    await entered.promise;
    expect(profile.revision).toBe(7);
    edit(profile.sections.search as Search);
    profile.revision += 1;
    release.resolve();
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "changed",
    );
    expect(instructions).toHaveValue("Keep my unsaved draft.");
    expect(
      within(dialog).getByRole("spinbutton", {
        name: "Posted within the last days",
      }),
    ).toHaveValue(30);
    expect(
      within(dialog).getByRole("checkbox", { name: "Indeed" }),
    ).toBeChecked();
    expect(
      within(dialog).getByRole("button", { name: "Save and Start" }),
    ).toBeDisabled();
    expect(profile.saves).toEqual([]);
    expect(starts).toEqual([]);
  },
);

const confirmedPermission = {
  enabled: true,
  accepted: true,
  allowed: true,
  text: "Current indeed risk sentence.",
  text_version: 4,
};

it.each(["network", "aborted body", "malformed JSON", "malformed shape"])(
  "blocks Save and Start after an ambiguous %s permission result",
  async (fault) => {
    const { profile, starts, user, dialog } = await open();
    permissionApi(profile);
    const card = await reviewPermission(user, dialog, "Indeed");
    await user.type(
      within(dialog).getByRole("textbox", {
        name: "Instructions for this search",
      }),
      "Retain this draft.",
    );
    const writes: unknown[] = [];
    server.use(
      http.put(permissionPath("indeed"), async ({ request }) => {
        writes.push(await request.json());
        profile.revision = 7; // The request committed before its response was lost or unusable.
        if (fault === "network") return HttpResponse.error();
        if (fault === "aborted body")
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.error(
                  new DOMException("response body aborted", "AbortError"),
                );
              },
            }),
          );
        if (fault === "malformed JSON")
          return new HttpResponse("{unfinished", {
            headers: { "Content-Type": "application/json" },
          });
        return HttpResponse.json({ enabled: true });
      }),
    );
    await acceptPermission(user, card, "Indeed");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "not confirmed",
    );
    expect(
      within(dialog).getByRole("button", { name: "Save and Start" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("textbox", {
        name: "Instructions for this search",
      }),
    ).toHaveValue("Retain this draft.");
    expect(writes).toHaveLength(1);
    expect(profile.saves).toEqual([]);
    expect(starts).toEqual([]);
  },
);

it("blocks Save and Start after a committed permission request times out", async () => {
  const { profile, starts, user, dialog } = await open();
  permissionApi(profile);
  const card = await reviewPermission(user, dialog, "Indeed");
  await user.click(
    within(card).getByRole("checkbox", {
      name: "I accept the Indeed risk above",
    }),
  );
  const release = deferred();
  let entered = false;
  server.use(
    http.put(permissionPath("indeed"), async () => {
      profile.revision = 7;
      entered = true;
      await release.promise;
      return HttpResponse.json(confirmedPermission);
    }),
  );
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    fireEvent.click(
      within(card).getByRole("button", { name: "Enable Indeed browsing" }),
    );
    await waitForOnFakeClock(
      () => {
        expect(entered).toBe(true);
      },
      1,
      100,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS);
    });
    await waitForOnFakeClock(() => {
      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        "not confirmed",
      );
    });
    expect(
      within(dialog).getByRole("button", { name: "Save and Start" }),
    ).toBeDisabled();
    expect(profile.saves).toEqual([]);
    expect(starts).toEqual([]);
  } finally {
    release.resolve();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    vi.useRealTimers();
  }
});

it("requires reconciliation when PUT succeeds but the final profile GET fails", async () => {
  const { profile, starts, user, dialog } = await open();
  permissionApi(profile);
  const card = await reviewPermission(user, dialog, "Indeed");
  server.use(
    http.get("*/api/profile", () =>
      HttpResponse.json(
        { detail: "Verification unavailable." },
        { status: 503 },
      ),
    ),
  );
  await acceptPermission(user, card, "Indeed");
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "not confirmed",
  );
  expect(profile.revision).toBe(7);
  expect(
    within(dialog).getByRole("button", { name: "Save and Start" }),
  ).toBeDisabled();
  expect(profile.saves).toEqual([]);
  expect(starts).toEqual([]);
});

it.each(["PUT", "final GET"])(
  "admits one permission action and no Save while %s is held",
  async (held) => {
    const { profile, starts, user, dialog } = await open();
    const writes = permissionApi(profile);
    const indeed = await reviewPermission(user, dialog, "Indeed");
    const jungle = within(dialog).getByRole("region", {
      name: "Welcome to the Jungle browser permission",
    });
    await within(jungle).findByText(
      "Current welcome_to_the_jungle risk sentence.",
    );
    for (const [card, label] of [
      [indeed, "Indeed"],
      [jungle, "Welcome to the Jungle"],
    ] as const)
      await user.click(
        within(card).getByRole("checkbox", {
          name: `I accept the ${label} risk above`,
        }),
      );
    const entered = deferred();
    const release = deferred();
    if (held === "PUT")
      server.use(
        http.put(permissionPath("indeed"), async ({ request }) => {
          writes.push({
            kind: "indeed",
            body: (await request.json()) as Record<string, unknown>,
          });
          entered.resolve();
          await release.promise;
          profile.revision += 1;
          return HttpResponse.json(confirmedPermission);
        }),
      );
    else
      server.use(
        http.get("*/api/profile", async () => {
          entered.resolve();
          await release.promise;
          return HttpResponse.json(profileBody(profile));
        }),
      );
    const save = within(dialog).getByRole("button", { name: "Save and Start" });
    try {
      act(() => {
        within(indeed)
          .getByRole("button", { name: "Enable Indeed browsing" })
          .click();
        within(jungle)
          .getByRole("button", {
            name: "Enable Welcome to the Jungle browsing",
          })
          .click();
        save.click();
      });
      await entered.promise;
      expect(save).toHaveTextContent("Save and Start");
      expect(writes).toHaveLength(1);
      expect(profile.saves).toEqual([]);
      expect(starts).toEqual([]);
      expect(save).toBeDisabled();
    } finally {
      release.resolve();
    }
  },
);

const OFF_PLATFORMS: PermissionKind[] = ["indeed"];
it.each(OFF_PLATFORMS)(
  "keeps a newer %s Off decision over an older Enable response",
  async (kind) => {
    const { profile, starts, user, dialog } = await open();
    permissionApi(profile);
    const label = kind === "linkedin" ? "LinkedIn" : "Indeed";
    const card = await reviewPermission(user, dialog, label);
    const entered = deferred();
    const release = deferred();
    server.use(
      http.put(permissionPath(kind), async () => {
        if (kind === "linkedin")
          (profile.sections.search as Search).browser_sources = [
            {
              kind,
              query: "junior engineer",
              location: null,
              enabled: true,
              interactive_only: true,
            },
          ];
        else
          Object.assign(profile.sections.search!, {
            platform_consents: {
              indeed: {
                enabled: true,
                accepted_at: "2026-10-24T10:00:00Z",
                text_version: 4,
              },
            },
          });
        profile.revision = 7;
        entered.resolve();
        await release.promise;
        return HttpResponse.json({
          ...confirmedPermission,
          text: `Current ${kind} risk sentence.`,
        });
      }),
    );
    await acceptPermission(user, card, label);
    await entered.promise;
    if (kind === "linkedin")
      (profile.sections.search as Search).browser_sources![0]!.enabled = false;
    else
      Object.assign(profile.sections.search!, {
        platform_consents: {
          indeed: {
            enabled: false,
            accepted_at: "2026-10-24T10:00:00Z",
            text_version: 4,
          },
        },
      });
    profile.revision = 8;
    server.use(
      http.get(permissionPath(kind), () =>
        HttpResponse.json({
          ...confirmedPermission,
          enabled: false,
          accepted: true,
          allowed: false,
          text: `Current ${kind} risk sentence.`,
        }),
      ),
    );
    release.resolve();
    await waitFor(() => {
      expect(
        within(dialog).getByRole("button", { name: "Save and Start" }),
      ).toBeEnabled();
    });
    expect(
      within(card).queryByText("Browsing allowed"),
    ).not.toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: `Disable ${label} browsing` }),
    ).not.toBeInTheDocument();
    expect(profile.revision).toBe(8);
    expect(profile.saves).toEqual([]);
    expect(starts).toEqual([]);
  },
);

it.each([false, true])(
  "does not replace a newer profile with an old verification response, reopen=%s",
  async (reopen) => {
    const { profile, starts, user, dialog, client, router } = await open();
    permissionApi(profile);
    const card = await reviewPermission(user, dialog, "Indeed");
    if (!reopen)
      await user.type(
        within(dialog).getByRole("textbox", {
          name: "Instructions for this search",
        }),
        "Newer mounted draft.",
      );
    const entered = deferred();
    const release = deferred();
    server.use(
      http.get(
        "*/api/profile",
        async () => {
          const old = structuredClone(profileBody(profile));
          entered.resolve();
          await release.promise;
          return HttpResponse.json(old);
        },
        { once: true },
      ),
    );
    await acceptPermission(user, card, "Indeed");
    await entered.promise;
    (profile.sections.search as Search).days = 7;
    profile.revision = 8;
    if (reopen) {
      await act(async () => {
        await router.navigate("/privacy");
      });
      await act(async () => {
        await router.navigate("/jobs");
      });
      await screen.findByRole("table", { name: "Postings" });
      await user.click(screen.getByRole("button", { name: "Find jobs" }));
    }
    const fresh = await getProfile();
    act(() => {
      client.setQueryData(profileKey, fresh);
    });
    const active = screen.getByRole("dialog", { name: "Find jobs setup" });
    if (reopen)
      await user.type(
        within(active).getByRole("textbox", {
          name: "Instructions for this search",
        }),
        "Newer mounted draft.",
      );
    release.resolve();
    await waitFor(() => {
      expect(
        within(active).getByRole("textbox", {
          name: "Instructions for this search",
        }),
      ).toBeEnabled();
    });
    expect(client.getQueryData<Profile>(profileKey)!.revision).toBe(8);
    expect(
      within(active).getByRole("textbox", {
        name: "Instructions for this search",
      }),
    ).toHaveValue("Newer mounted draft.");
    expect(profile.saves).toEqual([]);
    expect(starts).toEqual([]);
  },
);

it("requires a new explicit checkbox action when the current disclosure changes", async () => {
  const { profile, user, dialog, client } = await open();
  const writes = permissionApi(profile);
  const card = await reviewPermission(user, dialog, "Indeed");
  const acceptance = within(card).getByRole("checkbox", {
    name: "I accept the Indeed risk above",
  });
  await user.click(acceptance);
  expect(acceptance).toBeChecked();
  server.use(
    http.get(permissionPath("indeed"), () =>
      HttpResponse.json({
        ...confirmedPermission,
        enabled: false,
        accepted: false,
        allowed: false,
        text: "New current risk sentence.",
        text_version: 5,
      }),
    ),
  );
  await act(async () => {
    await client.invalidateQueries({
      queryKey: ["search-permission", "indeed"],
    });
  });
  expect(
    await within(card).findByText("New current risk sentence."),
  ).toBeVisible();
  expect(acceptance).not.toBeChecked();
  expect(
    within(card).getByRole("button", { name: "Enable Indeed browsing" }),
  ).toBeDisabled();
  expect(writes).toEqual([]);
});
