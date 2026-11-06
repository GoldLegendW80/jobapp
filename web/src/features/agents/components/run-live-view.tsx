/**
 * The live browser on the page of a website search run (browser B18): the run of the agent
 * `browser_discovery` reads one job website in the person's browser, and while it does, the page
 * shows that browser, the same LiveBrowser as Sign in and Account's "Your browser".
 *
 * It only reads (GET /api/browser) and watches: it never starts, ends or takes a lease. Take
 * over and Give back are LiveBrowser's own, as on Account: the agent's next tool call waits, and
 * neither the lease nor the search ends by itself (the website's share of the sweep's time goes
 * on, so a long Take over can use it up: tasks/browser/todo.md, B18).
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { useFreshBrowser } from "@/features/browser/api/use-browser";
import { LiveBrowser } from "@/components/shared/browser/live-browser";

import {
  ELSEWHERE,
  askText,
  STARTING,
  WEBSITE_AGENT,
  browserOfRun,
} from "../helpers/run-browser";
import type { Run } from "../api/runs-api";

// How often the page reads whether the search holds the browser (it starts, or ends).
const LEASE_POLL_MS = 3_000;

function Starting() {
  return (
    <p role="status" className="text-sm text-muted-foreground">
      {STARTING}
    </p>
  );
}

/**
 * The line that replaces the view. It is announced (role="status"), and when the focus was in
 * the view as it closed (on Take over, Give back or Full screen), it takes the focus instead of
 * letting it fall to the page.
 */
function Elsewhere({
  takeFocus = false,
  onFocused,
}: {
  takeFocus?: boolean;
  onFocused?: () => void;
}) {
  const line = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!takeFocus) return;
    line.current?.focus();
    onFocused?.();
  }, [takeFocus, onFocused]);
  return (
    <p
      ref={line}
      role="status"
      tabIndex={-1}
      className="rounded-sm text-sm text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      {ELSEWHERE}
    </p>
  );
}

/**
 * Mounted only while the run runs, so its read of the browser is a fresh one. As it goes, it
 * tells `onClose` whether the focus was inside the view.
 */
function RunningView({
  run,
  onClose,
}: {
  run: Run;
  onClose: (focusWasInside: boolean) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(
    () => () => {
      // The cleanup runs before the view's elements leave the page.
      onClose(box.current?.contains(document.activeElement) ?? false);
    },
    [onClose],
  );
  const browser = useFreshBrowser(LEASE_POLL_MS);
  const view = browserOfRun(run, browser);
  if (view.show === "starting") return <Starting />;
  if (view.show === "elsewhere") return <Elsewhere />;
  const ask = askText(view.lease);
  return (
    <div ref={box} data-testid="run-live-view" className="space-y-2">
      {ask ? (
        <p
          role="status"
          data-testid="run-ask-person"
          className="break-words rounded-md border border-border bg-muted px-3 py-2 text-sm text-foreground"
        >
          {ask}
        </p>
      ) : null}
      <LiveBrowser key={view.lease.id} />
    </div>
  );
}

/**
 * Under a website search run's header: the live view while it runs, "Your browser is starting."
 * while it is queued or waits for its lease, and one line once it ended. Nothing for any other
 * agent's run, or while the run is still loading.
 */
export function RunLiveView({ run }: { run: Run | undefined }) {
  // Whether the focus was in the view when it closed: the line that replaces it takes it then.
  const [takeFocus, setTakeFocus] = useState(false);
  const onFocused = useCallback(() => {
    setTakeFocus(false);
  }, []);
  if (run?.agent !== WEBSITE_AGENT) return null;
  if (run.status === "queued") return <Starting />;
  if (run.status === "running") {
    return <RunningView run={run} onClose={setTakeFocus} />;
  }
  return <Elsewhere takeFocus={takeFocus} onFocused={onFocused} />;
}
