import { useQuery } from "@tanstack/react-query";

import { getSummary, summaryKey } from "./queue-api";
import { summaryLine } from "./full-auto-state";
import { reasonLine } from "./queue-state";

/** The summary is read again this often while the queue page is open. */
const SUMMARY_POLL_MS = 30_000;

/**
 * Full auto's day so far (tasks/apply/todo.md, A13b): submitted, held, and sent to the person for
 * review with each reason's sentence. Shown while Full auto is on, or once anything happened
 * today.
 */
export function FullAutoSummary({ on }: { on: boolean }) {
  const query = useQuery({
    queryKey: summaryKey,
    queryFn: ({ signal }) => getSummary(signal),
    refetchInterval: SUMMARY_POLL_MS,
  });
  const summary = query.data;
  if (!summary) return null;
  const line = summaryLine(summary);
  if (!line && !on) return null;
  return (
    <section
      aria-labelledby="full-auto-today"
      className="flex flex-col gap-2 rounded-lg border bg-card p-4 text-sm text-card-foreground"
    >
      <h2 id="full-auto-today" className="text-base font-semibold">
        Full auto today
      </h2>
      <p>{line ? `${line}.` : "Nothing yet today."}</p>
      {summary.sent_to_review.length > 0 ? (
        <ul
          aria-label="Why they were sent to you"
          className="flex list-disc flex-col gap-1 pl-5 text-muted-foreground"
        >
          {summary.sent_to_review.map((one) => (
            <li key={one.code}>
              {reasonLine({ code: one.code, sentence: one.sentence })}{" "}
              <span className="tabular-nums">({one.count})</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
