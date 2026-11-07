import { http, HttpResponse } from "msw";

import type { IngestionDocument } from "@/features/ingestion/api/documents-api";
import type { Review } from "@/features/ingestion/api/proposals-api";
import { fakeDocument, fakeDocuments } from "@/test/ingestion";
import { fakeEstimate, fakeRun, fakeStep } from "@/test/ingestion-runs";
import { server } from "@/test/server";

/** The text of Camille's CV, as the reader extracted it: what the proposals quote. */
const CV_TEXT = `Camille Martin
Senior data engineer, Lyon
camille@example.org

Experience
Lumen Freight, senior data engineer, April 2022 to today.
Real time parcel tracking on Kafka and Flink.
Vellum Analytics, data engineer, September 2019 to March 2022.
Migrated 140 models to dbt; the nightly build went from 3 hours to 25 minutes.

Skills
Python, SQL, dbt, Airflow, Kafka, Flink, BigQuery, Terraform.
`;

const NOTES_TEXT = `# Projects

Real time parcel tracking, 2024: delays seen in seconds, not hours.
`;

/** What Camille added to fill her profile: her CV and her notes on her projects. */
export function camilleDocuments(): IngestionDocument[] {
  return [
    fakeDocument({
      id: "doc-cv",
      name: "camille-martin-cv.pdf",
      kind: "pdf",
      mime: "application/pdf",
      size: 184_320,
      pages: 2,
      pages_text: 2,
      pages_image: 0,
      created_at: "2026-11-04T08:12:00Z",
    }),
    fakeDocument({
      id: "doc-notes",
      name: "projects.md",
      mime: "text/markdown",
      size: 2_048,
      created_at: "2026-11-04T08:14:00Z",
    }),
  ];
}

/** The fake documents api with Camille's two documents and their text. */
export function fakeCamilleDocuments(items = camilleDocuments()) {
  const state = fakeDocuments(items);
  state.texts.set("doc-cv", CV_TEXT);
  state.texts.set("doc-notes", NOTES_TEXT);
  return state;
}

/** The estimate of a run over Camille's two documents, on a Claude subscription. */
export function camilleEstimate() {
  return fakeEstimate({
    documents: [
      {
        id: "doc-cv",
        name: "camille-martin-cv.pdf",
        characters: 6_420,
        images: 0,
        steps: 1,
        reused_steps: 0,
      },
      {
        id: "doc-notes",
        name: "projects.md",
        characters: 1_980,
        images: 0,
        steps: 1,
        reused_steps: 0,
      },
    ],
    characters: 8_400,
  });
}

const STEPS = {
  cv: { document_id: "doc-cv", document_name: "camille-martin-cv.pdf" },
  notes: { document_id: "doc-notes", document_name: "projects.md" },
};

/** A run halfway: the CV read, the notes being read. */
export function runningRun() {
  return fakeRun({
    document_ids: ["doc-cv", "doc-notes"],
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        tokens: 41_200,
        ...STEPS.cv,
      }),
      fakeStep({ position: 1, status: "running", ...STEPS.notes }),
    ],
    tokens: { spent: 41_200 },
    counts: { items: 9 },
  });
}

/** The same run, finished, with what it proposes. */
export function doneRun() {
  return fakeRun({
    status: "done",
    finished_at: "2026-11-04T08:31:00Z",
    document_ids: ["doc-cv", "doc-notes"],
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        tokens: 41_200,
        ...STEPS.cv,
      }),
      fakeStep({
        position: 1,
        status: "done",
        items: 3,
        tokens: 12_800,
        ...STEPS.notes,
      }),
    ],
    tokens: { spent: 54_000 },
    counts: {
      running: 0,
      done: 2,
      items: 12,
      proposals: 5,
    },
  });
}

