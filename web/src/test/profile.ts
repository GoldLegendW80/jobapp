/**
 * A fake /api/profile for tests: a profile shaped like the invented owner's import
 * (api/tests/profile/fixtures/owner_export.json), and MSW handlers
 * that keep it in memory, bump the revision on every save, answer 409 to a stale base_revision,
 * and compute completeness with the api's rules. Every save and import appends to `revisions`,
 * which the History endpoints page through as the api does (newest first, `before` exclusive),
 * and export and import speak the api's file format.
 */
import { http, HttpResponse } from "msw";

import type { SectionData, SectionName } from "@/features/profile/api";

import { server } from "./server";

export const XSS_BODY = [
  "Intro with **bold** and a [safe link](https://example.org).",
  "",
  '<script>window.__xss = "script"</script>',
  "",
  '<img src="x" onerror="window.__xss = \'img\'">',
  "",
  "[bad link](javascript:window.__xss='link')",
].join("\n");

export function haraldLike(): SectionData {
  return {
    identity: {
      full_name: "Harald Example",
      headline: "GenAI Engineer",
      email: "harald@example.org",
      phone: "+33 6 00 00 00 00",
      city: "Nantes",
      country: "France",
      linkedin_url: "https://www.linkedin.com/in/example/",
      website: "https://example.org/",
      links: [
        { label: "github.com/example", url: "https://github.com/example" },
      ],
      languages: [
        { name: "French", level: "Mother tongue" },
        { name: "English", level: "Advanced (C1)" },
        { name: "Spanish", level: "Intermediate" },
      ],
    },
    experience: {
      employers: [
        {
          key: "lumetra",
          organisation: "Lumetra Group",
          title: "GenAI Engineer",
          start: "2025-03",
          end: null,
          location: "Nantes",
          contract: null,
        },
        {
          key: "noxtech",
          organisation: "NoxTechSolutions",
          title: "Auto-entrepreneur (self-employed)",
          start: "2023-05",
          end: null,
          location: "Nantes",
          contract: "freelance",
        },
        {
          key: "olmea",
          organisation: "OLMEA",
          title: "Full-stack Developer (apprenticeship)",
          start: "2021-09",
          end: "2024-06",
          location: "Saint-Nazaire",
          contract: "apprenticeship",
        },
      ],
      education: [
        {
          school: "ORBITECH",
          degree: "Master of Engineering",
          start: "2020-09",
          end: "2024-06",
          location: "Nantes",
          details: "Major: Data Science",
        },
        {
          school: "Université de Kervan Haute-Loire",
          degree: "Bachelor of Mathematics",
          start: null,
          end: null,
          location: null,
          details: "2017 to 2020",
        },
      ],
    },
    facts: {
      blocks: [
        {
          title: "HARD RULES FOR ANY CV YOU WRITE",
          body: "1. Every claim traces to this file.\n2. No invented metric.",
        },
        { title: "POSITIONING", body: XSS_BODY },
      ],
    },
    skills: {
      years_experience: 3.5,
      have: [
        {
          name: "agents",
          advanced_regex: false,
          terms: ["agent", "langgraph"],
        },
        {
          name: "llm",
          advanced_regex: true,
          terms: ["\\bllm(?:s)?\\b", "\\bgenai\\b"],
        },
      ],
      adjacent: [{ name: "mlops", advanced_regex: false, terms: ["mlflow"] }],
      none: [{ name: "rust", advanced_regex: false, terms: ["rust"] }],
      gates: [
        { name: "phd", advanced_regex: false, terms: ["phd"], penalty: 25 },
      ],
    },
    search: {
      title_include: { advanced_regex: true, terms: ["applied ai", "llm"] },
      title_exclude: { advanced_regex: true, terms: ["\\bcyber\\b"] },
      location: { advanced_regex: false, terms: ["Paris", "Remote"] },
      location_exclude: { advanced_regex: false, terms: ["United States"] },
      contract_types: ["CDI", "Freelance"],
      remote_stance: "any",
      min_fit: 70,
      days: 30,
      never_apply: ["No cybersecurity roles."],
      sweep_schedule: {
        enabled: false,
        days: [
          "monday",
          "tuesday",
          "wednesday",
          "thursday",
          "friday",
          "saturday",
          "sunday",
        ],
        times: ["06:17"],
        timezone: "Europe/Paris",
      },
      documents: { auto: true, min_fit: 70, daily_ceiling: 100 },
      seniority: ["senior"],
      industries: { advanced_regex: false, terms: ["gaming"] },
      company_types: ["scaleup"],
      compensation: {
        salary: { min: 55000, max: 70000, currency: "EUR" },
        daily_rate: null,
      },
    },
    answers: {
      work_authorization: [
        {
          country: "France",
          answer: "French citizen, no sponsorship needed.",
          needs_sponsorship: false,
        },
        {
          country: "United Kingdom",
          answer: "Sponsorship would be needed.",
          needs_sponsorship: true,
        },
      ],
      notice_period: "1 to 3 months.",
      earliest_start: "December 2026",
      salary_policy: "Never invent a number.",
      relocation: "Paris preferred.",
      travel: "Monthly travel is fine.",
      motivation: "I want to bring AI to work that matters.",
      heard_about: "The careers page.",
      leave_blank: ["Voluntary self identification"],
      preferred_name: "Ana",
      pronouns: "she/her",
      years_experience_stated: "About 3.5 years",
      highest_degree: "Master's degree",
      highest_degree_year: 2022,
      willing_onsite: true,
      willing_hybrid: true,
      previously_applied_default: false,
      background_check_consent: false,
      reference_check_consent: true,
      eeo_policy: "stored",
      eeo_answers: [{ topic: "gender", answer: "Woman" }],
      salary_stance: "range",
      salary_ranges: [
        { currency: "EUR", minimum: 50000, maximum: 60000, period: "year" },
        { currency: "GBP", minimum: 45000, maximum: null, period: "year" },
      ],
    },
    preferences: {
      onboarding: { completed_at: null, dismissed_at: null, steps_passed: [] },
      documents_language: "auto",
      documents_tone: "plain",
    },
  };
}

