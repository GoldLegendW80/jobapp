import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { formatDateTime } from "@/features/jobs/helpers/format";
import { describeEvent } from "@/features/jobs/helpers/text";

/** The posting's events, newest first. */
export function PostingHistory({ job }: { job: JobDetail }) {
  return (
    <Section title="History">
      <Card>
        <CardContent className="flex flex-col gap-4">
          {job.events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No events yet.</p>
          ) : (
            <ol
              aria-label="History, newest first"
              className="flex flex-col gap-3 border-l pl-4"
            >
              {job.events.map((event) => (
                <li key={event.id} className="relative flex flex-col gap-0.5">
                  <span
                    aria-hidden="true"
                    className="absolute top-1.5 -left-[1.3rem] size-2 rounded-full bg-muted-foreground"
                  />
                  <span className="text-sm">
                    {describeEvent(event.kind, event.data)}
                  </span>
                  <time
                    dateTime={event.at}
                    className="text-xs text-muted-foreground"
                  >
                    {formatDateTime(event.at)}
                  </time>
                </li>
              ))}
            </ol>
          )}
          {job.events_total > job.events.length ? (
            <p className="text-xs text-muted-foreground">
              Showing the newest {job.events.length} of {job.events_total}{" "}
              events.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
