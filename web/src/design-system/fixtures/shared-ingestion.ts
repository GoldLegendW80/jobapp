import { delay, http, HttpResponse } from "msw";

import type { IngestionDocument } from "@/features/ingestion/api/documents-api";
import type { Consent, Run } from "@/features/ingestion/api/runs-api";
import type { UploadItem, Uploads } from "@/features/ingestion/api/use-uploads";
import { fakeDocument } from "@/test/ingestion";
import {
  CONSENT_TEXT,
  fakeRun,
  fakeRuns,
  fakeStep,
  REMINDER,
} from "@/test/ingestion-runs";
import { server } from "@/test/server";

import {
  camilleDocuments,
  camilleEstimate,
  fakeCamilleDocuments,
} from "./camille-ingestion";

/** Camille's documents in every state a row can show, newest first. */
export function everyDocumentState(): IngestionDocument[] {
  return [
    fakeDocument({
      id: "doc-reading",
      name: "portfolio-2026.pdf",
      kind: "pdf",
      mime: "application/pdf",
      size: 2_621_440,
      status: "pending",
      created_at: "2026-11-05T09:40:00Z",
    }),
    fakeDocument({
      id: "doc-secret",
      name: "server-notes.txt",
      size: 3_412,
      secret_warning: true,
      created_at: "2026-11-05T09:30:00Z",
    }),
    fakeDocument({
      id: "doc-photo",
      name: "certificate-photo.jpg",
      kind: "image",
      mime: "image/jpeg",
      size: 1_310_720,
      status: "awaiting_transcription",
      pages_image: 1,
      created_at: "2026-11-05T09:20:00Z",
    }),
    fakeDocument({
      id: "doc-scan",
      name: "diploma-scan.pdf",
      kind: "pdf",
      mime: "application/pdf",
      size: 912_384,
      status: "failed",
      pages: 1,
      pages_text: 0,
      failed_reason:
        "No text could be read from this scan. Add a PDF with selectable text, or a photo of each page.",
      created_at: "2026-11-05T09:10:00Z",
    }),
    fakeDocument({
      id: "doc-keynote",
      name: "talk-slides.key",
      kind: "other",
      mime: "application/octet-stream",
      size: 8_912_896,
      status: "pending",
      readable: false,
      created_at: "2026-11-05T09:00:00Z",
    }),
    fakeDocument({
      id: "doc-pasted",
      name: "Pasted text",
      source: "pasted",
      size: 1_204,
      created_at: "2026-11-05T08:50:00Z",
    }),
    ...camilleDocuments(),
  ];
}

/** A document whose name runs long, as an export from another tool names it. */
export const LONG_NAME_DOCUMENT = fakeDocument({
  id: "doc-long",
  name: "camille-martin-curriculum-vitae-senior-data-engineer-lyon-version-for-logistics-companies-november-2026-final-final.pdf",
  kind: "pdf",
  mime: "application/pdf",
  size: 248_000,
  pages: 3,
  pages_text: 2,
  pages_image: 1,
  note: "The third page is a photo: it is kept as an image for the agent.",
  created_at: "2026-11-05T10:00:00Z",
});

/** The upload queue of the drop zone as a page would hand it over, without sending anything. */
export function staticUploads(
  items: UploadItem[] = [],
  announcement = "",
): Uploads {
  return {
    items,
    announcement,
    add: () => undefined,
    refuse: () => undefined,
    remove: () => undefined,
  };
}

/** Three files on their way: one sending, one the server is storing, one waiting its turn. */
export const UPLOADS_UNDER_WAY: UploadItem[] = [
  {
    id: 1,
    name: "camille-martin-cv.pdf",
    size: 184_320,
    state: "uploading",
    progress: 1,
  },
  {
    id: 2,
    name: "linkedin-export.zip",
    size: 6_291_456,
    state: "uploading",
    progress: 0.42,
  },
  {
    id: 3,
    name: "projects.md",
    size: 2_048,
    state: "queued",
    progress: 0,
  },
];

/** What stays in the list until it is dismissed: a refusal, a folder, a file already there. */
export const UPLOADS_REFUSED: UploadItem[] = [
  {
    id: 4,
    name: "team-photo.gif",
    size: 402_112,
    state: "failed",
    progress: 0,
    message:
      "GIF images cannot be read. Convert the image to PNG or JPEG and add it again.",
  },
  {
    id: 5,
    name: "Old applications",
    size: 0,
    state: "failed",
    progress: 0,
    message: "A folder cannot be added. Open it and add the files inside.",
  },
  {
    id: 6,
    name: "projects.md",
    size: 2_048,
    state: "duplicate",
    progress: 1,
    message: "projects.md is already in your documents.",
  },
];

/** The text of a two page PDF whose second page is a scan, kept as an image. */
const TWO_PAGES = `Camille Martin
Senior data engineer, Lyon

Lumen Freight, senior data engineer, April 2022 to today.
Real time parcel tracking on Kafka and Flink.\f`;

