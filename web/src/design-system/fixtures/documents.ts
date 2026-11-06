import { http, HttpResponse } from "msw";

import type { VersionSummary } from "@/features/documents/api/documents-api";
import type { PostingDocument } from "@/features/documents/api/posting-documents-api";
import type { Template } from "@/features/documents/api/templates-api";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { fakeCv, fakeDocumentOf, fakeDocuments } from "@/test/documents";
import { fakeJobDetail } from "@/test/jobs";
import { server } from "@/test/server";

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
  banner: "#c2410c",
  classic: "#1f4e79",
  compact: "#3f3f46",
  sidebar: "#0f766e",
  serif: "#7c2d12",
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

/** The posting's CV or letter as GET /api/jobs/{id}/documents lists it. */
export function postingDocument(
  kind: "cv" | "letter",
  postingId: string,
  overrides: Partial<PostingDocument> = {},
): PostingDocument {
  return {
    ...fakeDocumentOf(fakeCv({ id: `${kind}-doc` }), { latest: null }),
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
 * versions (the latest first) and the image of each version's first page.
 */
export function fakePostingDocuments(
  job: JobDetail,
  documents: PostingDocument[],
  history: VersionSummary[] = [],
) {
  fakeJobDetail(job);
  const api = fakeDocuments({ cvs: [fakeCv()] });
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
}
