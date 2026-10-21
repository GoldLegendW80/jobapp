import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { serverOffset } from "./full-auto-state";
import { FullAutoSummary } from "./full-auto-summary";
import {
  getQueue,
  queueCountKey,
  queueListKey,
  type QueueItem,
} from "./queue-api";
import { QueueCard } from "./queue-card";
import { isWaiting, queuePoll } from "./queue-state";
import { applySettingsKey, getApplySettings } from "./settings-api";

/** The api answers at most this many items. */
const QUEUE_MAX = 200;

/**
 * /applications, the review queue (SPEC-apply.md, "Frontend"): the applications of the person's
 * Likes, those waiting for the person first, then those under way. The page reads the queue
 * again by itself while something is under way, and keeps the app shell's count in step. With
 * Full auto, its day so far comes first, and each hold counts down on the server's clock.
 */
export function QueuePage() {
  useTitle("Application queue");
  const queryClient = useQueryClient();
  const heading = useRef<HTMLHeadingElement>(null);
  const query = useQuery({
    queryKey: queueListKey,
    queryFn: ({ signal }) => getQueue(signal),
    refetchInterval: (current) => queuePoll(current.state.data),
  });
  const settings = useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
  });
  const fullAuto = settings.data?.mode_after_like === "full_auto";
  const offset = serverOffset(query.data?.now, query.dataUpdatedAt);
  const waitingOnYou = query.data?.waiting_on_you;
  useEffect(() => {
    if (waitingOnYou !== undefined) {
      queryClient.setQueryData(queueCountKey, waitingOnYou);
    }
  }, [waitingOnYou, queryClient]);

  const items = query.data?.items ?? [];
  const waiting = items.filter(isWaiting);
  const underWay = items.filter((item) => !isWaiting(item));
  // A card that left the queue by a click hands the focus to the page's title.
  const leave = useCallback(() => {
    heading.current?.focus();
  }, []);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold tracking-tight outline-none"
        >
          Application queue
        </h1>
        <p className="text-muted-foreground">
          {settings.data === undefined
            ? "Your review queue: the applications of the postings you like."
            : fullAuto
              ? "Your review queue. In Full auto your Like is your approval: the app submits what passes every check once the hold ends, unless you cancel it here, and everything else waits here for you."
              : "Your review queue: the assistant fills the form of each posting you like, then waits. Nothing is sent until you approve it."}
        </p>
      </header>
      <FullAutoSummary on={fullAuto} />
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading your queue
        </p>
      ) : query.isError && query.data === undefined ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col items-start gap-2">
            The queue could not be loaded. {describeError(query.error)}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void query.refetch()}
            >
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      ) : (
        <>
          {query.isError ? (
            <p role="status" className="text-sm text-muted-foreground">
              Could not refresh just now: this may be out of date.
            </p>
          ) : null}
          {items.length === 0 ? (
            <p className="text-muted-foreground">
              Your queue is empty. Select postings on the{" "}
              <Link to="/jobs" className="underline underline-offset-4">
                Jobs
              </Link>{" "}
              page and click Like: each application appears here.
            </p>
          ) : (
            <>
              <QueueSection
                id="queue-waiting"
                title={`Waiting for you (${waiting.length})`}
                empty="Nothing waits for you."
                items={waiting}
                onLeave={leave}
                offset={offset}
              />
              <QueueSection
                id="queue-under-way"
                title={`Under way (${underWay.length})`}
                empty="Nothing is under way."
                items={underWay}
                onLeave={leave}
                offset={offset}
              />
              {items.length >= QUEUE_MAX ? (
                <p className="text-sm text-muted-foreground">
                  Only the first {QUEUE_MAX} applications are shown. The next
                  ones appear as these leave the queue.
                </p>
              ) : null}
            </>
          )}
        </>
      )}
    </div>
  );
}

function QueueSection({
  id,
  title,
  empty,
  items,
  onLeave,
  offset,
}: {
  id: string;
  title: string;
  empty: string;
  items: QueueItem[];
  onLeave: () => void;
  offset: number;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {items.length > 0 ? (
        <ul className="flex flex-col gap-4">
          {items.map((item) => (
            <li key={item.id}>
              <QueueCard item={item} onLeave={onLeave} offset={offset} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  );
}
