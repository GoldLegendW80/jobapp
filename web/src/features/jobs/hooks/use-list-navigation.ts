/**
 * Moving through the Jobs list: one item in the tab order (the current posting, else the last
 * focused), arrow keys, Home, End, Page Up and Page Down between items, Space or X to tick,
 * Enter to open, and a click that selects the posting beside the list or opens it.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
} from "react";
import { useNavigate } from "react-router";

import type { JobListItem } from "../api/jobs-api";
import { withSelected } from "../helpers/filters";
import { ITEM_ESTIMATE, type ListCurrent } from "../helpers/jobs-list";
import type { ListSelection } from "../helpers/selection";

export function useListNavigation({
  items,
  from,
  current,
  selection,
  listRef,
  viewHeight,
  scrollToIndex,
}: {
  items: JobListItem[];
  /** The list's query string, so the posting page can come back to the same view. */
  from: string;
  current?: ListCurrent;
  selection?: ListSelection;
  listRef: RefObject<HTMLUListElement>;
  /** The height of what scrolls the list, for Page Up and Page Down. */
  viewHeight: () => number;
  scrollToIndex: (index: number) => void;
}) {
  const navigate = useNavigate();
  const [active, setActive] = useState(0);
  // The item a key moved to: focused once it is rendered (the URL names it a little later).
  const focusPending = useRef<number | null>(null);

  const currentIndex = current
    ? items.findIndex((job) => job.id === current.id)
    : -1;
  // The item in the tab order: the current posting when the list holds it, else the last focused.
  const focusIndex =
    currentIndex >= 0
      ? currentIndex
      : Math.min(active, Math.max(0, items.length - 1));

  useEffect(() => {
    if (focusPending.current === null) return;
    const item = listRef.current?.querySelector<HTMLElement>(
      `:scope > [data-index="${focusPending.current}"]`,
    );
    if (item) {
      focusPending.current = null;
      item.focus({ preventScroll: true });
    }
  });

  const fromFor = useCallback((id: string) => withSelected(from, id), [from]);

  const open = (job: JobListItem) => {
    void navigate(`/jobs/${job.id}`, { state: { from: fromFor(job.id) } });
  };

  const move = (index: number) => {
    const next = Math.max(0, Math.min(items.length - 1, index));
    const job = items[next];
    if (!job) return;
    setActive(next);
    focusPending.current = next;
    current?.onSelect(job.id);
    scrollToIndex(next);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLLIElement>, index: number) => {
    const page = Math.max(1, Math.floor(viewHeight() / ITEM_ESTIMATE) - 1);
    const keys: Record<string, number> = {
      ArrowDown: index + 1,
      ArrowUp: index - 1,
      Home: 0,
      End: items.length - 1,
      PageDown: index + page,
      PageUp: index - page,
    };
    const target = keys[event.key];
    if (target !== undefined) {
      event.preventDefault();
      move(target);
      return;
    }
    if (
      selection &&
      (event.key === " " || event.key === "x") &&
      event.target === event.currentTarget
    ) {
      event.preventDefault();
      selection.toggle(index, event.shiftKey);
      return;
    }
    if (event.key === "Enter" && event.target === event.currentTarget) {
      event.preventDefault();
      const job = items[index];
      if (job) open(job);
    }
  };

  const onItemClick = (
    event: MouseEvent<HTMLLIElement>,
    job: JobListItem,
    index: number,
  ) => {
    // The title link navigates on its own (and keeps middle click and new tab); the checkbox
    // ticks; Apply opens its confirmation.
    if ((event.target as HTMLElement).closest("a, button, input, label"))
      return;
    if (current) {
      setActive(index);
      current.onSelect(job.id);
    } else open(job);
  };

  return {
    currentIndex,
    focusIndex,
    fromFor,
    onKeyDown,
    onItemClick,
    onFocus: setActive,
  };
}
