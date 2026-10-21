/**
 * The freelance and project feeds of a sweep in its summary (opportunities O8): per feed the
 * person turned on, what it found, what was new, how many postings the never apply rules set
 * aside, and in plain words why it was not read, or not to the end, when that happened. The job
 * sites read in the browser keep their own list (features/jobs/sweep-sources.tsx).
 */
import { Rss } from "lucide-react";

import type { SweepSource } from "@/features/jobs/sweeps-api";
import { WORDING } from "@/lib/wording";

import { FEED_SHORT_NAMES, feedLine, isFeedKind } from "./source-words";

export function SweepFeeds({ feeds }: { feeds: SweepSource[] }) {
  if (!feeds.length) return null;
  return (
    <section
      aria-label={WORDING.freelanceSources}
      className="flex flex-col gap-1"
    >
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Rss aria-hidden="true" className="size-4 text-muted-foreground" />
        {WORDING.freelanceSources}
      </h3>
      <ul className="flex flex-col gap-1 text-sm break-words">
        {feeds.map((feed, index) => (
          <li key={`${feed.kind}:${index}`}>
            <span className="font-medium">
              {isFeedKind(feed.kind) ? FEED_SHORT_NAMES[feed.kind] : feed.kind}
            </span>
            <span className="text-muted-foreground">{`: ${feedLine(feed)}`}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
