import type { Review } from "../../api/proposals-api";
import {
  EMPTY_NOTE,
  NEWER_RUN_NOTE,
  plural,
} from "@/features/ingestion/helpers/proposal-words";

/** The head of the review: how many proposals, how many decided, and the documents read. */
export function ReviewSummary({ review }: { review: Review }) {
  const { counts } = review;
  return (
    <div className="flex flex-col gap-1">
      <h3 className="font-semibold tracking-tight">What your documents say</h3>
      <p className="text-sm text-muted-foreground">
        {counts.total === 0
          ? EMPTY_NOTE
          : `${plural(counts.total, "proposal", "proposals")} in ${plural(review.sections.length, "section", "sections")}. Nothing changes in your profile until you accept it.`}
      </p>
      {counts.total > 0 ? (
        <p
          className="text-sm text-muted-foreground tabular-nums"
          data-testid="review-left"
        >
          {counts.pending === 0
            ? "Every proposal is decided."
            : `${String(counts.pending)} left to review, ${String(counts.accepted)} accepted, ${String(counts.rejected)} rejected.`}
        </p>
      ) : null}
      {review.newer_run ? (
        <p className="text-sm text-muted-foreground" data-testid="newer-run">
          {NEWER_RUN_NOTE}
        </p>
      ) : null}
      {review.partial ? (
        <p className="text-sm text-muted-foreground">
          Some of your documents could not be read, so this list may be
          incomplete.
        </p>
      ) : null}
      {review.documents.length > 0 ? (
        <ul
          aria-label="Documents read"
          className="flex flex-wrap gap-x-3 text-sm text-muted-foreground"
        >
          {review.documents.map((document) => (
            <li key={document.id}>
              {document.deleted ? (
                "Deleted"
              ) : (
                <bdi>{document.name ?? "Document"}</bdi>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
