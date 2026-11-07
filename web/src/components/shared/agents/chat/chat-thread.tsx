import type { ReactNode } from "react";

import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

/**
 * A conversation's scrolling frame, for every chat of the app: it follows the answer being
 * written, holds the person's newest message in view while it is answered, and offers a button
 * back to the newest message after scrolling up. Its children are MessageScrollerItems. The
 * content is not a live region: each chat announces a new answer once itself.
 */
export function ChatThread({
  scrollTestId,
  loading = false,
  error,
  empty,
  fit = false,
  children,
}: {
  scrollTestId: string;
  loading?: boolean;
  /** Why the conversation could not be loaded, as a sentence. */
  error?: string;
  /** What shows once loaded with no message yet; null while there are messages. */
  empty?: ReactNode;
  /** As tall as its messages up to half the screen, flush with the card it sits in; else it
   * fills the height of its column with its own padding, as in a side panel. */
  fit?: boolean;
  children?: ReactNode;
}) {
  // Not an item, so the first messages open the thread on the newest one.
  const placeholder = loading ? (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Spinner aria-hidden="true" role={undefined} />
      Loading the conversation
    </p>
  ) : error !== undefined ? (
    <p className="text-sm text-destructive">
      The conversation could not be loaded. {error}
    </p>
  ) : (
    empty
  );
  return (
    <MessageScrollerProvider autoScroll>
      <MessageScroller className={fit ? undefined : "min-h-0 flex-1"}>
        <MessageScrollerViewport
          data-testid={scrollTestId}
          className={fit ? "h-auto max-h-[min(28rem,50vh)]" : undefined}
        >
          {placeholder ? (
            <div className={fit ? undefined : "px-4 pt-4"}>{placeholder}</div>
          ) : null}
          <MessageScrollerContent
            aria-live="off"
            className={cn(fit ? "pb-1" : "p-4", placeholder ? "min-h-0" : null)}
          >
            {children}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