const filled = (value: unknown) =>
  !(
    value === undefined ||
    value === null ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );

type Rules = Record<string, (section: Record<string, unknown>) => boolean>;
const field = (name: string) => (s: Record<string, unknown>) => filled(s[name]);
const terms = (name: string) => (s: Record<string, unknown>) =>
  filled((s[name] as { terms?: unknown } | undefined)?.terms ?? []);

/** The api's REQUIRED rules (api/app/profile/schemas.py). */
const REQUIRED: Record<SectionName, Rules> = {
  identity: Object.fromEntries(
    [
      "full_name",
      "headline",
      "email",
      "phone",
      "city",
      "country",
      "languages",
    ].map((n) => [n, field(n)]),
  ),
  experience: { employers: field("employers"), education: field("education") },
  facts: { blocks: field("blocks") },
  skills: { years_experience: field("years_experience"), have: field("have") },
  search: {
    title_include: terms("title_include"),
    location: terms("location"),
    contract_types: field("contract_types"),
    remote_stance: field("remote_stance"),
    interactive_sites: (s) =>
      ((s.interactive_sites as { enabled: boolean }[] | undefined) ?? []).some(
        (site) => site.enabled,
      ),
  },
  answers: Object.fromEntries(
    [
      "work_authorization",
      "notice_period",
      "earliest_start",
      "salary_policy",
      "motivation",
    ].map((n) => [n, field(n)]),
  ),
  preferences: {},
};

export function completeness(sections: Partial<Record<SectionName, unknown>>) {
  const result: Record<string, { percent: number; missing: string[] }> = {};
  for (const [name, rules] of Object.entries(REQUIRED)) {
    const data = (sections[name as SectionName] ?? {}) as Record<
      string,
      unknown
    >;
    const missing = Object.entries(rules)
      .filter(([, ok]) => !ok(data))
      .map(([n]) => n);
    const total = Object.keys(rules).length;
    result[name] = {
      percent:
        total === 0
          ? 100
          : Math.round((100 * (total - missing.length)) / total),
      missing,
    };
  }
  // Preferences has no required field: always 100, out of the mean (as the api).
  const overall = Math.round(
    Object.entries(result)
      .filter(([name]) => name !== "preferences")
      .reduce((sum, [, s]) => sum + s.percent, 0) / 6,
  );
  return { overall, sections: result };
}

