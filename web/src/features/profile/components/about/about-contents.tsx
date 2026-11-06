import type { Part } from "../../api/about-api";
import { partAnchor } from "@/features/profile/helpers/about-parts";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** The list of parts: sticky beside them on desktop, a row of chips above them on phones. */
export function AboutContents({ parts }: { parts: readonly Part[] }) {
  return (
    <nav aria-label={W.contents} className="lg:sticky lg:top-4 lg:self-start">
      <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-1">
        {parts.map((part, index) => (
          <li key={`${index}-${part.heading}`} className="min-w-0">
            <a
              href={`#${partAnchor(index)}`}
              className="inline-flex max-w-full truncate rounded-full border px-3 py-1 text-sm hover:bg-accent lg:rounded-md lg:border-0 lg:px-2"
            >
              {part.heading}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
