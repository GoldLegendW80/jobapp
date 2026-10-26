/**
 * The browser sources of a sweep in its summary (browser B8, `stats.sources` of
 * api/app/browser/sources): per job site read in the person's browser, what it found, what was
 * new, and in plain words why it stopped early when it did. A site the sweep never began
 * (`not_started`, after another stopped the sweep's sites) shows only that. The freelance and
 * project feeds of the same list have their own heading and words (opportunities O8).
 */
import { Globe } from "lucide-react";

import { isFeedKind } from "@/features/opportunities/sources/source-words";
import { SweepFeeds } from "@/features/opportunities/sources/sweep-feeds";

import { SOURCE_PROBLEMS, searchesSentence, sourceName } from "./labels";
import { plural } from "./selection";
import type { SweepSource } from "./sweeps-api";

export function SweepSources({ sources }: { sources: SweepSource[] }) {
  const feeds = sources.filter((source) => isFeedKind(source.kind));
  const sites = sources.filter(
    (source) => !isFeedKind(source.kind) && source.mode !== "api",
  );
  return (
    <>
      <BrowserSites sources={sites} />
      <SweepFeeds feeds={feeds} />
    </>
  );
}

function sourceStatus(source: SweepSource): string {
  if (
    source.mode === "llm_browser" &&
    (source.phase === "pending" ||
      source.outcome === "not_attempted" ||
      source.reason === "interactive_executor_pending")
  ) {
    return "not searched this time.";
  }
  if (source.error === "not_started") return `${SOURCE_PROBLEMS.not_started}`;
  const searches = searchesSentence(source);
  const found = `${plural(source.found, "posting")} found, ${source.new.toLocaleString("en-GB")} new.`;
  const counts = searches ? `${searches} ${found}` : found;
  return source.error
    ? `${counts} ${SOURCE_PROBLEMS[source.error] ?? "It stopped early."}`
    : counts;
}

function BrowserSites({ sources }: { sources: SweepSource[] }) {
  if (!sources.length) return null;
  return (
    <section
      aria-label="Job sites in your browser"
      className="flex flex-col gap-1"
    >
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Globe aria-hidden="true" className="size-4 text-muted-foreground" />
        Job sites in your browser
      </h3>
      <ul className="flex flex-col gap-1 text-sm break-words">
        {sources.map((source, index) => (
          <li key={`${source.kind}:${source.query}:${index}`}>
            <span className="font-medium">{sourceName(source)}</span>
            <span className="text-muted-foreground">
              : {sourceStatus(source)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
