import type { MouseEvent } from "react";

import { cn } from "@/lib/utils";

import { LISTED_MENTION } from "@/features/jobs/helpers/listed-words";
import { safeHttpUrl } from "@/features/jobs/helpers/text";

/** A row's click selects it: a link of the mention opens its page and leaves the row as it is. */
const own = (event: MouseEvent) => {
  event.stopPropagation();
};

/**
 * Jobs J31: "Seen in a list: its own page was not read", on a posting kept from a card of a list
 * (the row of the Jobs list and the posting), with a link to the list it was seen in and one to
 * its own page. Nothing for any other posting. An address that is not http or https stays text.
 */
export function ListedMention({
  job,
  focusable = true,
  className,
}: {
  job: { list_url?: string | null; url: string | null };
  /** In a row of the list, its links are tab stops only while the row has the focus. */
  focusable?: boolean;
  className?: string;
}) {
  if (typeof job.list_url !== "string") return null;
  const list = safeHttpUrl(job.list_url);
  const page = safeHttpUrl(job.url);
  const links = [
    list ? { href: list, text: "the list" } : null,
    page ? { href: page, text: "its own page" } : null,
  ].filter((link) => link !== null);
  return (
    <p
      data-testid="listed-mention"
      className={cn("text-xs text-muted-foreground", className)}
    >
      <span className="font-medium text-warning">{LISTED_MENTION}.</span>
      {links.length > 0 ? (
        <>
          {" "}
          Open{" "}
          {links.map((link, index) => (
            <span key={link.text}>
              {index > 0 ? " or " : null}
              <a
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                tabIndex={focusable ? 0 : -1}
                onClick={own}
                className="underline underline-offset-4 [overflow-wrap:anywhere]"
              >
                {link.text}
              </a>
            </span>
          ))}
          .
        </>
      ) : null}
    </p>
  );
}
