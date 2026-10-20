import { QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { waitForOnFakeClock } from "@/test/clock";
import { fakeVersion } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { DocumentView } from "./document-view";
import {
  documentKey,
  type DocumentOut,
  type VersionSummary,
} from "./documents-api";
import { AUTOSAVE_MS } from "./edit-autosave";
import { fieldKeys, withDefaults, type Draft } from "./edit-content";

/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

const PROFILE = {
  identity: {
    full_name: "Alex Example",
    headline: null,
    email: "e2e-alex@example.org",
    phone: null,
    city: "Lyon",
    country: "France",
    linkedin_url: null,
    website: null,
    links: [],
    languages: [{ name: "English", level: "fluent" }],
  },
  experience: {
    employers: [
      {
        key: "example-corp",
        organisation: "Example Corp",
        title: "Platform engineer",
        start: "2021-03",
        end: null,
        location: null,
        contract: null,
      },
      {
        key: "sample-labs",
        organisation: "Sample Labs",
        title: "Backend developer",
        start: "2018-01",
        end: "2021-02",
        location: null,
        contract: null,
      },
    ],
    education: [],
  },
};

const CV: Draft = {
  schema: 1,
  target: "Platform role at Example Robotics",
  headline: "Platform engineer",
  summary: "Builds reliable platforms.",
  highlights: ["Cut deploy time"],
  competencies: ["Kubernetes", "Python"],
  experience: [
    {
      employer_key: "example-corp",
      bullets: ["Ran the build farm", "Moved services to containers"],
    },
    { employer_key: "sample-labs", bullets: ["Wrote the billing api"] },
  ],
  skills: [{ label: "Languages", items: ["Python", "Go"] }],
  notes: "",
};

const LIMITS = {
  cv: {
    headline: 90,
    summary: 330,
    highlight: 60,
    competency: 40,
    main_bullet: 190,
    bullet: 100,
    paragraph: null,
  },
  letter: { headline: 90, paragraph: 900, summary: null },
};

const BASE: Partial<DocumentOut> = {
  scope: "base",
  posting_id: null,
  name: "Main CV",
  draft: {
    ...CV,
    fixed: {
      employers: [
        {
          key: "example-corp",
          organisation: "Example Corp",
          title: "Platform engineer",
          start: "2021-03",
          end: null,
          location: "Lyon",
        },
      ],
      schools: [
        {
          school: "Example University",
          degree: "MSc Computer Science",
          start: "2014",
          end: "2016",
          location: "",
        },
      ],
      languages: [{ name: "French", level: "native" }],
    },
  },
};

const LETTER: Partial<DocumentOut> = {
  kind: "letter",
  draft: {
    schema: 1,
    recipient: "Hiring team, Example Robotics",
    subject: "Platform engineer",
    salutation: "Hello,",
    paragraphs: ["First paragraph.", "Second paragraph."],
    closing: "Kind regards,",
  },
};

function doc(overrides: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id: "doc-1",
    scope: "posting",
    kind: "cv",
    posting_id: "job-1",
    base_document_id: null,
    name: null,
    language: "en",
    template_slug: "classic",
    draft: CV,
    draft_revision: 3,
    draft_written_by: "writer",
    draft_updated_at: "2026-10-14T09:00:00Z",
    notes: [],
    render: { state: "idle", revision: 3, error: null, message: null },
    latest: fakeVersion({ number: 2 }),
    ...overrides,
  };
}

interface Save {
  ifMatch: string | null;
  body: Draft;
}

