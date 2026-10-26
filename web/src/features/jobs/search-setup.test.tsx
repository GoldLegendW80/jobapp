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
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

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
  renderApp("/jobs");
  await screen.findByRole("table", { name: "Postings" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Find jobs" }));
  const dialog = await screen.findByRole("dialog", { name: "Find jobs setup" });
  return { profile, starts, user, dialog };
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
