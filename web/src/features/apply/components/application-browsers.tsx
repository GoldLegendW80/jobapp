import { useState } from "react";

import { LiveViewSlot } from "@/components/shared/apply/live-view-slot";
import { LiveViews } from "@/components/shared/browser/live-views";

import type { QueueItem } from "../api/queue-api";
import { agentWaits } from "../helpers/agent-wait";
import { liveStatus, liveWait, liveWatchOnly } from "../helpers/live-view";

const IN_THE_BROWSER = new Set(["filling", "approved", "submitting"]);

/**
 * An application the assistant works on in the person's browser, or is about to: its fill, or
 * its submit once approved. Apply myself's session has its own panel on the job page, and an
 * application by e-mail uses no browser.
 */
function inTheBrowser(item: QueueItem): boolean {
  return (
    item.ats !== "email" &&
    item.mode !== "manual" &&
    IN_THE_BROWSER.has(item.state)
  );
}

/**
 * On the Applications page, behind Watch the browser, the live view of each application the
 * assistant works on in the person's browser (LiveViewSlot, as on its job page), named by its
 * posting. Each shows the lease the api names for its application, whatever its state (A23): a
 * fill with Use the browser myself, the submit lease and Review first's agent after the Confirm
 * watch only. Two fills side by side each show their own window.
 */
export function ApplicationBrowsers({ items }: { items: QueueItem[] }) {
  const [open, setOpen] = useState(false);
  const working = items.filter(inTheBrowser);
  return (
    <LiveViews
      label="Applications in your browser"
      open={open}
      onOpenChange={setOpen}
    >
      {working.map((item) => (
        <LiveViewSlot
          key={item.id}
          label={item.title}
          detail={item.company}
          idle={liveWait(item)}
          leaseOf={{ application: item.id }}
          status={(purpose) => liveStatus(purpose, item)}
          watchOnly={(purpose) => liveWatchOnly(purpose, item)}
          namedOnly={item.state !== "filling"}
          assistantWaits={agentWaits(item)}
        />
      ))}
    </LiveViews>
  );
}
