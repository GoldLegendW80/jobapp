/**
 * A fake gallery for tests: GET /api/document-templates, and the template switch and "Make it
 * fit" of a document kept in a `fakeDocuments` state. The templates are shaped like the bundled
 * manifests (tags, photo, names in both languages) with invented descriptions.
 */
import { http, HttpResponse } from "msw";

import type { DocumentOut, Template } from "@/features/documents/documents-api";

import type { FakeDocuments } from "./documents";
import { server } from "./server";

export function fakeTemplate(
  slug: string,
  overrides: Partial<Template> = {},
): Template {
  const name = slug.charAt(0).toUpperCase() + slug.slice(1);
  return {
    slug,
    version: 1,
    user: false,
    name: { en: name, fr: `${name} FR` },
    description: {
      en: `The ${slug} look, in English.`,
      fr: `Le style ${slug}, en français.`,
    },
    tags: ["one column", "sans"],
    photo: "optional",
    accent: "#1f4e79",
    kinds: ["cv", "letter"],
    thumbnails: {
      cv: `/api/document-templates/${slug}/thumbnail?kind=cv&v=1`,
      letter: `/api/document-templates/${slug}/thumbnail?kind=letter&v=1`,
    },
    ...overrides,
  };
}

/** Bundled ones in the api's order (by slug), then the person's own, as the api lists them. */
export const TEMPLATES: Template[] = [
  fakeTemplate("banner", { tags: ["one column", "sans", "photo", "colour"] }),
  fakeTemplate("classic", {
    tags: ["one column", "sans", "ats friendly", "photo"],
  }),
  fakeTemplate("compact", {
    tags: ["one column", "sans", "no photo", "compact", "ats friendly"],
    photo: "none",
  }),
  fakeTemplate("sidebar", { tags: ["two columns", "sans", "photo", "colour"] }),
  fakeTemplate("serif", {
    tags: ["one column", "serif", "no photo", "ats friendly"],
    photo: "none",
  }),
  fakeTemplate("upload-abcd1234", {
    user: true,
    name: { en: "My uploaded look", fr: "My uploaded look" },
    description: {},
    tags: [],
    photo: "none",
    accent: null,
    thumbnails: {},
  }),
];

export interface FakeGallery {
  templates: Template[];
  /** The answer of the next PATCH or fit, when a test wants a failure. */
  patchStatus?: number;
  fitStatus?: number;
}

/**
 * Serves the gallery, and switches the template of a document of `documents` on PATCH: the
 * answer is the document with the new slug, a render queued, and `after` applied (an overflow).
 */
export function fakeGallery(
  documents: FakeDocuments,
  options: {
    templates?: Template[];
    after?: (document: DocumentOut) => DocumentOut;
  } = {},
): FakeGallery {
  const state: FakeGallery = { templates: options.templates ?? TEMPLATES };
  const record = async (request: Request) => {
    const text = await request.clone().text();
    documents.requests.push({
      method: request.method,
      path: new URL(request.url).pathname,
      body: text === "" ? undefined : (JSON.parse(text) as unknown),
      headers: request.headers,
    });
    return text === "" ? {} : (JSON.parse(text) as Record<string, unknown>);
  };
  server.use(
    http.get("*/api/document-templates", async ({ request }) => {
      await record(request);
      return HttpResponse.json({ templates: state.templates });
    }),
    http.patch("*/api/documents/:id", async ({ request, params }) => {
      const body = await record(request);
      const id = String(params.id);
      if (state.patchStatus !== undefined)
        return HttpResponse.json(
          { detail: "This template does not exist." },
          { status: state.patchStatus },
        );
      const current = documents.documents.get(id);
      if (!current)
        return HttpResponse.json({ detail: "Not found." }, { status: 404 });
      const switched: DocumentOut = {
        ...current,
        template_slug:
          typeof body.template_slug === "string"
            ? body.template_slug
            : current.template_slug,
        render: { state: "queued", revision: 2, error: null, message: null },
      };
      const rendered = (options.after ?? ((one) => one))({
        ...switched,
        render: { state: "idle", revision: 2, error: null, message: null },
      });
      // The answer says queued; the next read finds it drawn.
      documents.documents.set(id, rendered);
      return HttpResponse.json(switched);
    }),
    http.post("*/api/documents/:id/fit", async ({ request }) => {
      await record(request);
      if (state.fitStatus !== undefined)
        return HttpResponse.json(
          { detail: "Not Found" },
          { status: state.fitStatus },
        );
      return HttpResponse.json(
        { id: "g-fit", status: "queued", wait_reason: null, error: null },
        { status: 202 },
      );
    }),
  );
  return state;
}
