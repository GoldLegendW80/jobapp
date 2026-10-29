/**
 * The live browser on the page of a website search run (browser B18): the run of the agent
 * `browser_discovery` reads one job website in the person's browser, and while it does, the page
 * shows that browser, the same LiveBrowser as Sign in and Account's "Your browser".
 *
 * It only reads (GET /api/browser) and watches: it never starts, ends or takes a lease. Take
 * over and Give back are LiveBrowser's own, as on Account: the agent's next tool call waits, and
 * neither the lease nor the search ends (the website's share of the sweep's time goes on, so a
 * long Take over can use it up: tasks/browser/todo.md, B18).
 */
import { useQuery } from "@tanstack/react-query";

import { browserQueryKey, getBrowser } from "@/features/browser/browser-api";
import { LiveBrowser } from "@/features/browser/live-browser";

import {
  ELSEWHERE,
  STARTING,
  WEBSITE_AGENT,
  browserOfRun,
} from "./run-browser";
import type { Run } from "./runs-api";

// How often the page reads whether the search holds the browser (it starts, or ends).
const LEASE_POLL_MS = 3_000;

function Starting() {
  return (
    <p role="status" className="text-sm text-muted-foreground">
      {STARTING}
    </p>
  );
}

function Elsewhere() {
  return <p className="text-sm text-muted-foreground">{ELSEWHERE}</p>;
}

/** Mounted only while the run runs, so its read of the browser is a fresh one. */
function RunningView({ run }: { run: Run }) {
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: LEASE_POLL_MS,
    // The cache may hold the lease of the website searched before this one.
    refetchOnMount: "always",
  });
  const view = browserOfRun(
    run,
    query.isFetchedAfterMount ? query.data : undefined,
  );
  if (view.show === "starting") return <Starting />;
  if (view.show === "elsewhere") return <Elsewhere />;
  return (
    <div data-testid="run-live-view">
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
  if (run?.agent !== WEBSITE_AGENT) return null;
  if (run.status === "queued") return <Starting />;
  if (run.status === "running") return <RunningView run={run} />;
  return <Elsewhere />;
}
