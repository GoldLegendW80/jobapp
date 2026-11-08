import { ListChecks } from "lucide-react";

import { useCoverage } from "@/features/documents/api/use-coverage";
import { missingSentence } from "@/features/documents/helpers/coverage-text";
import type { PanelProps } from "../document-view";

/**
 * Under a posting's CV: the terms of the posting's requirements, in the person's own words, that
 * the draft does not print (SPEC-documents.md, "Keyword coverage"). Read again at each revision
 * of the draft; a fact to weigh, never a block, and nothing at all while it is unknown.
 */
export function CoverageNote({ document }: Pick<PanelProps, "document">) {
  const enabled = document.scope === "posting" && document.kind === "cv";
  const coverage = useCoverage(document.id, document.draft_revision, enabled);
  const sentence = enabled
    ? missingSentence(coverage.data?.missing ?? [])
    : null;
  if (sentence === null) return null;
  return (
    <p
      className="flex items-start gap-2 text-sm text-muted-foreground"
      data-testid="coverage-note"
    >
      <ListChecks aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{sentence}</span>
    </p>
  );
}
