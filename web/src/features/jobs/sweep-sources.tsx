/**
 * The browser sources of a sweep in its summary (browser B8, `stats.sources` of
 * api/app/browser/sources): per job site read in the person's browser, what it found, what was
 * new, and in plain words why it stopped early when it did. A site the sweep never began
 * (`not_started`, after another stopped the sweep's sites) shows only that. The freelance and
 * project feeds of the same list have their own heading and words (opportunities O8).
 */
import { Building2, Globe } from "lucide-react";

import { isFeedKind } from "@/features/opportunities/sources/source-words";
import { SweepFeeds } from "@/features/opportunities/sources/sweep-feeds";

import {
  SOURCE_PROBLEMS,
  leftOutSentence,
  searchesSentence,
  sourceName,
} from "./labels";
import { plural } from "./selection";
import type { SweepSource } from "./sweeps-api";

export function SweepSources({ sources }: { sources: SweepSource[] }) {
  const feeds = sources.filter((source) => isFeedKind(source.kind));
  const sites = sources.filter(
    (source) => !isFeedKind(source.kind) && source.mode !== "api",
  );
  // Company job boards (owner, 2026-10-30): one source each, read through the ATS's API.
  const boards = sources.filter(
    (source) => source.mode === "api" && source.kind === "board",
  );
  return (
    <>
      <BrowserSites sources={sites} />
      <CompanyBoardSources sources={boards} />
      <SweepFeeds feeds={feeds} />
    </>
  );
}

/** A board's line, named by its company (frozen with the sweep, else its `<ats>:<board>`):
 * what it found and kept, or why it was not read (its `reason`). */
function boardStatus(source: SweepSource): string {
  if (source.error === "not_started")
    return (
      (source.reason ? SOURCE_PROBLEMS[source.reason] : undefined) ??
      SOURCE_PROBLEMS.not_started ??
      ""
    );
  const found = `${plural(source.found, "posting")} matched, ${source.new.toLocaleString("en-GB")} new.`;
  return source.error
    ? `${found} ${SOURCE_PROBLEMS[source.error] ?? "It stopped early."}`
    : found;
}

function CompanyBoardSources({ sources }: { sources: SweepSource[] }) {
  if (!sources.length) return null;
  return (
    <section aria-label="Company job boards" className="flex flex-col gap-1">
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Building2
          aria-hidden="true"
          className="size-4 text-muted-foreground"
        />
        Company job boards
      </h3>
      <ul className="flex flex-col gap-1 text-sm break-words">
        {sources.map((source, index) => (
          <li key={`${source.query}:${String(index)}`}>
            <span className="font-medium">
              {source.company || source.query}
            </span>
            <span className="text-muted-foreground">
              : {boardStatus(source)}
            </span>
          </li>
        ))}
      </ul>
    </section>
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
  const counts = [searches, leftOutSentence(source), found]
    .filter((part) => part !== "")
    .join(" ");
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
