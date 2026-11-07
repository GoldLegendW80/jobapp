import { Search as SearchIcon } from "lucide-react";
import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { useIsPhone } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

/** The scrolling middle of the setup, between its header and its footer, which stay in view. */
export const BODY =
  "min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 sm:px-6";
/** The header's and the footer's sides, as the body's. */
export const EDGES = "shrink-0 px-5 sm:px-6";

const INTRO =
  "Add the places to search, say what you want, and start. The app keeps the postings that match.";

/** The header's grid: the title's glyph in the gutter of the grid below, so the title starts at
 * the text's edge. */
const HEADER =
  "grid grid-cols-[2.25rem_minmax(0,1fr)] items-center gap-x-0 gap-y-1";

function TitleGlyph() {
  return (
    <span
      aria-hidden="true"
      className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-primary"
    >
      <SearchIcon className="size-4" />
    </span>
  );
}

/**
 * Where the setup opens: a drawer from the bottom of the screen on a phone (below `sm`, dragged
 * down by its top to close, at most 92% of the screen's height), a dialog in the middle above it. Either way
 * the header and the footer stay in view and the middle scrolls. While `dismissible` is false
 * (saving), neither closes by Escape, a click outside or a drag.
 */
export function SetupSurface({
  onClose,
  onReturnFocus,
  dismissible = true,
  children,
}: {
  /** Asked to close (Escape, the close button, a click outside, a drag down). */
  onClose: () => void;
  onReturnFocus: () => void;
  dismissible?: boolean;
  children: ReactNode;
}) {
  const phone = useIsPhone();
  const onOpenChange = (open: boolean) => {
    if (!open && dismissible) onClose();
  };
  const onCloseAutoFocus = (event: Event) => {
    event.preventDefault();
    onReturnFocus();
  };
  if (phone)
    return (
      // autoFocus: the first field takes the focus, as in the dialog (vaul keeps it on the page).
      <Drawer
        open
        onOpenChange={onOpenChange}
        dismissible={dismissible}
        autoFocus
      >
        <DrawerContent
          onCloseAutoFocus={onCloseAutoFocus}
          className="gap-0 overflow-hidden"
        >
          <DrawerHeader
            className={cn(
              EDGES,
              HEADER,
              "border-b pt-3 pr-5 pb-3 text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left",
            )}
          >
            <TitleGlyph />
            <DrawerTitle className="text-lg leading-tight">
              Find jobs
            </DrawerTitle>
            <DrawerDescription className="col-start-2">
              {INTRO}
            </DrawerDescription>
          </DrawerHeader>
          {/* Dragged by its top only: below, a drag scrolls the fields or works a live browser. */}
          <div data-vaul-no-drag className="flex min-h-0 flex-1 flex-col">
            {children}
          </div>
        </DrawerContent>
      </Drawer>
    );
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent
        onCloseAutoFocus={onCloseAutoFocus}
        className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl"
      >
        <DialogHeader
          className={cn(EDGES, HEADER, "border-b pt-6 pr-12 pb-4 text-left")}
        >
          <TitleGlyph />
          <DialogTitle className="leading-tight">Find jobs</DialogTitle>
          <DialogDescription className="col-start-2">{INTRO}</DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}
