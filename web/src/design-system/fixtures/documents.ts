import { http, HttpResponse } from "msw";

import type { Message } from "@/features/documents/api/chat-api";
import type {
  DocumentOut,
  VersionSummary,
} from "@/features/documents/api/documents-api";
import type { PostingDocument } from "@/features/documents/api/posting-documents-api";
import type { Consent } from "@/features/documents/api/consent-api";
import type { Template } from "@/features/documents/api/templates-api";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { fakeCv, fakeDocumentOf, fakeDocuments } from "@/test/documents";
import { fakeJobDetail } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";
import { TEMPLATES } from "@/test/templates";

import { camilleProfile } from "./camille-profile";

const svg = (body: string) =>
  new HttpResponse(body, { headers: { "Content-Type": "image/svg+xml" } });

/** Grey bars standing for lines of text, `count` of them from `y`, `gap` apart. */
const lines = (x: number, y: number, width: number, count: number, gap = 14) =>
  Array.from(
    { length: count },
    (_, index) =>
      `<rect x="${String(x)}" y="${String(y + index * gap)}" width="${String(index % 4 === 3 ? width * 0.6 : width)}" height="6" rx="3" fill="#d4d4d8"/>`,
  ).join("");

/** An invented A4 page: a CV (one column or with a sidebar) or a letter, in the template's accent. */
function page(kind: "cv" | "letter", accent: string, sidebar: boolean) {
  const content =
    kind === "letter"
      ? `<rect x="48" y="48" width="180" height="14" rx="4" fill="${accent}"/>
${lines(48, 76, 140, 3)}
${lines(360, 140, 186, 2)}
${lines(48, 210, 499, 7)}
${lines(48, 330, 499, 8)}
${lines(48, 464, 499, 6)}
<rect x="48" y="580" width="120" height="10" rx="4" fill="${accent}"/>`
      : sidebar
        ? `<rect width="190" height="842" fill="${accent}" opacity="0.14"/>
<circle cx="95" cy="100" r="44" fill="${accent}" opacity="0.5"/>
${lines(32, 170, 126, 6)}
${lines(32, 280, 126, 8)}
<rect x="222" y="56" width="240" height="18" rx="4" fill="#18181b"/>
<rect x="222" y="84" width="160" height="10" rx="4" fill="${accent}"/>
${lines(222, 130, 330, 6)}
${lines(222, 240, 330, 9)}
${lines(222, 390, 330, 9)}`
        : `<rect width="595" height="120" fill="${accent}" opacity="0.14"/>
<rect x="48" y="40" width="260" height="20" rx="4" fill="#18181b"/>
<rect x="48" y="72" width="180" height="10" rx="4" fill="${accent}"/>
<rect x="48" y="150" width="120" height="10" rx="4" fill="${accent}"/>
${lines(48, 172, 499, 5)}
<rect x="48" y="260" width="120" height="10" rx="4" fill="${accent}"/>
${lines(48, 282, 499, 9)}
<rect x="48" y="430" width="120" height="10" rx="4" fill="${accent}"/>
${lines(48, 452, 499, 7)}
<rect x="48" y="560" width="120" height="10" rx="4" fill="${accent}"/>
${lines(48, 582, 499, 4)}`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842" viewBox="0 0 595 842"><rect width="595" height="842" fill="#ffffff"/>${content}</svg>`;
}

const ACCENTS: Record<string, string> = {
  band: "#262525",
  ledger: "#000000",
  "photo-columns": "#07113c",
};

/** Serves every template's thumbnail as an invented page drawn in its accent. */
export function fakeTemplateThumbnails(templates: readonly Template[]) {
  server.use(
    http.get(
      "*/api/document-templates/:slug/thumbnail",
      ({ params, request }) => {
        const slug = String(params.slug);
        const template = templates.find((one) => one.slug === slug);
        const kind =
          new URL(request.url).searchParams.get("kind") === "letter"
            ? "letter"
            : "cv";
        return svg(
          page(
            kind,
            ACCENTS[slug] ?? template?.accent ?? "#1f4e79",
            template?.tags.includes("two columns") ?? false,
          ),
        );
      },
    ),
  );
}

/** Serves the first page of every version: a letter for a document whose id starts so, else a CV. */
export function fakePageImages() {
  server.use(
    http.get("*/api/documents/:id/versions/:number/page1.png", ({ params }) =>
      svg(
        page(
          String(params.id).startsWith("letter") ? "letter" : "cv",
          "#1f4e79",
          false,
        ),
      ),
    ),
  );
}

/** The text of Camille's CV written for a posting (camille-profile.ts), as the writer drafts it. */
export const CV_DRAFT: Record<string, unknown> = {
  schema: 1,
  target: "Senior data engineer, freight data platform",
  headline: "Senior data engineer, streaming and analytics platforms",
  summary:
    "Data engineer with seven years on batch and streaming pipelines. Builds the platforms analysts trust: tested dbt models, Kafka streams and clear ownership.",
  highlights: [
    "Cut the nightly load from four hours to forty minutes",
    "Moved twelve teams onto one dbt project",
  ],
  competencies: ["Python", "SQL", "dbt", "Kafka", "Airflow", "BigQuery"],
  experience: [
    {
      employer_key: "lumen-freight",
      bullets: [
        "Runs the streaming platform behind live shipment tracking",
        "Wrote the data contracts every producing team signs",
      ],
    },
    {
      employer_key: "vellum-analytics",
      bullets: ["Built the client reporting warehouse on BigQuery"],
    },
  ],
  skills: [
    { label: "Languages", items: ["Python", "SQL", "Scala"] },
    { label: "Tools", items: ["dbt", "Airflow", "Kafka", "Terraform"] },
  ],
  notes: "",
};

