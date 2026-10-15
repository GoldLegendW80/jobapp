import type { Application, ApplicationEvent } from "./apply-api";
import { durationText, eventLine } from "./apply-state";

const TIME = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

/** The run report: what happened, in order, and how long it took. */
export function RunReport({
  application,
  events,
}: {
  application: Application;
  events: readonly ApplicationEvent[];
}) {
  const duration = durationText(application);
  return (
    <section aria-label="Run report" className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">Run report</h3>
      {duration ? (
        <p className="text-sm text-muted-foreground">Took {duration}.</p>
      ) : null}
      <ol className="flex flex-col gap-1 text-sm">
        {events.map((event, index) => (
          <li key={`${event.at}-${index}`} className="flex gap-3">
            <time
              dateTime={event.at}
              className="w-20 shrink-0 text-muted-foreground tabular-nums"
            >
              {TIME.format(new Date(event.at))}
            </time>
            <span>{eventLine(event)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