/** A run stopped at its token ceiling, waiting for the person to continue or stop. */
export function pausedRun() {
  return fakeRun({
    status: "paused",
    paused_reason: "token_ceiling",
    document_ids: ["doc-cv", "doc-notes"],
    tokens: { spent: 360_000, ceiling: 352_440, continue_adds: 120_000 },
    counts: { running: 0, planned: 1, done: 1, items: 9 },
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        tokens: 360_000,
        ...STEPS.cv,
      }),
      fakeStep({ position: 1, status: "planned", ...STEPS.notes }),
    ],
  });
}

const CV_REF = { id: "doc-cv", name: "camille-martin-cv.pdf", deleted: false };
const NOTES_REF = { id: "doc-notes", name: "projects.md", deleted: false };

function cite(document: typeof CV_REF, text: string, excerpt: string) {
  const start = Math.max(0, text.indexOf(excerpt));
  return {
    document_id: document.id,
    document,
    excerpt,
    excerpt_cut: false,
    start,
    end: start + excerpt.length,
    transcribed: false,
    source_deleted: false,
  };
}

type Item = Review["sections"][number]["items"][number];

function proposal(
  section: string,
  key: string,
  value: unknown,
  excerpt: string,
  more: Partial<Item> = {},
): Item {
  return {
    id: `p-${key}`,
    status: "pending",
    section,
    key,
    slot: key.split(":")[0] ?? key,
    action: "add",
    value,
    before: null,
    after: value,
    noop: false,
    stale: false,
    stale_reason: null,
    locked: false,
    bulk: null,
    problem: null,
    flags: [],
    free_text: false,
    citations: [cite(CV_REF, CV_TEXT, excerpt)],
    conflict: [],
    depends_on: [],
    base_revision: 1,
    applied_revision: null,
    ...more,
  };
}

const ITEMS: Item[] = [
  proposal(
    "identity",
    "headline",
    "Senior data engineer",
    "Senior data engineer",
  ),
  proposal("identity", "city", "Lyon", "Lyon"),
  proposal(
    "experience",
    "employers:lumen-freight-2022",
    {
      organisation: "Lumen Freight",
      title: "Senior data engineer",
      start: "2022-04",
      end: null,
    },
    "Lumen Freight, senior data engineer, April 2022 to today.",
    { locked: true, bulk: "locked" },
  ),
  proposal(
    "experience",
    "employers:vellum-analytics-2019",
    {
      organisation: "Vellum Analytics",
      title: "Data engineer",
      start: "2019-09",
      end: "2022-03",
    },
    "Vellum Analytics, data engineer, September 2019 to March 2022.",
    { locked: true, bulk: "locked" },
  ),
  proposal("skills", "years_experience", 7, "April 2022 to today", {
    flags: ["derived"],
    bulk: "flagged",
    citations: [
      cite(CV_REF, CV_TEXT, "April 2022 to today"),
      cite(NOTES_REF, NOTES_TEXT, "Real time parcel tracking, 2024"),
    ],
  }),
];

function counts(items: Item[]) {
  return {
    total: items.length,
    pending: items.filter((one) => one.status === "pending").length,
    accepted: items.filter((one) => one.status === "accepted").length,
    rejected: 0,
    superseded: 0,
    invalid: 0,
    undone: 0,
  };
}

/** What the finished run proposes for Camille's profile, served for any run. */
export function fakeCamilleProposals(items: Item[] = ITEMS) {
  const sections = [...new Set(items.map((one) => one.section))];
  const review: Review = {
    run_id: "r1",
    status: "done",
    partial: false,
    newer_run: false,
    profile_revision: 3,
    counts: counts(items),
    documents: [CV_REF, NOTES_REF],
    sections: sections.map((section) => {
      const mine = items.filter((one) => one.section === section);
      return { section, counts: counts(mine), items: mine };
    }),
  };
  server.use(
    http.get("*/api/ingestion/runs/:id/proposals", () =>
      HttpResponse.json(review),
    ),
  );
}
