/**
 * The browser sources of a sweep in its summary (browser B8, `stats.sources` of
 * api/app/browser/sources): per job site read in the person's browser, what it found, what was
 * new, and in plain words why it stopped early when it did.
 */
import { Globe } from "lucide-react";

import { SOURCE_PROBLEMS, sourceName } from "./labels";
import { plural } from "./selection";
import type { SweepSource } from "./sweeps-api";

export function SweepSources({ sources }: { sources: SweepSource[] }) {
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
              {`: ${plural(source.found, "posting")} found, ${source.new.toLocaleString("en-GB")} new.`}
              {source.error
                ? ` ${SOURCE_PROBLEMS[source.error] ?? "It stopped early."}`
                : ""}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
