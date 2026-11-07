import { http, HttpResponse } from "msw";

import type {
  ProfileField,
  ProfileRun,
  ProfileSection,
} from "@/features/apply/api/site-profile-api";
import { server } from "@/test/server";

export const RUN_ID = "run-1";

const field = (
  key: string,
  label: string,
  value: unknown,
  current: unknown,
  overrides: Partial<ProfileField> = {},
): ProfileField => ({
  field_key: key,
  label,
  kind: "text",
  value,
  current_value: current,
  source: "experience",
  source_path: `experience.${key}`,
  required: false,
  refusal: null,
  options: [],
  replace: false,
  approved: false,
  ...overrides,
});

/** The Experience section of an invented career site, filled from the profile, before saving. */
export const EXPERIENCE: ProfileSection = {
  section: "experience",
  label: "Experience",
  position: 1,
  state: "ready",
  error_code: null,
  values_hash: "hash-experience-1",
  fields: [
    field(
      "headline",
      "Headline",
      "Senior data engineer, pricing pipelines",
      "Data engineer",
    ),
    field("current_title", "Current job title", "Senior data engineer", "", {
      required: true,
    }),
    field("current_employer", "Current employer", "Atelier Brume", null, {
      required: true,
    }),
    field("years", "Years of experience", "6", null),
    field(
      "email",
      "E-mail address",
      "friend@example.org",
      "friend@example.org",
      {
        source: "identity",
        source_path: "identity.email",
        refusal: "email",
      },
    ),
    field("salary_visible", "Show my salary to recruiters", null, "No", {
      kind: "select",
      source: "left",
      source_path: "",
      refusal: "privacy",
      options: ["Yes", "No"],
    }),
  ],
  screenshots: [
    {
      id: "shot-before",
      kind: "before",
      section: "experience",
      created_at: "2026-11-06T09:02:00Z",
    },
  ],
  events: [],
};

/** A run on an invented career site of the person, `state` with no section yet unless given. */
export function profileRun(
  state: string,
  overrides: Partial<ProfileRun> = {},
): ProfileRun {
  return {
    id: RUN_ID,
    url: "https://careers.example.org/candidate/profile",
    host: "careers.example.org",
    site_kind: "generic",
    state,
    autosave: false,
    autosave_confirmed: false,
    needs: null,
    error_code: null,
    created_at: "2026-11-06T09:00:00Z",
    updated_at: "2026-11-06T09:02:00Z",
    cost: {},
    sections: [],
    ...overrides,
  };
}

const PROFILE_SHOT = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520">
<rect width="900" height="520" fill="#ffffff"/>
<rect width="900" height="56" fill="#f3f4f6"/>
<text x="32" y="36" font-family="sans-serif" font-size="20" font-weight="600" fill="#111827">Your candidate profile: Experience</text>
${[
  ["Headline", "Senior data engineer, pricing pipelines"],
  ["Current job title", "Senior data engineer"],
  ["Current employer", "Atelier Brume"],
  ["Years of experience", "6"],
]
  .map(
    ([label, value], index) =>
      `<text x="32" y="${String(100 + index * 84)}" font-family="sans-serif" font-size="14" fill="#4b5563">${label ?? ""}</text>
<rect x="32" y="${String(110 + index * 84)}" width="560" height="40" rx="6" fill="#ffffff" stroke="#d1d5db"/>
<text x="44" y="${String(136 + index * 84)}" font-family="sans-serif" font-size="15" fill="#111827">${value ?? ""}</text>`,
  )
  .join("\n")}
<rect x="32" y="450" width="140" height="44" rx="22" fill="#111827" stroke="#f97316" stroke-width="4"/>
<text x="102" y="478" text-anchor="middle" font-family="sans-serif" font-size="15" fill="#ffffff">Save</text>
</svg>`;

/**
 * GET /api/site-profiles/{id} answers `run` (or `respond`), its screenshots the invented page
 * above, and every action answers the run as it is: a story shows a state, it does not save.
 */
export function fakeProfileRun(run: ProfileRun, respond?: () => Response) {
  server.use(
    http.get(
      "*/api/site-profiles/:id/sections/:section/screenshots/:shot",
      () =>
        new HttpResponse(PROFILE_SHOT, {
          headers: { "Content-Type": "image/svg+xml" },
        }),
    ),
    http.get("*/api/site-profiles/:id", () =>
      respond ? respond() : HttpResponse.json(run),
    ),
    http.post("*/api/site-profiles/:id/*", () => HttpResponse.json(run)),
  );
}
