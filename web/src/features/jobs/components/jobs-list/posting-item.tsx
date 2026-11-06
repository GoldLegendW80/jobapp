import type { KeyboardEvent, MouseEvent } from "react";
import { Link } from "react-router";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Checkbox } from "@/components/ui/checkbox";
import { ApplyRow } from "@/features/jobs/components/apply-row";
import { cn } from "@/lib/utils";

import type { JobListItem } from "../../api/jobs-api";
import { formatDate } from "@/features/jobs/helpers/format";
import { initialOf, placeLine } from "@/features/jobs/helpers/jobs-list";
import type { ListSelection } from "@/features/jobs/helpers/selection";
import {
  FitBadge,
  PayText,
  StatusBadge,
} from "@/components/shared/jobs/badges";

/** One posting of the list as a compact card: the company's initial, the title, the company,
 * the place, the pay, the date found, the fit, the status and its Apply. */
export function PostingItem({
  job,
  index,
  total,
  top,
  measure,
  focusable,
  isCurrent,
  from,
  selection,
  onKeyDown,
  onFocus,
  onClick,
  onApply,
}: {
  job: JobListItem;
  index: number;
  total: number;
  top: number;
  measure: (element: Element | null) => void;
  focusable: boolean;
  isCurrent: boolean;
  from: string;
  selection?: ListSelection;
  onKeyDown: (event: KeyboardEvent<HTMLLIElement>) => void;
  onFocus: () => void;
  onClick: (event: MouseEvent<HTMLLIElement>) => void;
  onApply: (id: string) => void;
}) {
  const checked = selection?.selected.has(job.id) ?? false;
  return (
    <li
      ref={measure}
      data-index={index}
      aria-posinset={index + 1}
      aria-setsize={total}
      aria-current={isCurrent ? "true" : undefined}
      tabIndex={focusable ? 0 : -1}
      onKeyDown={onKeyDown}
      onFocus={onFocus}
      onClick={onClick}
      className={cn(
        "absolute inset-x-2 top-0 flex cursor-pointer items-start gap-3 rounded-lg p-3 outline-none transition-colors hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset",
        isCurrent &&
          "bg-muted before:absolute before:inset-y-3 before:left-0 before:w-[3px] before:rounded-full before:bg-primary",
        checked && "bg-highlight/40 hover:bg-highlight/60",
      )}
      style={{ transform: `translateY(${top}px)` }}
    >
      {selection ? (
        <Checkbox
          checked={checked}
          aria-label={`Select ${job.title} at ${job.company}`}
          tabIndex={-1}
          className="mt-3 cursor-pointer bg-card"
          onClick={(event) => {
            event.stopPropagation();
            selection.toggle(index, event.shiftKey);
          }}
        />
      ) : null}
      <Avatar size="lg" aria-hidden="true">
        <AvatarFallback className="bg-secondary font-medium text-secondary-foreground">
          {initialOf(job.company)}
        </AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Link
          to={`/jobs/${job.id}`}
          state={{ from }}
          tabIndex={-1}
          className="line-clamp-2 text-sm font-medium break-words hover:underline"
        >
          {job.title}
        </Link>
        <p className="truncate text-sm">{job.company}</p>
        <p className="truncate text-xs text-muted-foreground">
          {placeLine(job)}
        </p>
        <p className="flex items-baseline justify-between gap-2 text-xs">
          <PayText job={job} className="font-medium" />
          <time
            dateTime={job.found_on}
            className="shrink-0 text-muted-foreground tabular-nums"
          >
            {formatDate(job.found_on)}
          </time>
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {/* A badge's tint is see-through: on white it reads at 4.5:1 even on a tinted item. */}
          <span className="inline-flex rounded-full bg-card">
            <FitBadge fit={job.fit} overridden={job.fit_overridden} />
          </span>
          <span className="inline-flex rounded-full bg-card">
            <StatusBadge status={job.status} />
          </span>
          <span className="ml-auto">
            <ApplyRow
              job={job}
              from={from}
              focusable={focusable}
              onApply={onApply}
            />
          </span>
        </div>
      </div>
    </li>
  );
}