export interface FakeProfile {
  revision: number;
  sections: Partial<Record<SectionName, unknown>>;
  photoEtag: string | null;
  saves: { section: string; body: { base_revision: number; data: unknown } }[];
  uploads: Blob[];
  deletes: number;
  revisions: FakeRevision[];
  /** bodies of POST /api/profile/import */
  imports: Record<string, unknown>[];
  exports: number;
}

export interface FakeRevision {
  revision: number;
  section: SectionName;
  created_at: string;
  data: unknown;
}

const SECTION_ORDER: SectionName[] = [
  "identity",
  "experience",
  "facts",
  "skills",
  "search",
  "answers",
];

/** A fixed time `revision` hours after the first save, so lists sort and read predictably. */
export const revisionTime = (revision: number) =>
  new Date(Date.UTC(2026, 9, 1, 8) + revision * 3600_000).toISOString();

export function profileBody(state: FakeProfile) {
  const sections: Record<string, unknown> = {};
  for (const name of [
    "identity",
    "experience",
    "facts",
    "skills",
    "search",
    "answers",
    "preferences",
  ]) {
    sections[name] = state.sections[name as SectionName] ?? null;
  }
  return {
    revision: state.revision,
    updated_at: "2026-10-09T08:00:00Z",
    sections,
    completeness: completeness(state.sections),
    has_photo: state.photoEtag !== null,
    photo_etag: state.photoEtag,
  };
}

