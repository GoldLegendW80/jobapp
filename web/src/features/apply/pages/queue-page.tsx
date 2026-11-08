import { Inbox } from "lucide-react";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/hooks/use-title";

import { ApplicationBrowsers } from "../components/application-browsers";
import { ApproveAll } from "../components/approve-all";
import { serverOffset } from "../helpers/full-auto-state";
import { FullAutoSummary } from "../components/full-auto-summary";
import { FollowUps } from "../components/follow-ups";
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
 * /applications, Applications (SPEC-apply.md, "Frontend"): the person's applications (of a Like
 * or of an Apply click), those waiting for the person first, then those under way, each group a
 * section of item cards across the page's width, the live view of each one the assistant works on
 * in the person's browser above those under way. The page reads the queue again by itself while
 * something is under way, and keeps the app shell's count in step. With Full auto, its day so far
 * comes first, and each hold counts down on the server's clock.
 */
export function QueuePage() {
  useTitle("Applications");
  const title = useRef<HTMLHeadingElement>(null);
  const query = useQueue();
  const settings = useApplySettings();
  const fullAuto = settings.data?.mode_after_like === "full_auto";
  const offset = serverOffset(query.data?.now, query.dataUpdatedAt);

  const items = query.data?.items ?? [];
  const waiting = items.filter(isWaiting);
  const underWay = items.filter((item) => !isWaiting(item));
  // A card that left the queue by a click hands the focus to the page's title.
  const leave = useCallback(() => {
    title.current?.focus();
  }, []);
  // The answers typed in each card and not saved yet: such a form waits for its own Approve.
  const [unsaved, setUnsaved] = useState<Readonly<Record<string, number>>>({});
  const onUnsaved = useCallback((id: string, count: number) => {
    setUnsaved((old) =>
      (old[id] ?? 0) === count ? old : { ...old, [id]: count },
    );
  }, []);

  return (
    <Page>
      <PageHeader
        titleRef={title}
        title="Applications"
        description={
          <span data-testid="queue-intro">
            {INTRO}
            {fullAuto ? ` ${FULL_AUTO_INTRO}` : null}
          </span>
        }
      />
      <PageBody>
        <FullAutoSummary on={fullAuto} />
        {query.isPending ? (
          <LoadingCard>Loading your queue</LoadingCard>
        ) : query.isError && query.data === undefined ? (
          <LoadError query={query}>The queue could not be loaded.</LoadError>
        ) : (
          <>
            {query.isError ? (
              <p role="status" className="text-sm text-muted-foreground">
                Could not refresh just now: this may be out of date.
              </p>
            ) : null}
            {items.length === 0 ? (
              <EmptyState
                icon={Inbox}
                title="Your queue is empty"
                description="Like postings on the Jobs page: each application appears here."
                actions={
                  <Button asChild>
                    <Link to="/jobs">Open Jobs</Link>
                  </Button>
                }
              />
            ) : (
              <>
                <QueueSection
                  title={`Waiting for you (${waiting.length})`}
                  empty="Nothing waits for you."
                  items={waiting}
                  onLeave={leave}
                  offset={offset}
                  onUnsaved={onUnsaved}
                >
                  <ApproveAll items={waiting} unsaved={unsaved} />
                </QueueSection>
                <QueueSection
                  title={`Under way (${underWay.length})`}
                  empty="Nothing is under way."
                  items={underWay}
                  onLeave={leave}
                  offset={offset}
                >
                  <ApplicationBrowsers items={underWay} />
                </QueueSection>
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
        <FollowUps />
      </PageBody>
    </Page>
  );
}

/** One group of the queue, its applications as item cards; `children` come before them. */
function QueueSection({
  title,
  empty,
  items,
  onLeave,
  offset,
  onUnsaved,
  children,
}: {
  title: string;
  empty: string;
  items: QueueItem[];
  onLeave: () => void;
  offset: number;
  onUnsaved?: (id: string, count: number) => void;
  children?: ReactNode;
}) {
  return (
    <Section title={title}>
      {children}
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
    </Section>
  );
}
