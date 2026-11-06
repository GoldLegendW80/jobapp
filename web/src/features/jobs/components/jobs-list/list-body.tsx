import type { VirtualItem } from "@tanstack/react-virtual";
import { useEffect, useRef, useState, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  BulkApplyDialog,
  type BulkApplyRequest,
} from "@/components/shared/apply/bulk-apply";

import type { JobListItem } from "../../api/jobs-api";
import { useListNavigation } from "../../hooks/use-list-navigation";
import {
  PREFETCH_ROWS,
  type ListCurrent,
} from "@/features/jobs/helpers/jobs-list";
import type { ListSelection } from "@/features/jobs/helpers/selection";
import { PostingItem } from "./posting-item";

export interface ListProps {
  items: JobListItem[];
  total: number;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  /** The list's query string, so the posting page can come back to the same view. */
  from: string;
  selection?: ListSelection;
}

/** What the body needs of either virtualiser. */
interface ListVirtualizer {
  getVirtualItems: () => VirtualItem[];
  getTotalSize: () => number;
  scrollToIndex: (index: number, options?: { align?: "auto" }) => void;
  measureElement: (element: Element | null) => void;
}

/** The items near the view, the next page loaded as the view nears the end, and an item's
 * Apply confirmation. */
export function ListBody({
  items,
  total,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  from,
  selection,
  current,
  virtualizer,
  scrollMargin,
  viewHeight,
  listRef,
}: ListProps & {
  current?: ListCurrent;
  virtualizer: ListVirtualizer;
  scrollMargin: number;
  /** The height of what scrolls the list, for Page Up and Page Down. */
  viewHeight: () => number;
  listRef?: RefObject<HTMLUListElement>;
}) {
  const ownRef = useRef<HTMLUListElement>(null);
  const ref = listRef ?? ownRef;
  // An item's Apply opens one confirmation, outside the items, so its clicks and keys never reach
  // an item's own handlers (which open or select the posting).
  const [applying, setApplying] = useState<BulkApplyRequest | null>(null);
  const navigation = useListNavigation({
    items,
    from,
    current,
    selection,
    listRef: ref,
    viewHeight,
    scrollToIndex: (index) => {
      virtualizer.scrollToIndex(index, { align: "auto" });
    },
  });
  const virtualItems = virtualizer.getVirtualItems();
  const last = virtualItems.at(-1)?.index ?? -1;

  useEffect(() => {
    if (
      hasNextPage &&
      !isFetchingNextPage &&
      last >= items.length - PREFETCH_ROWS
    ) {
      fetchNextPage();
    }
  }, [last, items.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  return (
    <>
      <ul
        ref={ref}
        aria-label="Postings"
        role="list"
        className="relative"
        style={{ height: virtualizer.getTotalSize() }}
      >
        {virtualItems.map((virtual) => {
          const job = items[virtual.index];
          if (!job) return null;
          return (
            <PostingItem
              key={virtual.key}
              job={job}
              index={virtual.index}
              total={total}
              top={virtual.start - scrollMargin}
              measure={virtualizer.measureElement}
              focusable={virtual.index === navigation.focusIndex}
              isCurrent={virtual.index === navigation.currentIndex}
              from={navigation.fromFor(job.id)}
              selection={selection}
              onKeyDown={(event) => {
                navigation.onKeyDown(event, virtual.index);
              }}
              onFocus={() => {
                navigation.onFocus(virtual.index);
              }}
              onClick={(event) => {
                navigation.onItemClick(event, job, virtual.index);
              }}
              onApply={(id) => {
                setApplying({ ids: [id], key: Date.now() });
              }}
            />
          );
        })}
      </ul>
      <BulkApplyDialog
        request={applying}
        onClose={() => {
          setApplying(null);
        }}
        onApplied={() => undefined}
      />
      {hasNextPage ? (
        <p
          role="status"
          aria-live="polite"
          className="flex items-center justify-center gap-2 px-3 pb-4 text-sm text-muted-foreground"
        >
          {isFetchingNextPage ? (
            <>
              <Spinner aria-hidden="true" role={undefined} />
              Loading more postings
            </>
          ) : (
            <Button
              type="button"
              variant="link"
              className="h-auto p-0 font-normal text-inherit underline has-[>svg]:px-0"
              onClick={fetchNextPage}
            >
              Load more postings
            </Button>
          )}
        </p>
      ) : null}
    </>
  );
}
