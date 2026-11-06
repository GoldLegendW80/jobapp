import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { useQueueSummary } from "../api/use-queue";
import { summaryLine } from "../helpers/full-auto-state";
import { reasonLine } from "../helpers/queue-state";

/**
 * Full auto's day so far (tasks/apply/todo.md, A13b): submitted, held, and sent to the person for
 * review with each reason's sentence. Shown while Full auto is on, or once anything happened
 * today.
 */
export function FullAutoSummary({ on }: { on: boolean }) {
  const query = useQueueSummary();
  const summary = query.data;
  if (!summary) return null;
  const line = summaryLine(summary);
  if (!line && !on) return null;
  return (
    <Card
      role="region"
      aria-labelledby="full-auto-today"
      className="gap-3 text-sm"
    >
      <CardHeader>
        <CardTitle>
          <h2 id="full-auto-today" className="text-base">
            Full auto today
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
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
      </CardContent>
    </Card>
  );
}
