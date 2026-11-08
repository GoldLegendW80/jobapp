import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { CoverageNote } from "@/components/shared/documents/document-view/coverage-note";
import { postingDocument } from "@/design-system/fixtures/documents";
import { createQueryClient } from "@/lib/query";
import { fakeVersion } from "@/test/documents";
import { server } from "@/test/server";

import type { DocumentOut } from "@/features/documents/api/documents-api";

function fakeCoverage(missing: string[]) {
  const reads: string[] = [];
  server.use(
    http.get("*/api/documents/:id/coverage", ({ params }) => {
      reads.push(String(params.id));
      return HttpResponse.json({ draft_revision: 1, terms: missing, missing });
    }),
  );
  return reads;
}

const show = (document: DocumentOut) =>
  render(
    <QueryClientProvider client={createQueryClient()}>
      <CoverageNote document={document} />
    </QueryClientProvider>,
  );

const cv = postingDocument("cv", "job-1", {
  latest: fakeVersion({ number: 1 }),
});

describe("CoverageNote", () => {
  it("names the missing terms of a posting's CV", async () => {
    fakeCoverage(["Kubernetes", "Airflow"]);
    show(cv);
    expect(await screen.findByTestId("coverage-note")).toHaveTextContent(
      "Kubernetes and Airflow, named by the posting, are not in this CV. Add one only if it is true of you.",
    );
  });

  it("shows nothing when every term is printed", async () => {
    const reads = fakeCoverage([]);
    show(cv);
    await waitFor(() => {
      expect(reads).toEqual([cv.id]);
    });
    expect(screen.queryByTestId("coverage-note")).toBeNull();
  });

  it("never asks for a letter", () => {
    const reads = fakeCoverage(["Kubernetes"]);
    show(
      postingDocument("letter", "job-1", {
        latest: fakeVersion({ number: 1 }),
      }),
    );
    expect(screen.queryByTestId("coverage-note")).toBeNull();
    expect(reads).toEqual([]);
  });
});
