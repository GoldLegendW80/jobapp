import { delay, http, HttpResponse } from "msw";

import type { JobDetail, JobListItem } from "@/features/jobs/api/jobs-api";
import { makeDetail, makeJob } from "@/test/jobs";
import { server } from "@/test/server";

import { POSTINGS } from "./postings";

/** A title and a company long enough to wrap: the list clamps the title, the posting wraps it. */
export const LONG_TITLE_POSTING: JobListItem = makeJob(101, {
  title:
    "Senior staff data platform engineer for real time pricing, demand forecasting and customer analytics across European markets",
  company: "Compagnie Fluviale et Maritime du Rhône et de la Saône",
  location: "Villeurbanne, Auvergne-Rhône-Alpes, France",
  work_mode: "hybrid",
  contract_type: "CDI",
  status: "shortlisted",
  fit: 88,
  pay_min: 72000,
  pay_max: 85000,
  pay_currency: "EUR",
  pay_period: "year",
  competencies: ["Python", "Kafka", "dbt", "Forecasting", "Airflow"],
  gaps: ["Rust", "Maritime logistics"],
  url: "https://careers.example.org/101",
  found_on: "2026-11-05",
  published_on: "2026-11-03",
});

/** A posting that states no pay and that the app has not scored yet. */
export const NO_PAY_NO_FIT_POSTING: JobListItem = makeJob(102, {
  title: "Data analyst",
  company: "Coopérative Sillon",
  location: "Rennes, France",
  contract_type: "CDD",
  status: "found",
  fit: null,
  competencies: [],
  gaps: [],
  url: "https://careers.example.org/102",
  found_on: "2026-11-06",
});

function postingText(job: JobListItem): string {
  return `${job.company} is hiring for this role in ${job.location}. The team is small: five people who own the data the rest of the company reads every morning.

What you will do: build and run the pipelines the team relies on, work every week with the people who read your tables, and keep the platform simple and cheap to run.

What we look for: a few years with Python and SQL, a project you shipped and still look after, and the wish to learn how the business works.

How to apply: send your CV and a short letter. We answer every application within two weeks.`;
}

/** What GET /api/jobs/{id} answers for an invented posting: its own fields, a text, a short history. */
export function postingDetail(job: JobListItem): JobDetail {
  return makeDetail({
    ...job,
    posting_text: postingText(job),
    legacy: {},
    events: [
      {
        id: 1,
        at: `${job.found_on}T07:12:00Z`,
        kind: "created",
        data: { slug: job.slug, via: "sweep" },
      },
    ],
    events_total: 1,
  });
}

/**
 * GET /api/jobs/{id} for each of `rows`. Any other id or path under /api/jobs (the sweeps, the
 * feeds, the places) falls through to the next handler.
 */
export function fakePostingDetails(rows: JobListItem[] = POSTINGS) {
  const details = new Map(rows.map((row) => [row.id, postingDetail(row)]));
  server.use(
    http.get("*/api/jobs/:id", ({ params }) => {
      const detail = details.get(String(params.id));
      return detail ? HttpResponse.json(detail) : undefined;
    }),
  );
}

/** GET /api/jobs/{id} answers `status` for `id` (a 404 for a deleted posting, a 500). */
export function failPostingDetail(id: string, status: number) {
  server.use(
    http.get("*/api/jobs/:id", ({ params }) =>
      params.id === id
        ? HttpResponse.json(
            { detail: status === 404 ? "Job not found." : "Server error" },
            { status },
          )
        : undefined,
    ),
  );
}

/**
 * GET /api/jobs/{id} answers `id` only after `ms` (the api gives up at 15 seconds): the posting
 * shows as loading meanwhile, then the next handler answers.
 */
export function delayPostingDetail(id: string, ms: number) {
  server.use(
    http.get("*/api/jobs/:id", async ({ params }) => {
      if (params.id === id) await delay(ms);
      return undefined;
    }),
  );
}
