import { delay, http, HttpResponse } from "msw";

import type {
  JobDetail,
  JobEvent,
  JobFile,
} from "@/features/jobs/api/jobs-api";
import { server } from "@/test/server";

import { postingDetail } from "./posting-details";
import { POSTING_DETAIL } from "./postings";

/** The files of a posting worked on: the CV and letter written for it, an upload, an import. */
export const POSTING_FILES: JobFile[] = [
  {
    id: "00000000-0000-4000-a000-000000000011",
    name: "cv-lumen-freight.pdf",
    mime: "application/pdf",
    size: 48_213,
    sha256: "ab".repeat(32),
    origin: "generated",
    created_at: "2026-11-02T09:14:00Z",
  },
  {
    id: "00000000-0000-4000-a000-000000000012",
    name: "letter-lumen-freight.pdf",
    mime: "application/pdf",
    size: 31_870,
    sha256: "bc".repeat(32),
    origin: "generated",
    created_at: "2026-11-02T09:15:00Z",
  },
  {
    id: "00000000-0000-4000-a000-000000000013",
    name: "answers.json",
    mime: "application/json",
    size: 2_048,
    sha256: "cd".repeat(32),
    origin: "upload",
    created_at: "2026-11-03T16:40:00Z",
  },
  {
    id: "00000000-0000-4000-a000-000000000014",
    name: "score_posting.py",
    mime: "text/x-python",
    size: 5_312,
    sha256: "de".repeat(32),
    origin: "legacy",
    created_at: "2026-10-09T08:00:00Z",
  },
  {
    id: "00000000-0000-4000-a000-000000000015",
    name: "interview-notes.docx",
    mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    size: 1_204_337,
    sha256: "ef".repeat(32),
    origin: "upload",
    created_at: "2026-11-04T18:02:00Z",
  },
];

/** A file whose name runs long, as an import names them. */
export const LONG_NAMED_FILE: JobFile = {
  id: "00000000-0000-4000-a000-000000000016",
  name: "cv_camille_martin_senior_data_engineer_lumen_freight_lyon_hybrid_version_finale_relue_2026-11-02.pdf",
  mime: "application/pdf",
  size: 51_002,
  sha256: "fa".repeat(32),
  origin: "generated",
  created_at: "2026-11-02T09:20:00Z",
};

/** One event of each kind the history tells, newest first. */
export const POSTING_EVENTS: JobEvent[] = [
  {
    id: 9,
    at: "2026-11-05T10:02:00Z",
    kind: "note",
    data: { via: "bulk" },
  },
  {
    id: 8,
    at: "2026-11-04T18:05:00Z",
    kind: "note",
    data: { from: "", to: "Called the hiring manager." },
  },
  {
    id: 7,
    at: "2026-11-04T09:30:00Z",
    kind: "status",
    data: { from: "cv_ready", to: "staged" },
  },
  {
    id: 6,
    at: "2026-11-03T16:41:00Z",
    kind: "fit",
    data: { from: 84, to: 91, manual: true },
  },
  {
    id: 5,
    at: "2026-11-03T08:00:00Z",
    kind: "edit",
    data: {
      changes: {
        location: { from: "Lyon", to: "Lyon, France" },
        pay_min: { from: null, to: 55000 },
      },
    },
  },
  {
    id: 4,
    at: "2026-11-02T09:15:00Z",
    kind: "status",
    data: { from: "shortlisted", to: "cv_ready", via: "agent" },
  },
  {
    id: 3,
    at: "2026-11-02T07:20:00Z",
    kind: "fit",
    data: { from: 79, to: 84, via: "rescore" },
  },
  {
    id: 2,
    at: "2026-11-01T07:15:00Z",
    kind: "sweep",
    data: {},
  },
  {
    id: 1,
    at: "2026-11-01T07:12:00Z",
    kind: "created",
    data: { via: "sweep" },
  },
];

/** The first invented posting a few days on: notes, files, a full history, an imported field. */
export const WORKED_ON_POSTING: JobDetail = {
  ...postingDetail(POSTING_DETAIL),
  status: "staged",
  fit: 91,
  fit_overridden: true,
  notes:
    "Called the hiring manager on Tuesday: the team works in English, two days a week at the office. Send the letter before Friday.",
  files: POSTING_FILES,
  file_count: POSTING_FILES.length,
  events: POSTING_EVENTS,
  events_total: 23,
  legacy: {
    sheet_row: "jobs.csv, row 12",
    recruiter: { name: "Agence Hublot", phone_shared: false },
  },
};

/** A tiny PDF that says "Camille Martin, CV". */
const PDF = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 360 160]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 52>>stream
BT /F1 20 Tf 36 80 Td (Camille Martin, CV) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
%%EOF`;

/** What answers.json holds. */
export const ANSWERS_JSON = {
  notice_period: "Three months.",
  salary_expectation: { min: 55000, max: 65000, currency: "EUR" },
  work_authorization: "French citizen, no sponsorship needed.",
};

/**
 * GET /api/jobs/{id}/files/{fileId}: a PDF answers a small PDF, a JSON file its content. `broken`
 * answers a 500 for every file; `notPdf` answers text where a PDF is expected.
 */
export function fakeJobFiles(how: "ok" | "broken" | "notPdf" | "slow" = "ok") {
  const files = new Map(
    [...POSTING_FILES, LONG_NAMED_FILE].map((file) => [file.id, file]),
  );
  server.use(
    http.get("*/api/jobs/:id/files/:fileId", async ({ params }) => {
      const file = files.get(String(params.fileId));
      if (!file)
        return HttpResponse.json(
          { detail: "File not found." },
          { status: 404 },
        );
      if (how === "slow") await delay("infinite");
      if (how === "broken")
        return HttpResponse.json({ detail: "Server error" }, { status: 500 });
      if (file.mime === "application/json")
        return HttpResponse.json(ANSWERS_JSON);
      if (how === "notPdf") return HttpResponse.text("Not a PDF at all.");
      return new HttpResponse(PDF, {
        headers: { "Content-Type": "application/pdf" },
      });
    }),
  );
}