/** A page of a scanned document, drawn: no real document. */
const PAGE_IMAGE = `<svg xmlns="http://www.w3.org/2000/svg" width="620" height="800" viewBox="0 0 620 800">
<rect width="620" height="800" fill="#fafaf9"/>
<text x="48" y="80" font-family="serif" font-size="26" fill="#1c1917">Certificate of completion</text>
${Array.from(
  { length: 14 },
  (_, line) =>
    `<rect x="48" y="${String(130 + line * 34)}" width="${String(420 + ((line * 37) % 100))}" height="10" rx="5" fill="#d6d3d1"/>`,
).join("\n")}
<text x="48" y="700" font-family="serif" font-size="18" fill="#57534e">Lyon, 12 March 2024</text>
</svg>`;

/** A long note, to show a preview that loads its text a part at a time. */
export const LONG_TEXT = Array.from(
  { length: 40 },
  (_, n) =>
    `Project ${String(n + 1)}. Built a pipeline that moved parcel events from the depots to the tracking page, measured the delay at each stage and cut it from minutes to seconds.`,
).join("\n\n");

/**
 * The documents api with Camille's documents and the texts the preview shows: her CV, her notes,
 * the long note, a two page PDF with a page kept as an image, and a photo with no text yet.
 */
export function fakePreviewDocuments() {
  const twoPages = fakeDocument({
    id: "doc-two-pages",
    name: "references.pdf",
    kind: "pdf",
    mime: "application/pdf",
    size: 402_000,
    pages: 2,
    pages_text: 1,
    pages_image: 1,
  });
  const photo = everyDocumentState().find((one) => one.id === "doc-photo");
  const long = fakeDocument({
    id: "doc-long-note",
    name: "every-project.md",
    mime: "text/markdown",
    size: LONG_TEXT.length,
  });
  const empty = fakeDocument({
    id: "doc-empty",
    name: "blank-page.pdf",
    kind: "pdf",
    mime: "application/pdf",
    size: 9_800,
    pages: 1,
    pages_text: 0,
  });
  const state = fakeCamilleDocuments([
    ...camilleDocuments(),
    twoPages,
    long,
    empty,
    ...(photo ? [photo] : []),
  ]);
  state.texts.set(twoPages.id, TWO_PAGES);
  state.images.set(twoPages.id, [{ position: 1, width: 620, height: 800 }]);
  state.texts.set("doc-photo", "");
  state.images.set("doc-photo", [{ position: 0, width: 620, height: 800 }]);
  state.texts.set(long.id, LONG_TEXT);
  state.texts.set(empty.id, "");
  server.use(
    http.get(
      "*/api/ingestion/documents/:id/images/:position",
      () =>
        new HttpResponse(PAGE_IMAGE, {
          headers: { "Content-Type": "image/svg+xml" },
        }),
    ),
  );
  return state;
}

/** Every documents route answers the same failure, or never answers. */
export function failDocuments(answer: "error" | "loading") {
  const respond = async () => {
    if (answer === "loading") await delay("infinite");
    return HttpResponse.json({ detail: "Server error" }, { status: 500 });
  };
  server.use(
    http.get("*/api/ingestion/documents", respond),
    http.get("*/api/ingestion/documents/:id/text", respond),
  );
}

/** The consent text, not yet agreed to: the box shows before the first run. */
export const CONSENT_ASKED: Consent = {
  version: 1,
  text: CONSENT_TEXT,
  reminder: REMINDER,
  accepted: false,
  accepted_at: null,
};

/** The consent text agreed to: the reminder shows under the button instead. */
export const CONSENT_GIVEN: Consent = {
  ...CONSENT_ASKED,
  accepted: true,
  accepted_at: "2026-11-04T08:20:00Z",
};

/** A long consent text, as a new version may be. */
export const CONSENT_LONG: Consent = {
  ...CONSENT_ASKED,
  version: 2,
  text: `${CONSENT_TEXT} Anthropic processes it to answer and does not train its models on it. The app keeps what the agent proposes on this server until you accept or reject it, and keeps nothing else from the answer. Each run uses your own credential: on an API key it is billed to you, on a subscription it counts against your quota.`,
};

const CAMILLE_STEPS = {
  cv: { document_id: "doc-cv", document_name: "camille-martin-cv.pdf" },
  notes: { document_id: "doc-notes", document_name: "projects.md" },
};

/** A run that waits for one of the person's other agent runs to end. */
export function waitingRun(): Run {
  return fakeRun({
    document_ids: ["doc-cv", "doc-notes"],
    waiting: "places",
    steps: [
      fakeStep({ position: 0, status: "planned", ...CAMILLE_STEPS.cv }),
      fakeStep({ position: 1, status: "planned", ...CAMILLE_STEPS.notes }),
    ],
    tokens: { spent: 0 },
    counts: { running: 0, done: 0, planned: 2, items: 0 },
  });
}

/** A run asked to stop: it ends once the step in progress has ended. */
export function stoppingRun(): Run {
  return fakeRun({
    document_ids: ["doc-cv", "doc-notes"],
    cancel_requested: true,
    steps: [
      fakeStep({
        position: 0,
        status: "running",
        items: 4,
        ...CAMILLE_STEPS.cv,
      }),
      fakeStep({ position: 1, status: "planned", ...CAMILLE_STEPS.notes }),
    ],
    tokens: { spent: 18_400 },
    counts: { running: 1, done: 0, planned: 1, items: 4 },
  });
}

