import type { IngestionDocument } from "@/features/ingestion/api/documents-api";
import type { Run } from "@/features/ingestion/api/runs-api";
import {
  DELETED_DOCUMENT,
  whyUnread,
} from "@/features/ingestion/helpers/run-words";

/** The selected documents of an ended run that have nothing read, each with why. */
export function UnreadDocuments({
  run,
  documents,
}: {
  run: Run;
  /** The person's documents, for the name of one the run never reached. */
  documents: readonly IngestionDocument[] | undefined;
}) {
  const nameOf = (documentId: string): string => {
    const step = run.steps.find((one) => one.document_id === documentId);
    if (step) return step.document_name ?? DELETED_DOCUMENT;
    return (
      documents?.find((one) => one.id === documentId)?.name ?? DELETED_DOCUMENT
    );
  };
  return (
    <section aria-label="Documents that were not read">
      <h4 className="font-semibold">Not read</h4>
      <ul className="mt-1 flex flex-col gap-1">
        {run.unread_document_ids.map((documentId) => (
          <li key={documentId} className="break-words">
            <span className="font-medium">{nameOf(documentId)}</span>:{" "}
            {whyUnread(run, documentId)}
          </li>
        ))}
      </ul>
    </section>
  );
}
