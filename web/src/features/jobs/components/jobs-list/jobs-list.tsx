import {
  observeElementRect,
  useVirtualizer,
  useWindowVirtualizer,
} from "@tanstack/react-virtual";
import { useLayoutEffect, useRef, useState } from "react";

import type { JobListItem } from "../../api/jobs-api";
import {
  ITEM_ESTIMATE,
  ITEM_GAP,
  LIST_PADDING,
  OVERSCAN,
  type ListCurrent,
} from "@/features/jobs/helpers/jobs-list";
import { ListBody, type ListProps } from "./list-body";
import { ListCard } from "./list-card";

/**
 * The postings as compact cards in one white card, virtualised (only the items near the view are
 * in the DOM) and loading the next page as the view nears the end. With `current` the list sits
 * beside the detail pane (from 1024px): it scrolls in its own box, the current posting is marked,
 * and a click on an item or an arrow key changes it; without, the window scrolls the list and a
 * click opens the posting. Arrow keys, Home, End, Page Up and Page Down move between items; Enter
 * or a click on the title opens the posting's page; Space or X ticks the item.
 */
export function JobsList({
  current,
  ...props
}: ListProps & { current?: ListCurrent }) {
  return current ? (
    <PaneList {...props} current={current} />
  ) : (
    <WindowList {...props} />
  );
}

/** A box not laid out yet (jsdom, a hidden pane) measures 0 high: the window's height stands in. */
const observePaneRect: typeof observeElementRect = (instance, onRect) =>
  observeElementRect(instance, (rect) => {
    onRect(rect.height ? rect : { ...rect, height: window.innerHeight });
  });

function PaneList(props: ListProps & { current: ListCurrent }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line react-hooks/incompatible-library -- the app has no React Compiler, and WindowList's useWindowVirtualizer is the same API, unlisted.
  const virtualizer = useVirtualizer({
    count: props.items.length,
    getScrollElement: () => scrollRef.current,
    observeElementRect: observePaneRect,
    ...virtualOptions(props.items),
  });
  return (
    <ListCard selection={props.selection} className="flex-1">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <ListBody
          {...props}
          virtualizer={virtualizer}
          scrollMargin={0}
          viewHeight={() => scrollRef.current?.clientHeight ?? 0}
        />
      </div>
    </ListCard>
  );
}

function WindowList(props: ListProps) {
  const listRef = useRef<HTMLUListElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  // Where the list starts in the page: it moves when what is above it changes (a chip, a notice).
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      setScrollMargin(list.getBoundingClientRect().top + window.scrollY);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    return () => {
      observer.disconnect();
    };
  }, []);
  const virtualizer = useWindowVirtualizer({
    count: props.items.length,
    scrollMargin,
    ...virtualOptions(props.items),
  });
  return (
    <ListCard selection={props.selection}>
      <ListBody
        {...props}
        listRef={listRef}
        virtualizer={virtualizer}
        scrollMargin={scrollMargin}
        viewHeight={() => window.innerHeight}
      />
    </ListCard>
  );
}

function virtualOptions(items: JobListItem[]) {
  return {
    estimateSize: () => ITEM_ESTIMATE,
    overscan: OVERSCAN,
    gap: ITEM_GAP,
    paddingStart: LIST_PADDING,
    paddingEnd: LIST_PADDING,
    getItemKey: (index: number) => items[index]?.id ?? index,
    // jsdom and hidden items measure 0: keep the estimate instead of collapsing the item.
    measureElement: (element: Element) =>
      element.getBoundingClientRect().height || ITEM_ESTIMATE,
  };
}