/** The text of a cover letter written for a posting. */
export const LETTER_DRAFT: Record<string, unknown> = {
  schema: 1,
  target: "Senior data engineer, freight data platform",
  headline: "Senior data engineer",
  recipient: "Hiring team, Lumen Freight",
  subject: "Application for the senior data engineer role",
  salutation: "Hello,",
  paragraphs: [
    "Your team is rebuilding shipment tracking on streaming data, which is the work I have done for the last three years.",
    "I would bring tested pipelines, data contracts that producing teams sign, and the habit of explaining every number.",
  ],
  closing: "Kind regards,",
};

/** A short conversation with the chat of a document: one change asked, one answered. */
export const CHAT_MESSAGES: Message[] = [
  {
    id: 1,
    channel: "content",
    role: "user",
    text: "Make the summary shorter",
    generation_id: "g-chat-1",
    version_number: null,
    at: "2026-11-05T09:20:00Z",
  },
  {
    id: 2,
    channel: "content",
    role: "assistant",
    text: "Changed the summary.\nThis is version 2.",
    generation_id: "g-chat-1",
    version_number: 2,
    at: "2026-11-05T09:21:00Z",
  },
];

/**
 * What the document view reads besides the document: the person's profile (the facts printed as
 * they are), the templates with their thumbnails, and the chat's `messages` of every document.
 */
export function fakeDocumentReads(messages: Message[] = []) {
  fakeProfile(camilleProfile());
  fakeTemplateThumbnails(TEMPLATES);
  server.use(
    http.get("*/api/document-templates", () =>
      HttpResponse.json({ templates: TEMPLATES }),
    ),
    http.get("*/api/documents/:id/messages", () =>
      HttpResponse.json({ messages }),
    ),
  );
}

/** The posting's CV or letter as GET /api/jobs/{id}/documents lists it. */
export function postingDocument(
  kind: "cv" | "letter",
  postingId: string,
  overrides: Partial<PostingDocument> = {},
): PostingDocument {
  return {
    ...fakeDocumentOf(fakeCv({ id: `${kind}-doc` }), {
      latest: null,
      draft: kind === "cv" ? CV_DRAFT : LETTER_DRAFT,
    }),
    scope: "posting",
    kind,
    posting_id: postingId,
    base_document_id: kind === "cv" ? "cv-1" : null,
    name: null,
    approved: null,
    sent: [],
    generation: null,
    ...overrides,
  };
}

/**
 * A posting and its documents: the job, the list of its documents, each document with its
 * versions (the latest first) and the image of each version's first page, and what the document
 * view reads besides (`fakeDocumentReads`): the chat's `messages`, under `consent`.
 */
export function fakePostingDocuments(
  job: JobDetail,
  documents: PostingDocument[],
  history: VersionSummary[] = [],
  chat: { consent?: Partial<Consent>; messages?: Message[] } = {},
) {
  fakeJobDetail(job);
  fakeDocumentReads(chat.messages);
  const api = fakeDocuments({ cvs: [fakeCv()], consent: chat.consent });
  for (const one of documents) {
    api.documents.set(one.id, one);
    api.versions.set(one.id, one.latest ? [one.latest, ...history] : []);
  }
  server.use(
    http.get("*/api/jobs/:id/documents", () =>
      HttpResponse.json({ documents }),
    ),
  );
  fakePageImages();
  return api;
}

/**
 * One document served on its own, as the document view reads it: the document, its versions
 * (the latest first), its page images, saves of its draft, and what the view reads besides.
 */
export function fakeOneDocument(
  document: DocumentOut,
  options: {
    versions?: VersionSummary[];
    consent?: Partial<Consent>;
    messages?: Message[];
    corrections?: unknown[];
  } = {},
) {
  fakeDocumentReads(options.messages);
  const api = fakeDocuments({ cvs: [fakeCv()], consent: options.consent });
  api.documents.set(document.id, document);
  api.versions.set(
    document.id,
    options.versions ?? (document.latest ? [document.latest] : []),
  );
  if (options.corrections)
    api.corrections.set(document.id, options.corrections);
  server.use(
    http.put("*/api/documents/:id/draft", async ({ request, params }) => {
      const id = String(params.id);
      const current = api.documents.get(id) ?? document;
      const saved: DocumentOut = {
        ...current,
        draft: (await request.json()) as Record<string, unknown>,
        draft_revision: current.draft_revision + 1,
        draft_written_by: "user",
      };
      api.documents.set(id, saved);
      return HttpResponse.json(saved);
    }),
  );
  fakePageImages();
  return api;
}
