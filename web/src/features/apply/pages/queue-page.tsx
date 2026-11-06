import { CircleAlert, Inbox } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";
import { useTitle } from "@/hooks/use-title";

import { ApproveAll } from "../components/approve-all";
import { serverOffset } from "../helpers/full-auto-state";
import { FullAutoSummary } from "../components/full-auto-summary";
import type { QueueItem } from "../api/queue-api";
import { useQueue } from "../api/use-queue";
import { QueueCard } from "../components/queue-card";
import { isWaiting } from "../helpers/queue-state";
import { useApplySettings } from "../api/use-apply-settings";

/** The api answers at most this many items. */
const QUEUE_MAX = 200;

/**
 * What the page is for. It promises no review: since one click Apply (owner, 2026-11-01) an Apply
 * click sends the form without one by default, and only what needs the person waits here.
 */
export const INTRO =
  "Your applications. Anything that needs your answer or approval waits here.";
/** Added in Full auto, where a Like is the approval of what passes every check. */
export const FULL_AUTO_INTRO =
  "In Full auto your Like is your approval: the app sends what passes every check once the hold ends, unless you cancel it here.";

/**
 * /applications, the review queue (SPEC-apply.md, "Frontend"): the person's applications (of a
 * Like or of an Apply click), those waiting for the person first, then those under way. The page reads the queue
 * again by itself while something is under way, and keeps the app shell's count in step. With
 * Full auto, its day so far comes first, and each hold counts down on the server's clock.
 */
export function QueuePage() {
  useTitle("Application queue");
  const page = useRef<HTMLDivElement>(null);
  const query = useQueue();
  const settings = useApplySettings();
  const fullAuto = settings.data?.mode_after_like === "full_auto";
  const offset = serverOffset(query.data?.now, query.dataUpdatedAt);

  const items = query.data?.items ?? [];
  const waiting = items.filter(isWaiting);
  const underWay = items.filter((item) => !isWaiting(item));
  // A card that left the queue by a click hands the focus to the page's title.
  const leave = useCallback(() => {
    const title = page.current?.querySelector("h1");
    if (!title) return;
    title.tabIndex = -1;
    title.focus();
  }, []);
  // The answers typed in each card and not saved yet: such a form waits for its own Approve.
  const [unsaved, setUnsaved] = useState<Readonly<Record<string, number>>>({});
  const onUnsaved = useCallback((id: string, count: number) => {
    setUnsaved((old) =>
      (old[id] ?? 0) === count ? old : { ...old, [id]: count },
    );
  }, []);

  return (
    <div ref={page} className="mx-auto flex max-w-3xl flex-col gap-8">
      <PageHeader
        className="[&_h1]:outline-none"
        title="Application queue"
        description={
          <span data-testid="queue-intro">
            {INTRO}
            {fullAuto ? ` ${FULL_AUTO_INTRO}` : null}
          </span>
        }
      />
      <FullAutoSummary on={fullAuto} />
      {query.isPending ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <Spinner aria-hidden="true" role={undefined} />
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
            <Empty className="rounded-xl bg-card">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Inbox aria-hidden="true" />
                </EmptyMedia>
                <EmptyTitle>Your queue is empty.</EmptyTitle>
                <EmptyDescription>
                  Select postings on the <Link to="/jobs">Jobs</Link> page and
                  click Like: each application appears here.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <>
              <ApproveAll items={waiting} unsaved={unsaved} />
              <QueueSection
                id="queue-waiting"
                heading={`Waiting for you (${waiting.length})`}
                empty="Nothing waits for you."
                items={waiting}
                onLeave={leave}
                offset={offset}
                onUnsaved={onUnsaved}
              />
              <QueueSection
                id="queue-under-way"
                heading={`Under way (${underWay.length})`}
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
  heading,
  empty,
  items,
  onLeave,
  offset,
  onUnsaved,
}: {
  id: string;
  heading: string;
  empty: string;
  items: QueueItem[];
  onLeave: () => void;
  offset: number;
  onUnsaved?: (id: string, count: number) => void;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-lg font-medium tracking-tight">
        {heading}
      </h2>
      {items.length > 0 ? (
        <ul className="flex flex-col gap-4">
          {items.map((item) => (
            <li key={item.id}>
              <QueueCard
                item={item}
                onLeave={onLeave}
                offset={offset}
                onUnsavedChange={onUnsaved}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  );
}
