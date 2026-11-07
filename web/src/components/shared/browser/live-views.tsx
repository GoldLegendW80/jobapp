import { Eye, EyeOff, MonitorPlay } from "lucide-react";
import {
  Children,
  isValidElement,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { AspectRatio } from "@/components/ui/aspect-ratio";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyMedia } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

/** Where one live view stands, as its badge says it. */
export type LiveViewState =
  | "starting"
  | "waiting"
  | "connecting"
  | "live"
  | "needs-you"
  | "ended"
  | "unavailable";

const STATES: Record<
  LiveViewState,
  { text: string; variant: "outline" | "success" | "warning"; busy?: true }
> = {
  starting: { text: "Starting", variant: "outline", busy: true },
  waiting: { text: "Waiting", variant: "outline" },
  connecting: { text: "Connecting", variant: "outline", busy: true },
  live: { text: "Live", variant: "success" },
  "needs-you": { text: "Needs you", variant: "warning" },
  ended: { text: "Ended", variant: "outline" },
  unavailable: { text: "Unavailable", variant: "outline" },
};

/** A live view's line above its screen: what it shows (a website, an application) and its state. */
export function LiveViewHeading({
  label,
  detail,
  state,
}: {
  label: string;
  detail?: string;
  state: LiveViewState;
}) {
  const { text, variant, busy } = STATES[state];
  return (
    <div className="flex min-w-0 items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{label}</p>
        {detail ? (
          <p className="truncate text-xs text-muted-foreground">{detail}</p>
        ) : null}
      </div>
      <Badge variant={variant} data-state={state}>
        {busy ? <Spinner aria-hidden="true" role={undefined} /> : null}
        {text}
      </Badge>
    </div>
  );
}

/**
 * A labelled live view without its screen (it starts, it waits for the browser, or it cannot be
 * shown): the sentence that says why sits where the screen goes, at the screen's size, so a grid
 * of views keeps its shape as each one connects.
 */
export function LiveViewPlaceholder({
  label,
  detail,
  state,
  children,
}: {
  label: string;
  detail?: string;
  state: LiveViewState;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={`Live view of your browser: ${label}`}
      className="flex min-w-0 flex-col gap-3"
    >
      <LiveViewHeading label={label} detail={detail} state={state} />
      <AspectRatio ratio={16 / 10}>
        <Empty className="size-full justify-center-safe gap-2 overflow-y-auto rounded-md border border-solid bg-muted p-6 md:p-6">
          <EmptyMedia className="mb-0 text-muted-foreground">
            {STATES[state].busy ? (
              <Spinner aria-hidden="true" role={undefined} className="size-5" />
            ) : (
              <MonitorPlay aria-hidden="true" className="size-5" />
            )}
          </EmptyMedia>
          <EmptyDescription role="status">{children}</EmptyDescription>
        </Empty>
      </AspectRatio>
    </section>
  );
}

function onVisibility(change: () => void) {
  document.addEventListener("visibilitychange", change);
  return () => {
    document.removeEventListener("visibilitychange", change);
  };
}

/** Whether the page is on screen: false in a tab in the background or a minimised window. */
function usePageShown(): boolean {
  return useSyncExternalStore(
    onVisibility,
    () => document.visibilityState !== "hidden",
    () => true,
  );
}

/**
 * One or more live views of the person's browser, each with its label and state (LiveBrowser
 * with `label`, or LiveViewPlaceholder), behind "Watch the browser": one view at a readable
 * width, several side by side as the space allows, stacked on a phone. Find jobs' progress and
 * the Applications page use it. Closed until the person opens it, and closed again while the page
 * is off screen: browserd takes an open view for a person who watches (an assistant's ask then
 * waits for them, up to 15 minutes), and each view costs the Jetson an x11vnc.
 */
export function LiveViews({
  label,
  open,
  onOpenChange,
  children,
}: {
  label: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  const shown = usePageShown();
  const views = Children.toArray(children);
  if (views.length === 0) return null;
  return (
    <Collapsible
      open={open}
      onOpenChange={onOpenChange}
      className="flex flex-col gap-3"
    >
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" className="w-fit">
          {open ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
          {open ? "Hide the browser" : "Watch the browser"}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="@container">
        {shown ? (
          <ul
            aria-label={label}
            className={cn(
              "grid gap-6",
              views.length === 1 && "max-w-3xl",
              views.length > 1 && "@3xl:grid-cols-2",
              views.length > 2 && "@7xl:grid-cols-3",
            )}
          >
            {views.map((view, index) => (
              <li
                key={isValidElement(view) ? (view.key ?? index) : index}
                className="min-w-0"
              >
                {view}
              </li>
            ))}
          </ul>
        ) : null}
      </CollapsibleContent>
    </Collapsible>
  );
}
