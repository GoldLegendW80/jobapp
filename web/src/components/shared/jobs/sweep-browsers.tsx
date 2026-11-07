import { useState } from "react";

import { LiveBrowser } from "@/components/shared/browser/live-browser";
import {
  LiveViewPlaceholder,
  LiveViews,
} from "@/components/shared/browser/live-views";
import { askText, STARTING } from "@/features/agents/helpers/run-browser";
import {
  leasesOf,
  waitText,
  type Browser,
  type Lease,
} from "@/features/browser/api/browser-api";
import { useBrowser } from "@/features/browser/api/use-browser";
import type { Sweep, SweepSource } from "@/features/jobs/api/sweeps-api";
import { sourceName } from "@/features/jobs/helpers/labels";

// How often a view reads which lease the person's browser holds (it starts, or ends).
const LEASE_POLL_MS = 3_000;
/** The purpose of the lease a sweep holds while it searches a website (api leases.LEASE_MAX_S). */
const SWEEP_PURPOSE = "sweep";
const BUSY =
  "The browser is busy. This website's search starts when it is free.";
const UNAVAILABLE = "The browser is not available right now.";

/** A job website the sweep searches in the person's browser now (api interactive.py, `llm_browser`). */
function searchedInTheBrowser(source: SweepSource): boolean {
  return source.mode === "llm_browser" && source.phase === "running";
}

type View =
  | { show: "live"; lease: Lease }
  | { show: "starting" | "unavailable" }
  | { show: "waiting"; sentence: string };

/**
 * What a website searched now shows of the browser: the person's sweep lease that names this
 * website (`source_id`, one per window since two browsers at once), else, from a server whose
 * leases name no website, the one sweep lease while this website is the only one searched.
 */
function viewOf(
  browser: Browser | undefined,
  source: SweepSource,
  alone: boolean,
): View {
  if (!browser) return { show: "starting" };
  if (!browser.available) return { show: "unavailable" };
  const sweeps = leasesOf(browser).filter(
    (lease) => lease.automation && lease.purpose === SWEEP_PURPOSE,
  );
  const own =
    sweeps.find(
      (lease) => source.source_id && lease.source_id === source.source_id,
    ) ?? (alone ? sweeps.find((lease) => !lease.source_id) : undefined);
  if (own) {
    return own.state === "starting"
      ? { show: "starting" }
      : { show: "live", lease: own };
  }
  const place = browser.queue.find((item) => item.purpose === SWEEP_PURPOSE);
  if (place) return { show: "waiting", sentence: waitText(place) };
  if (browser.holder !== null) return { show: "waiting", sentence: BUSY };
  return { show: "starting" };
}

function SweepView({ source, alone }: { source: SweepSource; alone: boolean }) {
  const browser = useBrowser({ refetchInterval: LEASE_POLL_MS });
  const view = viewOf(browser.data, source, alone);
  const label = sourceName(source);
  const detail = source.host ?? undefined;
  if (view.show === "live") {
    const ask = askText(view.lease);
    return (
      <LiveBrowser
        key={view.lease.id}
        slot={view.lease.slot ?? 0}
        label={label}
        detail={detail}
        needsYou={ask !== null}
        status={ask ?? undefined}
      />
    );
  }
  return (
    <LiveViewPlaceholder label={label} detail={detail} state={view.show}>
      {view.show === "waiting"
        ? view.sentence
        : view.show === "unavailable"
          ? UNAVAILABLE
          : STARTING}
    </LiveViewPlaceholder>
  );
}

/**
 * The live view of each job website a running sweep searches in the person's browser (browser
 * B18's view, on Find jobs' progress), behind Watch the browser: watched, with LiveBrowser's own
 * Use the browser myself. Its open state outlives the gap between two websites. Nothing while
 * only the sources that make no browser call are read.
 */
export function SweepBrowsers({ sweep }: { sweep: Sweep }) {
  const [open, setOpen] = useState(false);
  const sources = sweep.sources.filter(searchedInTheBrowser);
  return (
    <LiveViews
      label="Websites searched in your browser"
      open={open}
      onOpenChange={setOpen}
    >
      {sources.map((source) => (
        <SweepView
          key={source.source_id ?? sourceName(source)}
          source={source}
          alone={sources.length === 1}
        />
      ))}
    </LiveViews>
  );
}
