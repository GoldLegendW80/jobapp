import { expect, type APIRequestContext } from "@playwright/test";

import type { DocumentOut } from "./documents-helpers";

/** The finished CV and letter share the fixture's original 150 second render budget.
 * A regeneration waits for both versions to advance beyond the previous ready pair. */
export async function waitForPostingDocuments(
  request: APIRequestContext,
  posting: string,
  previous: Partial<Record<DocumentOut["kind"], number>> = {},
): Promise<void> {
  let documents: DocumentOut[] = [];
  await expect
    .poll(
      async () => {
        const response = await request.get(`/api/jobs/${posting}/documents`);
        expect(response.status()).toBe(200);
        documents = ((await response.json()) as { documents: DocumentOut[] })
          .documents;
        // As in waitForRender, a terminal render error stops the wait and is asserted below.
        return (
          documents.some(
            (document) =>
              document.render.state === "idle" &&
              document.render.error !== null &&
              (previous[document.kind] === undefined ||
                (document.latest?.number ?? 0) >
                  (previous[document.kind] ?? 0)),
          ) ||
          (documents.length === 2 &&
            documents.every(
              (document) =>
                document.latest !== null &&
                document.latest.number > (previous[document.kind] ?? 0) &&
                document.render.state === "idle" &&
                document.render.error === null &&
                document.latest.has_pdf,
            ))
        );
      },
      { timeout: 150_000 },
    )
    .toBe(true);
  for (const document of documents)
    expect(document.render.error, document.render.message ?? "").toBeNull();
  expect(documents).toHaveLength(2);
  for (const document of documents) {
    expect(document.latest).not.toBeNull();
    expect(document.render.state).toBe("idle");
    expect(document.latest?.has_pdf).toBe(true);
  }
}
