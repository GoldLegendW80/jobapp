import { delay, http, HttpResponse } from "msw";

import type { JobDetail, JobListItem } from "@/features/jobs/api";

import { server } from "./server";

/** Harald's status counts (data/jobs.csv, 433 rows). */
export const CSV_COUNTS: Record<string, number> = {
  found: 158,
  shortlisted: 146,
  cv_ready: 3,
  staged: 29,
  applied: 29,
  interview: 2,
  rejected: 0,
  closed: 6,
  skipped: 60,
};

const COMPANIES = ["Mistral AI", "Anthropic", "Hugging Face", "Dust", "Alan"];
const TYPES = ["CDI", "CDI", "CDI", "VIE", "Freelance"];

export function makeJob(
  index: number,
  overrides: Partial<JobListItem> = {},
): JobListItem {
  const id = `00000000-0000-4000-9000-${String(index).padStart(12, "0")}`;
  return {
    id,
    slug: `job-${index}`,
    company: COMPANIES[index % COMPANIES.length]!,
    title: `Engineer ${index}`,
    location: index % 2 ? "Paris, France" : "Remote",
    contract_type: TYPES[index % TYPES.length]!,
    url: `https://jobs.example.org/${index}`,
    source: "ashby:example",
    status: "found",
    fit: index % 37 === 0 ? null : (index * 7) % 101,
    fit_overridden: false,
    competencies: ["agents"],
    gaps: [],
    description: null,
    published_on: null,
    found_on: `2026-${String((index % 9) + 1).padStart(2, "0")}-15`,
    applied_on: null,
    notes: "",
    file_count: 0,
    created_at: "2026-10-09T08:00:00Z",
    updated_at: "2026-10-09T08:00:00Z",
    ...overrides,
  };
}

/** 433 rows with exactly the CSV's status counts. */
export function csvJobs(): JobListItem[] {
  const jobs: JobListItem[] = [];
  for (const [status, count] of Object.entries(CSV_COUNTS))
    for (let i = 0; i < count; i += 1)
      jobs.push(makeJob(jobs.length + 1, { status }));
  return jobs;
}

export function makeDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    ...makeJob(1),
    posting_text: "First paragraph.\n\nSecond paragraph\nwith a line break.",
    legacy: { jd: "data/jobs/x/posting.txt" },
    files: [],
    events: [
      {
        id: 1,
        at: "2026-10-09T08:00:00Z",
        kind: "created",
        data: { slug: "job-1", via: "import" },
      },
    ],
    events_total: 1,
    ...overrides,
  };
}

function histogram(rows: JobListItem[]) {
  const buckets = Array.from({ length: 10 }, (_, i) => ({
    low: i * 10,
    high: i === 9 ? 100 : i * 10 + 9,
    count: 0,
  }));
  let unscored = 0;
  for (const row of rows) {
    if (row.fit === null) unscored += 1;
    else buckets[Math.min(9, Math.floor(row.fit / 10))]!.count += 1;
  }
  return { buckets, unscored };
}

/**
 * A fake GET /api/jobs over `rows`: status, type, q (substring of company or title), company,
 * fit range and found dates filter; the cursor is the offset. Every request's query is recorded.
 */
export function fakeJobsApi(
  rows: JobListItem[],
  options: {
    respond?: (
      params: URLSearchParams,
    ) => Response | undefined | Promise<Response | undefined>;
    latencyMs?: number;
  } = {},
) {
  const state = { rows, requests: [] as URLSearchParams[] };
  server.use(
    http.get("*/api/jobs", async ({ request }) => {
      const params = new URL(request.url).searchParams;
      state.requests.push(params);
      if (options.latencyMs) await delay(options.latencyMs);
      const override = await options.respond?.(params);
      if (override) return override;
      const statuses = params.getAll("status");
      const types = params.getAll("type");
      const q = params.get("q")?.toLowerCase();
      const company = params.get("company")?.toLowerCase();
      const fitMin = params.get("fit_min");
      const fitMax = params.get("fit_max");
      const from = params.get("found_from");
      const to = params.get("found_to");
      const base = state.rows.filter(
        (row) =>
          (!types.length || types.includes(row.contract_type)) &&
          (!q || `${row.company} ${row.title}`.toLowerCase().includes(q)) &&
          (!company || row.company.toLowerCase().includes(company)) &&
          (fitMin === null ||
            (row.fit !== null && row.fit >= Number(fitMin))) &&
          (fitMax === null ||
            (row.fit !== null && row.fit <= Number(fitMax))) &&
          (!from || row.found_on >= from) &&
          (!to || row.found_on <= to),
      );
      const counts: Record<string, number> = {};
      for (const status of Object.keys(CSV_COUNTS)) counts[status] = 0;
      for (const row of base)
        counts[row.status] = (counts[row.status] ?? 0) + 1;
      const matching = base.filter(
        (row) => !statuses.length || statuses.includes(row.status),
      );
      const limit = Number(params.get("limit") ?? 50);
      const offset = Number(params.get("cursor") ?? 0);
      const next = offset + limit;
      return HttpResponse.json({
        items: matching.slice(offset, next),
        next_cursor: next < matching.length ? String(next) : null,
        total: matching.length,
        counts_by_status: counts,
        fit_histogram: histogram(matching),
      });
    }),
  );
  return state;
}

/** A fake GET /api/jobs/{id} (and its files) for one job. */
export function fakeJobDetail(
  job: JobDetail,
  files: Record<string, () => Response> = {},
) {
  const state = { detailCalls: 0, fileCalls: [] as string[] };
  server.use(
    http.get("*/api/jobs/:id", ({ params }) => {
      state.detailCalls += 1;
      if (params.id !== job.id)
        return HttpResponse.json({ detail: "Job not found." }, { status: 404 });
      return HttpResponse.json(job);
    }),
    http.get("*/api/jobs/:id/files/:fileId", ({ params }) => {
      const fileId = String(params.fileId);
      state.fileCalls.push(fileId);
      const respond = files[fileId];
      return respond
        ? respond()
        : HttpResponse.json({ detail: "File not found." }, { status: 404 });
    }),
  );
  return state;
}
