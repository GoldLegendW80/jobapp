/** Invented searches for the stories of Your searches (jobs J33). */
import { makeSearch } from "@/test/searches";

const TERMS = (terms: string[]) => ({ advanced_regex: false, terms });

export const MAIN_SEARCH = makeSearch("main", "Main search", {
  title_include: TERMS(["Robotics engineer", "Controls engineer"]),
  location: TERMS(["Lyon", "Grenoble"]),
  interactive_sites: [
    {
      platform: "linkedin",
      url: "https://www.linkedin.com/",
      enabled: true,
      mode: "llm_browser",
    },
  ],
});

export const REMOTE_SEARCH = makeSearch(
  "00000000-0000-4000-8000-000000000001",
  "Remote roles anywhere in Europe",
  {
    title_include: TERMS(["Embedded software engineer"]),
    remote_stance: "remote",
    web_search: false,
    company_boards: [
      {
        ats: "ashby",
        board: "example-robotics",
        company: "Example Robotics",
        enabled: true,
      },
    ],
  },
  {
    schedule: {
      enabled: true,
      days: ["monday", "wednesday", "friday"],
      times: ["07:30"],
      timezone: "Europe/Paris",
      active: true,
      next_run: "2026-11-27T06:30:00Z",
    },
  },
);

export const WAITING_SEARCH = makeSearch(
  "00000000-0000-4000-8000-000000000002",
  "Internships in Grenoble",
  { title_include: TERMS(["Robotics intern"]), location: TERMS(["Grenoble"]) },
  {
    request: {
      id: "request-1",
      search_id: "00000000-0000-4000-8000-000000000002",
      place: 1,
      trigger: "manual",
      requested_at: "2026-11-26T09:00:00Z",
    },
  },
);