/** Install the fake profile api. Pass `null` sections for a brand new user. */
export function fakeProfile(
  initial: Partial<Record<SectionName, unknown>> | null = haraldLike(),
  revision = 6,
) {
  const state: FakeProfile = {
    revision: initial ? revision : 0,
    sections: initial ? structuredClone(initial) : {},
    photoEtag: null,
    saves: [],
    uploads: [],
    deletes: 0,
    revisions: [],
    imports: [],
    exports: 0,
  };
  // The saves that led there: the last six revisions, one per section saved.
  if (initial) {
    const saved = SECTION_ORDER.filter((name) => name in initial);
    saved.forEach((section, index) => {
      const number = state.revision - saved.length + 1 + index;
      if (number < 1) return;
      state.revisions.push({
        revision: number,
        section,
        created_at: revisionTime(number),
        data: structuredClone(initial[section]),
      });
    });
  }
  server.use(
    http.get("*/api/profile", () => HttpResponse.json(profileBody(state))),
    http.put("*/api/profile/photo", async ({ request }) => {
      const blob = await request.blob();
      state.uploads.push(blob);
      state.photoEtag = `"etag-${state.uploads.length}"`;
      return HttpResponse.json({
        mime: "image/png",
        width: 512,
        height: 512,
        size: blob.size,
        etag: state.photoEtag,
        updated_at: "2026-10-09T08:00:00Z",
      });
    }),
    http.delete("*/api/profile/photo", () => {
      state.photoEtag = null;
      state.deletes += 1;
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("*/api/profile/photo", () =>
      HttpResponse.arrayBuffer(new Uint8Array([0x89, 0x50]).buffer, {
        headers: { "Content-Type": "image/png" },
      }),
    ),
    http.put("*/api/profile/:section", async ({ request, params }) => {
      const body = (await request.json()) as {
        base_revision: number;
        data: unknown;
      };
      const section = String(params.section);
      state.saves.push({ section, body });
      if (body.base_revision !== state.revision) {
        return HttpResponse.json(
          {
            detail:
              "The profile changed since you loaded it. Review the current version and save again.",
            revision: state.revision,
            sections: profileBody(state).sections,
          },
          { status: 409 },
        );
      }
      state.sections[section as SectionName] = body.data;
      state.revision += 1;
      state.revisions.push({
        revision: state.revision,
        section: section as SectionName,
        created_at: revisionTime(state.revision),
        data: structuredClone(body.data),
      });
      return HttpResponse.json(profileBody(state));
    }),
    http.get("*/api/profile/revisions", ({ request }) => {
      const url = new URL(request.url);
      const section = url.searchParams.get("section");
      const limit = Number(url.searchParams.get("limit") ?? "20");
      const before = url.searchParams.get("before");
      const matching = state.revisions
        .filter((r) => section === null || r.section === section)
        .filter((r) => before === null || r.revision < Number(before))
        .sort((a, b) => b.revision - a.revision);
      const items = matching.slice(0, limit);
      return HttpResponse.json({
        items: items.map(({ revision, section: name, created_at }) => ({
          revision,
          section: name,
          created_at,
        })),
        next_before:
          matching.length > limit ? (items.at(-1)?.revision ?? null) : null,
      });
    }),
    http.get("*/api/profile/revisions/:revision", ({ params }) => {
      const found = state.revisions.find(
        (r) => r.revision === Number(params.revision),
      );
      if (!found)
        return HttpResponse.json(
          { detail: "No such revision." },
          { status: 404 },
        );
      return HttpResponse.json(found);
    }),
    http.get("*/api/profile/export", () => {
      state.exports += 1;
      const sections: Record<string, unknown> = {};
      for (const name of SECTION_ORDER)
        sections[name] = state.sections[name] ?? EMPTY_SECTIONS[name];
      return HttpResponse.json(
        {
          format: "jobapp-profile",
          version: 1,
          exported_at: "2026-10-09T08:00:00+00:00",
          sections,
        },
        {
          headers: {
            "Content-Disposition":
              'attachment; filename="profile-2026-10-09.json"',
          },
        },
      );
    }),
    http.post("*/api/profile/import", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.imports.push(body);
      if (
        body.base_revision !== undefined &&
        body.base_revision !== state.revision
      ) {
        return HttpResponse.json(
          {
            detail:
              "The profile changed since you loaded it. Review the current version and save again.",
            revision: state.revision,
            sections: profileBody(state).sections,
          },
          { status: 409 },
        );
      }
      const sections = body.sections as Record<SectionName, unknown>;
      for (const name of SECTION_ORDER) {
        state.revision += 1;
        state.sections[name] = sections[name];
        state.revisions.push({
          revision: state.revision,
          section: name,
          created_at: revisionTime(state.revision),
          data: structuredClone(sections[name]),
        });
      }
      if (body.photo)
        state.photoEtag = `"etag-import-${String(state.revision)}"`;
      return HttpResponse.json(profileBody(state));
    }),
  );
  return state;
}

/** The empty form of each section, as the api exports a section never saved. */
const EMPTY_SECTIONS: Record<SectionName, unknown> = {
  identity: {
    full_name: null,
    headline: null,
    email: null,
    phone: null,
    city: null,
    country: null,
    linkedin_url: null,
    website: null,
    links: [],
    languages: [],
  },
  experience: { employers: [], education: [] },
  facts: { blocks: [] },
  skills: {
    years_experience: null,
    have: [],
    adjacent: [],
    none: [],
    gates: [],
  },
  search: {
    title_include: { advanced_regex: false, terms: [] },
    title_exclude: { advanced_regex: false, terms: [] },
    location: { advanced_regex: false, terms: [] },
    location_exclude: { advanced_regex: false, terms: [] },
    contract_types: [],
    remote_stance: null,
    min_fit: 70,
    days: 30,
    never_apply: [],
    sweep_schedule: {
      enabled: false,
      days: [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
      ],
      times: ["06:17"],
      timezone: "Europe/Paris",
    },
    documents: { auto: true, min_fit: 70, daily_ceiling: 100 },
    seniority: [],
    industries: { advanced_regex: false, terms: [] },
    company_types: [],
    compensation: { salary: null, daily_rate: null },
  },
  answers: {
    work_authorization: [],
    notice_period: null,
    earliest_start: null,
    salary_policy: null,
    relocation: null,
    travel: null,
    motivation: null,
    heard_about: null,
    leave_blank: [],
    preferred_name: null,
    pronouns: null,
    years_experience_stated: null,
    highest_degree: null,
    highest_degree_year: null,
    willing_onsite: null,
    willing_hybrid: null,
    previously_applied_default: null,
    background_check_consent: null,
    reference_check_consent: null,
    eeo_policy: "decline",
    eeo_answers: [],
    salary_stance: null,
    salary_ranges: [],
  },
  preferences: {
    onboarding: { completed_at: null, dismissed_at: null, steps_passed: [] },
    documents_language: "auto",
    documents_tone: "plain",
  },
};
