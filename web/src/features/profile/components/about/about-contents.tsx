import type { ReactNode } from "react";

import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

export interface ContentsEntry {
  label: string;
  /** the id of the part's card */
  anchor: string;
}

/**
 * The list of parts, with `footer` under it (the size of the fact base): sticky beside the parts
 * on desktop, a row of chips above them on phones.
 */
export function AboutContents({
  entries,
  footer,
}: {
  entries: readonly ContentsEntry[];
  footer?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start">
      <nav aria-label={W.contents}>
        <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-1">
          {entries.map((entry) => (
            <li key={entry.anchor} className="min-w-0">
              <a
                href={`#${entry.anchor}`}
                className="inline-flex max-w-full truncate rounded-full border px-3 py-1 text-sm hover:bg-accent lg:rounded-md lg:border-0 lg:px-2"
              >
                {entry.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      {footer}
    </div>
  );
}
