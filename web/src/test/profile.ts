/**
 * A fake /api/profile for tests: a profile shaped like Harald's real import
 * (api/tests/profile/fixtures/harald_export.json) with made up contact details, and MSW handlers
 * that keep it in memory, bump the revision on every save, answer 409 to a stale base_revision,
 * and compute completeness with the api's rules.
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
      city: "Bordeaux",
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
          key: "betclic",
          organisation: "Betclic Group",
          title: "GenAI Engineer",
          start: "2025-09",
          end: null,
          location: "Bordeaux",
          contract: null,
        },
        {
          key: "devtech",
          organisation: "DevTechSolutions",
          title: "Auto-entrepreneur (self-employed)",
          start: "2024-08",
          end: null,
          location: "Bordeaux",
          contract: "freelance",
        },
        {
          key: "elsan",
          organisation: "ELSAN",
          title: "Full-stack Developer (apprenticeship)",
          start: "2023-01",
          end: "2025-08",
          location: "Bordeaux",
          contract: "apprenticeship",
        },
      ],
      education: [
        {
          school: "EPITECH",
          degree: "MSc Pro",
          start: "2022-09",
          end: "2025-07",
          location: "Bordeaux",
          details: "Specialization: AI",
        },
        {
          school: "Université de Reims Champagne-Ardenne",
          degree: "Bachelor of Computer Science",
          start: null,
          end: null,
          location: null,
          details: "2018 to 2022",
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
      contract_types: ["CDI", "Freelance"],
      remote_stance: "any",
      min_fit: 70,
      days: 30,
      boards: [
        {
          company: "Mistral AI",
          ats: "ashby",
          org: "mistral.ai",
          enabled: true,
          extra_exclude: { advanced_regex: false, terms: [] },
        },
        {
          company: "Datadog",
          ats: "greenhouse",
          org: "datadog",
          enabled: false,
          extra_exclude: { advanced_regex: false, terms: [] },
        },
      ],
      never_apply: ["No cybersecurity roles."],
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
    boards: (s) =>
      ((s.boards as { enabled: boolean }[] | undefined) ?? []).some(
        (b) => b.enabled,
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
      percent: Math.round((100 * (total - missing.length)) / total),
      missing,
    };
  }
  const overall = Math.round(
    Object.values(result).reduce((sum, s) => sum + s.percent, 0) / 6,
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
}

export function profileBody(state: FakeProfile) {
  const sections: Record<string, unknown> = {};
  for (const name of [
    "identity",
    "experience",
    "facts",
    "skills",
    "search",
    "answers",
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
  };
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
      return HttpResponse.json(profileBody(state));
    }),
  );
  return state;
}