/** A run paused by a failure of the model: Resume starts the step again. */
export function pausedOnFailureRun(): Run {
  return fakeRun({
    status: "paused",
    paused_reason:
      "The model did not answer in time. The step that stopped is read again when you resume.",
    document_ids: ["doc-cv", "doc-notes"],
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        tokens: 41_200,
        ...CAMILLE_STEPS.cv,
      }),
      fakeStep({
        position: 1,
        status: "failed",
        attempt: 2,
        error: "The model did not answer in time.",
        ...CAMILLE_STEPS.notes,
      }),
    ],
    tokens: { spent: 41_200 },
    counts: { running: 0, done: 1, failed: 1, items: 9 },
  });
}

/** A run paused at its ceiling with nothing left to read: it closes by itself. */
export function closingRun(): Run {
  return fakeRun({
    status: "paused",
    paused_reason: "token_ceiling",
    document_ids: ["doc-cv", "doc-notes"],
    tokens: { spent: 353_100, ceiling: 352_440, continue_adds: 0 },
    counts: { running: 0, done: 2, items: 12 },
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        tokens: 300_000,
        ...CAMILLE_STEPS.cv,
      }),
      fakeStep({
        position: 1,
        status: "done",
        items: 3,
        tokens: 53_100,
        ...CAMILLE_STEPS.notes,
      }),
    ],
  });
}

/** A finished run on an API key that left documents unread, each for its own reason. */
export function partialRun(): Run {
  return fakeRun({
    status: "done",
    partial: true,
    cost_usd: "0.94",
    finished_at: "2026-11-05T10:12:00Z",
    document_ids: ["doc-cv", "doc-notes", "doc-secret", "doc-gone"],
    unread_document_ids: ["doc-notes", "doc-secret", "doc-gone"],
    secret_document_ids: ["doc-secret"],
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        refused: 1,
        tokens: 41_200,
        cost_usd: "0.71",
        ...CAMILLE_STEPS.cv,
      }),
      fakeStep({
        position: 1,
        status: "failed",
        attempt: 3,
        tokens: 9_800,
        cost_usd: "0.23",
        error:
          "The model answered three times with text the app could not read.",
        ...CAMILLE_STEPS.notes,
      }),
      fakeStep({
        position: 2,
        status: "cancelled",
        document_id: "doc-gone",
        document_name: null,
      }),
    ],
    tokens: { spent: 51_000 },
    counts: {
      running: 0,
      done: 1,
      failed: 1,
      cancelled: 1,
      items: 9,
      refused: 1,
      two_digit_year: 2,
      proposals: 8,
    },
  });
}

/** A run the engine closed after it could not move it. */
export function failedRun(): Run {
  return fakeRun({
    status: "failed",
    closed_reason:
      "The run could not go on after the server restarted twice. What it had read is kept.",
    finished_at: "2026-11-05T10:20:00Z",
    document_ids: ["doc-cv", "doc-notes"],
    unread_document_ids: ["doc-notes"],
    steps: [
      fakeStep({
        position: 0,
        status: "done",
        items: 9,
        tokens: 41_200,
        ...CAMILLE_STEPS.cv,
      }),
      fakeStep({ position: 1, status: "cancelled", ...CAMILLE_STEPS.notes }),
    ],
    tokens: { spent: 41_200 },
    counts: { running: 0, done: 1, cancelled: 1, items: 9 },
  });
}

/** A run the person cancelled before it reached the notes. */
export function cancelledRun(): Run {
  return fakeRun({
    status: "cancelled",
    finished_at: "2026-11-05T10:25:00Z",
    document_ids: ["doc-cv", "doc-notes"],
    unread_document_ids: ["doc-cv", "doc-notes"],
    steps: [
      fakeStep({ position: 0, status: "cancelled", ...CAMILLE_STEPS.cv }),
      fakeStep({ position: 1, status: "cancelled", ...CAMILLE_STEPS.notes }),
    ],
    tokens: { spent: 2_100 },
    counts: { running: 0, done: 0, cancelled: 2, items: 0 },
  });
}

/**
 * The run routes with `runs` (newest first) and Camille's documents; the consent as given. The
 * start panel asks the estimate of Camille's two documents.
 */
export function fakeShownRuns(
  runs: Run[] = [],
  consent: Consent = CONSENT_GIVEN,
) {
  fakeCamilleDocuments();
  return fakeRuns({ runs, consent, estimate: camilleEstimate() });
}

/** The run routes never answer, or answer a failure. */
export function failRuns(answer: "error" | "loading") {
  const respond = async () => {
    if (answer === "loading") await delay("infinite");
    return HttpResponse.json({ detail: "Server error" }, { status: 500 });
  };
  server.use(
    http.get("*/api/ingestion/runs", respond),
    http.get("*/api/ingestion/runs/:id", respond),
  );
}
