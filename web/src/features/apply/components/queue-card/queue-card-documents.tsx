import { Link } from "react-router";

import { cn } from "@/lib/utils";

import type { QueueItem } from "../../api/queue-api";
import {
  asksDocumentLook,
  checkLine,
  needsLook,
} from "@/features/apply/helpers/queue-state";

/**
 * The documents of a queue item, each with its check and a link to it; in Review first, one to
 * look at is amber.
 */
export function QueueCardDocuments({ item }: { item: QueueItem }) {
  const documents = Object.entries(item.documents);
  const asks = asksDocumentLook(item);
  return (
    <section aria-label="Documents" className="flex flex-col gap-1">
      <h4 className="text-sm font-medium">Documents</h4>
      {asks && item.documents_review ? (
        <p className="text-sm font-medium text-warning">
          Review these documents before approval.
        </p>
      ) : null}
      {documents.length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-sm">
          {documents.map(([kind, check]) => (
            <li
              key={kind}
              className={cn(asks && needsLook(check) && "text-warning")}
            >
              <Link
                to={`/jobs/${item.posting_id}/documents/${kind}`}
                className="rounded-sm underline underline-offset-4"
              >
                {checkLine(kind, check, asks)}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          The documents show here once the form is filled.
        </p>
      )}
    </section>
  );
}