/** The api of one document: saves with If-Match, the language, restore and approve. */
function fake(start: DocumentOut, versions: VersionSummary[] = []) {
  fakeProfile(PROFILE);
  const state = {
    document: start,
    versions,
    contents: new Map<number, Draft>(),
    saves: [] as Save[],
    calls: [] as { method: string; path: string; body: unknown }[],
    hold: null as Promise<void> | null,
    renderAfterSave: "idle" as "idle" | "queued",
    notes: (() => []) as (draft: Draft) => Record<string, unknown>[],
    refuse: null as unknown[] | null,
  };
  const note = async (request: Request) => {
    const text = request.method === "GET" ? "" : await request.clone().text();
    state.calls.push({
      method: request.method,
      path: new URL(request.url).pathname,
      body: text === "" ? undefined : (JSON.parse(text) as unknown),
    });
  };
  server.use(
    http.get("*/api/document-templates", () =>
      HttpResponse.json({
        templates: [
          {
            slug: "classic",
            version: 1,
            user: false,
            name: { en: "Classic", fr: "Classique" },
            description: { en: "One column", fr: "Une colonne" },
            tags: [],
            photo: "optional",
            accent: null,
            kinds: ["cv", "letter"],
            thumbnails: {},
            soft_limits: LIMITS,
          },
        ],
      }),
    ),
    http.get("*/api/documents/:id/versions", () =>
      HttpResponse.json(state.versions),
    ),
    http.get("*/api/documents/:id", () => HttpResponse.json(state.document)),
    http.put("*/api/documents/:id/draft", async ({ request }) => {
      const body = (await request.json()) as Draft;
      const ifMatch = request.headers.get("If-Match");
      state.saves.push({ ifMatch, body });
      if (state.hold) await state.hold;
      if (Number(ifMatch) !== state.document.draft_revision)
        return HttpResponse.json(
          { detail: "This document changed.", document: state.document },
          { status: 409 },
        );
      if (state.refuse)
        return HttpResponse.json({ detail: state.refuse }, { status: 422 });
      const revision = state.document.draft_revision + 1;
      state.document = {
        ...state.document,
        draft: body,
        draft_revision: revision,
        draft_written_by: "user",
        notes: state.notes(body),
        render: {
          state: state.renderAfterSave,
          revision,
          error: null,
          message: null,
        },
      };
      return HttpResponse.json(state.document);
    }),
    http.patch("*/api/documents/:id", async ({ request }) => {
      await note(request);
      const change = (await request.json()) as { language: "en" | "fr" };
      state.document = { ...state.document, language: change.language };
      return HttpResponse.json(state.document);
    }),
    http.post(
      "*/api/documents/:id/versions/:number/restore",
      async ({ request, params }) => {
        await note(request);
        const content = state.contents.get(Number(params.number)) ?? {};
        state.document = {
          ...state.document,
          draft: content,
          draft_revision: state.document.draft_revision + 1,
          draft_written_by: "restore",
        };
        return HttpResponse.json(state.document);
      },
    ),
    http.post(
      "*/api/documents/:id/versions/:number/approve",
      async ({ request, params }) => {
        await note(request);
        const number = Number(params.number);
        state.versions = state.versions.map((one) => ({
          ...one,
          approved_at: one.number === number ? "2026-10-15T09:00:00Z" : null,
        }));
        return HttpResponse.json(
          state.versions.find((one) => one.number === number),
        );
      },
    ),
  );
  return state;
}

async function openEdit(user: UserEvent) {
  renderWithQuery(
    <MemoryRouter>
      <DocumentView documentId="doc-1" />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("tab", { name: "Edit" }));
  return screen.findByTestId("edit-panel");
}

async function retype(user: UserEvent, label: string, value: string) {
  const field = await screen.findByLabelText(label);
  await user.clear(field);
  await user.type(field, value);
  await user.tab();
}

afterEach(() => {
  vi.useRealTimers();
});

describe("the Edit view", () => {
  it("shows the fixed facts read only, counts against the template, and saves a second after typing stops", async () => {
    const api = fake(doc());
    api.renderAfterSave = "queued";
    const user = userEvent.setup();
    await openEdit(user);

    const facts = await screen.findByRole("region", {
      name: "From your profile, printed as they are",
    });
    expect(facts).toHaveTextContent("Alex Example");
    expect(
      within(facts).getByRole("link", {
        name: "Change your name and contact in your profile",
      }),
    ).toHaveAttribute("href", "/profile?tab=identity");
    expect(
      within(facts).getByRole("link", { name: "your employers and schools" }),
    ).toHaveAttribute("href", "/profile?tab=experience");
    expect(
      await screen.findByRole("group", { name: "Example Corp" }),
    ).toHaveTextContent("Platform engineer, March 2021 to today");
    const summary = screen.getByLabelText("Summary");
    expect(summary).toHaveAccessibleDescription("26 of about 330 characters");
    // The first employer's bullets have the main counter, the others the shorter one.
    expect(
      screen.getByLabelText("Bullet 1, Example Corp"),
    ).toHaveAccessibleDescription("18 of about 190 characters");
    expect(
      screen.getByLabelText("Bullet 1, Sample Labs"),
    ).toHaveAccessibleDescription("21 of about 100 characters");

    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.change(summary, {
      target: { value: "Builds reliable platforms for robots." },
    });
    expect(summary).toHaveAccessibleDescription("37 of about 330 characters");
    expect(screen.getByText("Not saved yet")).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS - 10);
    });
    expect(api.saves).toHaveLength(0);
    await waitForOnFakeClock(() => {
      expect(api.saves).toHaveLength(1);
    });
    expect(api.saves[0]).toMatchObject({
      ifMatch: "3",
      body: { summary: "Builds reliable platforms for robots." },
    });
    // The api queues the render of a save: the view says the page is being drawn.
    await waitForOnFakeClock(() => {
      expect(
        screen.getByText("Saved, the new page is being drawn"),
      ).toBeVisible();
    });
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("sends what is typed during a save after it, with the revision that save answered", async () => {
    const api = fake(doc());
    let release = () => {};
    api.hold = new Promise((resolve) => {
      release = resolve;
    });
    const user = userEvent.setup();
    await openEdit(user);
    await retype(user, "Headline", "Site reliability engineer");
    await waitFor(() => {
      expect(api.saves).toHaveLength(1);
    });
    expect(screen.getByText("Saving")).toBeVisible();

    await user.type(screen.getByLabelText("Headline"), ", Lyon");
    await user.tab();
    expect(api.saves).toHaveLength(1);
    release();
    await waitFor(() => {
      expect(api.saves).toHaveLength(2);
    });
    expect(api.saves[0]).toMatchObject({
      ifMatch: "3",
      body: { headline: "Site reliability engineer" },
    });
    expect(api.saves[1]).toMatchObject({
      ifMatch: "4",
      body: { headline: "Site reliability engineer, Lyon" },
    });
    expect(await screen.findByText("Saved")).toBeVisible();
    expect(api.saves).toHaveLength(2);
  });

  it("shows both texts when another tab saved first, and either can win", async () => {
    const api = fake(doc());
    const user = userEvent.setup();
    await openEdit(user);
    api.document = {
      ...api.document,
      draft: { ...CV, summary: "Saved in the other tab." },
      draft_revision: 4,
    };
    await retype(user, "Summary", "Typed in this tab.");

    const dialog = await screen.findByRole("alertdialog", {
      name: "This document changed in another tab",
    });
    expect(dialog).toHaveTextContent("Typed in this tab.");
    expect(dialog).toHaveTextContent("Saved in the other tab.");
    expect(dialog.textContent).not.toMatch(SEPARATOR);
    await user.click(
      within(dialog).getByRole("button", { name: "Keep my text" }),
    );
    await waitFor(() => {
      expect(api.document.draft.summary).toBe("Typed in this tab.");
    });
    expect(api.saves.map((one) => one.ifMatch)).toEqual(["3", "4"]);

    api.document = {
      ...api.document,
      draft: { ...CV, summary: "The other tab again." },
      draft_revision: 9,
    };
    await user.type(screen.getByLabelText("Summary"), " More.");
    await user.tab();
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Use the other tab's text",
      }),
    );
    await waitFor(() => {
      expect(screen.getByLabelText("Summary")).toHaveValue(
        "The other tab again.",
      );
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.saves).toHaveLength(3);
    expect(api.document.draft.summary).toBe("The other tab again.");
  });

  it("keeps what was typed when the view closes during a stale save, and asks on return", async () => {
    const api = fake(doc());
    const user = userEvent.setup();
    await openEdit(user);
    api.document = {
      ...api.document,
      draft: { ...CV, summary: "Saved in the other tab." },
      draft_revision: 4,
    };
    const summary = await screen.findByLabelText("Summary");
    await user.clear(summary);
    await user.type(summary, "Typed before leaving.");
    await user.click(screen.getByRole("tab", { name: "See" }));
    await waitFor(() => {
      expect(api.saves).toHaveLength(1);
    });
    expect(api.saves[0]?.body.summary).toBe("Typed before leaving.");

    await user.click(screen.getByRole("tab", { name: "Edit" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "This document changed in another tab",
    });
    expect(dialog).toHaveTextContent("Typed before leaving.");
    expect(dialog).toHaveTextContent("Saved in the other tab.");
    await user.click(
      within(dialog).getByRole("button", { name: "Keep my text" }),
    );
    await waitFor(() => {
      expect(api.document.draft.summary).toBe("Typed before leaving.");
    });
    expect(api.saves.map((one) => one.ifMatch)).toEqual(["3", "3", "4"]);
  });

  it("forgets what was not saved when the person signs out", async () => {
    const api = fake(doc());
    const user = userEvent.setup();
    const view = (
      <MemoryRouter>
        <DocumentView documentId="doc-1" />
      </MemoryRouter>
    );
    const { client, unmount } = renderWithQuery(view);
    await user.click(await screen.findByRole("tab", { name: "Edit" }));
    api.document = {
      ...api.document,
      draft: { ...CV, summary: "Saved in the other tab." },
      draft_revision: 4,
    };
    const summary = await screen.findByLabelText("Summary");
    await user.clear(summary);
    await user.type(summary, "Typed before signing out.");
    await user.click(screen.getByRole("tab", { name: "See" }));
    await waitFor(() => {
      expect(api.saves).toHaveLength(1);
    });
    unmount();
    // What signing out does to the cache (resetSession in auth-provider.tsx).
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== "me" });

    render(<QueryClientProvider client={client}>{view}</QueryClientProvider>);
    await user.click(await screen.findByRole("tab", { name: "Edit" }));
    expect(await screen.findByLabelText("Summary")).toHaveValue(
      "Saved in the other tab.",
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(api.saves).toHaveLength(1);
  });

  it("notes a claim typed by hand under its field and keeps it as typed", async () => {
    const api = fake(doc());
    api.notes = (draft) => {
      const at = String(draft.summary).indexOf("40");
      return at < 0
        ? []
        : [
            {
              field: "summary",
              start: at,
              end: at + 2,
              code: "number_not_in_sources",
              level: "claim",
              rewrite: false,
            },
          ];
    };
    const user = userEvent.setup();
    await openEdit(user);
    await retype(user, "Summary", "Led 40 engineers.");

    const notes = await screen.findByRole("list", { name: "Notes on Summary" });
    expect(notes).toHaveTextContent(
      "A number your profile does not state: “40”",
    );
    const summary = screen.getByLabelText("Summary");
    expect(summary).toHaveValue("Led 40 engineers.");
    expect(summary).toHaveAccessibleDescription(
      /A number your profile does not state/,
    );
    expect(api.saves).toHaveLength(1);
    expect(api.saves[0]?.body.summary).toBe("Led 40 engineers.");
  });

  it("says when an employer left the profile, and keeps its bullets", async () => {
    const api = fake(
      doc({
        draft: {
          ...CV,
          experience: [
            { employer_key: "gone-corp", bullets: ["Kept as written"] },
          ],
        },
      }),
    );
    const user = userEvent.setup();
    await openEdit(user);
    const entry = await screen.findByRole("group", { name: "gone-corp" });
    expect(
      await within(entry).findByText(
        /This employer is no longer in your profile/,
      ),
    ).toBeVisible();
    expect(within(entry).getByLabelText("Bullet 1, gone-corp")).toHaveValue(
      "Kept as written",
    );
    expect(api.saves).toHaveLength(0);
  });

  it("lists what the import left out, and says which employer is not in the profile", async () => {
    const legacy = (field: string, legacy_path: string, reason: string) => ({
      field,
      start: 0,
      end: 0,
      code: "legacy_invalid",
      level: "invalid",
      legacy_path,
      reason,
    });
    fake(
      doc({
        draft_written_by: "import",
        draft: {
          ...CV,
          experience: [
            {
              employer_key: "harbor-sample-works",
              bullets: ["Kept as written"],
            },
          ],
        },
        notes: [
          legacy("experience", "experience.1.bullets.0", "too_long"),
          legacy("", "photo", "unknown_field"),
          legacy("", "", "3 more"),
          {
            field: "experience.0.employer_key",
            start: 0,
            end: 19,
            code: "unknown_employer",
            level: "invalid",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    const panel = await openEdit(user);
    expect(
      within(panel).getByText(
        "Left out of the imported file: employer 2, bullet 1, too long.",
      ),
    ).toBeVisible();
    expect(
      within(panel).getByText(
        "Left out of the imported file: photo, not a field of this form.",
      ),
    ).toBeVisible();
    expect(
      within(panel).getByText(
        "3 more parts of the imported file were left out.",
      ),
    ).toBeVisible();
    const entry = await screen.findByRole("group", {
      name: "harbor-sample-works",
    });
    expect(
      await within(entry).findByText(
        /This employer is no longer in your profile/,
      ),
    ).toBeVisible();
    expect(panel.textContent).not.toMatch(SEPARATOR);
  });

  it("reorders bullets with the keyboard, keeping the focus on what moved", async () => {
    const api = fake(doc());
    const user = userEvent.setup();
    await openEdit(user);
    const entry = await screen.findByRole("group", { name: "Example Corp" });
    within(entry).getByLabelText("Bullet 2, Example Corp").focus();
    await user.keyboard("{Alt>}{ArrowUp}{/Alt}");
    const first = within(entry).getByLabelText("Bullet 1, Example Corp");
    expect(first).toHaveValue("Moved services to containers");
    expect(first).toHaveFocus();

    await user.click(
      within(entry).getByRole("button", {
        name: "Move bullet 1 of Example Corp down",
      }),
    );
    expect(
      within(entry).getByLabelText("Bullet 2, Example Corp"),
    ).toHaveFocus();
    await user.keyboard("{Alt>}{ArrowUp}{/Alt}");
    await user.tab();
    // Leaving a field saves at once: one save per move here, one after the other.
    await waitFor(() => {
      expect(api.document.draft.experience).toEqual([
        {
          employer_key: "example-corp",
          bullets: ["Moved services to containers", "Ran the build farm"],
        },
        { employer_key: "sample-labs", bullets: ["Wrote the billing api"] },
      ]);
    });
    expect(api.saves.map((one) => one.ifMatch)).toEqual(
      api.saves.map((_, index) => String(3 + index)),
    );
  });

  it("reorders the paragraphs of a letter with the keyboard", async () => {
    const api = fake(
      doc({
        kind: "letter",
        draft: {
          schema: 1,
          recipient: "Hiring team, Example Robotics",
          subject: "Platform engineer",
          salutation: "Hello,",
          paragraphs: ["First paragraph.", "Second paragraph."],
          closing: "Kind regards,",
        },
      }),
    );
    const user = userEvent.setup();
    await openEdit(user);
    const paragraph = await screen.findByLabelText("Paragraph 1");
    expect(paragraph).toHaveAccessibleDescription("16 of about 900 characters");
    paragraph.focus();
    await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
    expect(screen.getByLabelText("Paragraph 2")).toHaveValue(
      "First paragraph.",
    );
    expect(screen.getByLabelText("Paragraph 2")).toHaveFocus();
    await user.tab();
    await waitFor(() => {
      expect(api.saves).toHaveLength(1);
    });
    expect(api.saves[0]?.body.paragraphs).toEqual([
      "Second paragraph.",
      "First paragraph.",
    ]);
    expect(screen.queryByRole("group", { name: "Experience" })).toBeNull();
  });

  it("restores a sent version's text into the draft and approves another from the history", async () => {
    const sent = fakeVersion({
      number: 1,
      frozen_at: "2026-10-12T10:00:00Z",
      frozen_reason: "marked_sent",
    });
    const latest = fakeVersion({ number: 2, origin: "edited" });
    const api = fake(doc({ latest }), [latest, sent]);
    api.contents.set(1, { ...CV, summary: "The summary that was sent." });
    const user = userEvent.setup();
    renderWithQuery(
      <MemoryRouter>
        <DocumentView documentId="doc-1" />
      </MemoryRouter>,
    );
    const history = await screen.findByRole("region", { name: "Versions" });
    await within(history).findByRole("button", { name: "Restore version 1" });
    // A sent version keeps its own mark: it is restored, never approved.
    expect(
      within(history).queryByRole("button", { name: "Approve version 1" }),
    ).toBeNull();
    await user.click(
      within(history).getByRole("button", { name: "Restore version 1" }),
    );
    const dialog = await screen.findByRole("alertdialog", {
      name: "Restore version 1 into the draft?",
    });
    expect(dialog).toHaveTextContent("as it was sent");
    await user.click(within(dialog).getByRole("button", { name: "Restore" }));
    await waitFor(() => {
      expect(api.calls.map((one) => `${one.method} ${one.path}`)).toContain(
        "POST /api/documents/doc-1/versions/1/restore",
      );
    });

    await user.click(screen.getByRole("tab", { name: "Edit" }));
    expect(await screen.findByLabelText("Summary")).toHaveValue(
      "The summary that was sent.",
    );
    await user.click(screen.getByRole("tab", { name: "See" }));
    await user.click(
      await screen.findByRole("button", { name: "Approve version 2" }),
    );
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Approve version 2" }),
      ).toBeNull();
    });
    expect(
      await screen.findByRole("region", { name: "Versions" }),
    ).toHaveTextContent("approved");
    expect(document.body.textContent).not.toMatch(SEPARATOR);
  });

  it("after an approval, an edit makes a new draft and leaves the approval alone", async () => {
    const approved = fakeVersion({
      number: 2,
      approved_at: "2026-10-14T10:00:00Z",
    });
    const api = fake(doc({ latest: approved }), [approved]);
    const user = userEvent.setup();
    await openEdit(user);
    expect(
      await screen.findByText(
        "Version 2 is approved. What you change here makes a new version, which you approve in See once its page is drawn.",
      ),
    ).toBeVisible();
    await retype(user, "Headline", "Staff platform engineer");
    await waitFor(() => {
      expect(api.saves).toHaveLength(1);
    });
    expect(api.calls.filter((one) => one.method === "POST")).toEqual([]);
  });

  it("switches the language of the page to French", async () => {
    const api = fake(doc());
    const user = userEvent.setup();
    await openEdit(user);
    await user.selectOptions(
      screen.getByLabelText("Language of the page"),
      "fr",
    );
    await waitFor(() => {
      expect(api.calls).toContainEqual({
        method: "PATCH",
        path: "/api/documents/doc-1",
        body: { language: "fr" },
      });
    });
    expect(
      await screen.findByText(
        "The headings and the date print in French; your text prints as you write it.",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("Language of the page")).toHaveValue("fr");
    expect(api.saves).toHaveLength(0);
  });

  it("edits a base CV's own fixed facts, and says which field the api refused", async () => {
    const api = fake(
      doc({
        scope: "base",
        posting_id: null,
        name: "Main CV",
        draft: { ...CV, experience: [] },
      }),
    );
    const user = userEvent.setup();
    await openEdit(user);
    const facts = await screen.findByRole("region", {
      name: "Facts of this base CV",
    });
    await user.click(
      within(facts).getByRole("button", { name: "Add an employer" }),
    );
    expect(
      within(facts).getByLabelText("Organisation, employer 1"),
    ).toHaveFocus();
    await user.keyboard("New Robotics");
    await user.tab();
    await waitFor(() => {
      expect(api.saves).toHaveLength(1);
    });
    expect(api.saves[0]?.body.fixed).toEqual({
      employers: [
        {
          key: "new-robotics",
          organisation: "New Robotics",
          title: "",
          start: null,
          end: null,
          location: "",
        },
      ],
      schools: [],
      languages: [],
    });
    // An employer only the base CV states can be given bullets.
    expect(
      within(
        screen.getByLabelText("Add an employer of your profile"),
      ).getByRole("option", { name: "New Robotics" }),
    ).toBeInTheDocument();

    api.refuse = [
      {
        path: "fixed.employers.0.start",
        code: "bad_format",
        message: "must be a year or a month",
      },
    ];
    await user.type(within(facts).getByLabelText("Start, employer 1"), "20");
    await user.tab();
    expect(await screen.findByText("Not saved")).toBeVisible();
    expect(
      within(facts).getByLabelText("Start, employer 1"),
    ).toHaveAccessibleDescription(/Write a year or a month: 2021 or 2021-03\./);
    expect(within(facts).getByLabelText("Start, employer 1")).toHaveValue("20");
  });

  it.each<{
    path: string;
    code: string;
    shape: Partial<DocumentOut>;
    said: string;
  }>([
    {
      path: "",
      code: "content_too_large",
      shape: {},
      said: "The whole document is too large to save. Shorten or remove some text.",
    },
    {
      path: "experience",
      code: "duplicate_employer",
      shape: {},
      said: "Experience: An employer is named twice.",
    },
    {
      path: "fixed.employers",
      code: "duplicate_employer",
      shape: BASE,
      said: "Fixed, employers: An employer is named twice.",
    },
    {
      path: "fixed.employers.0.key",
      code: "bad_format",
      shape: BASE,
      said: "Fixed, employers, item 1, key: This is not written in the expected form.",
    },
    {
      path: "experience.0.employer_key",
      code: "too_long",
      shape: {},
      said: "Experience, item 1, employer key: This is longer than allowed.",
    },
    {
      path: "highlights.4",
      code: "too_long",
      shape: {},
      said: "Highlights, item 5: This is longer than allowed.",
    },
  ])(
    "says $code at '$path', where no field is drawn, in the save line",
    async ({ path, code, shape, said }) => {
      const api = fake(doc(shape));
      api.refuse = [{ path, code, message: "refused" }];
      const user = userEvent.setup();
      const panel = await openEdit(user);
      await retype(user, "Headline", "Staff platform engineer");
      expect(await screen.findByText("Not saved")).toBeVisible();
      expect(screen.getByText(said)).toBeVisible();
      expect(screen.getByText("Your text stays here.")).toBeVisible();
      expect(screen.queryByText(/A field below/)).toBeNull();
      expect(panel.querySelector("[aria-invalid]")).toBeNull();
    },
  );

  it("marks a refused field below and says the refusal no field shows", async () => {
    const api = fake(doc());
    api.refuse = [
      { path: "headline", code: "too_long", message: "refused" },
      { path: "experience", code: "duplicate_employer", message: "refused" },
    ];
    const user = userEvent.setup();
    await openEdit(user);
    await retype(user, "Headline", "Staff platform engineer");
    expect(await screen.findByText("Not saved")).toBeVisible();
    expect(
      screen.getByText("A field below cannot be saved as it is."),
    ).toBeVisible();
    expect(
      screen.getByText("Experience: An employer is named twice."),
    ).toBeVisible();
    expect(screen.getByLabelText("Headline")).toHaveAccessibleDescription(
      /This is longer than allowed\./,
    );
  });

  it.each<[string, Partial<DocumentOut>]>([
    ["a base CV", BASE],
    ["a CV for a posting", {}],
    ["a letter", LETTER],
  ])(
    "draws an input for every key fieldKeys names, and no other, on %s",
    async (_, shape) => {
      const start = doc(shape);
      fake(start);
      const user = userEvent.setup();
      const panel = await openEdit(user);
      const target = await screen.findByLabelText(/^What this .* is for/);
      const prefix = target.id.slice(0, -"target".length);
      const drawn = [...panel.querySelectorAll("input, textarea")].map((one) =>
        one.id.slice(prefix.length).replace(/-/g, "."),
      );
      expect(new Set(drawn)).toEqual(
        fieldKeys(
          withDefaults(start),
          start.kind === "letter",
          start.scope === "base",
        ),
      );
    },
  );

  it("gives another document a form of its own, so its saves never reach the first", async () => {
    fake(doc());
    const other = doc({
      id: "doc-2",
      draft: { ...CV, headline: "Data engineer" },
    });
    const puts: string[] = [];
    server.use(
      http.get("*/api/documents/doc-2", () => HttpResponse.json(other)),
      http.put("*/api/documents/:id/draft", ({ params }) => {
        puts.push(String(params.id));
        return HttpResponse.json({ ...other, draft_revision: 4 });
      }),
    );
    const user = userEvent.setup();
    const view = (id: string) => (
      <MemoryRouter>
        <DocumentView documentId={id} />
      </MemoryRouter>
    );
    const { client, rerender } = renderWithQuery(view("doc-1"));
    await user.click(await screen.findByRole("tab", { name: "Edit" }));
    expect(await screen.findByLabelText("Headline")).toHaveValue(
      "Platform engineer",
    );
    client.setQueryData(documentKey("doc-2"), other);
    rerender(
      <QueryClientProvider client={client}>
        {view("doc-2")}
      </QueryClientProvider>,
    );
    expect(screen.getByLabelText("Headline")).toHaveValue("Data engineer");
    await retype(user, "Summary", "Typed in the second document.");
    await waitFor(() => {
      expect(puts).toEqual(["doc-2"]);
    });
  });
});
