import { useState } from "react";

import { LiveViewSlot } from "@/components/shared/apply/live-view-slot";
import { LiveViews } from "@/components/shared/browser/live-views";

import type { QueueItem } from "../api/queue-api";

const IN_THE_BROWSER = new Set(["filling", "approved", "submitting"]);

const FILL_STARTS = "Your browser is starting.";
const SUBMIT_WAITS =
  "Waiting for your browser. The app spaces out submissions to the same site, so this one may wait its turn.";
const SUBMIT_STATUS =
  "The assistant fills the form again with your approved answers, then submits it.";
const REHEARSAL_STATUS =
  "A rehearsal: the assistant fills the form again with your approved answers, then stops without submitting it.";
/** One click Apply's submit lease (mode `direct`): the click on Apply was the approval. */
const DIRECT_SUBMIT_STATUS =
  "The assistant fills the form again with the same answers, then sends it.";

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

function submitStatus(item: QueueItem): string {
  if (item.dry_run) return REHEARSAL_STATUS;
  return item.mode === "direct" ? DIRECT_SUBMIT_STATUS : SUBMIT_STATUS;
}

/**
 * On the Applications page, behind Watch the browser, the live view of each application the
 * assistant works on in the person's browser (LiveViewSlot, as on its job page), named by its
 * posting: its fill, watched with Use the browser myself, and its submit, watched only. Each lease
 * names the application it fills or sends, so two fills side by side each show their own window.
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
      {working.map((item) =>
        item.state === "filling" ? (
          <LiveViewSlot
            key={item.id}
            label={item.title}
            detail={item.company}
            idle={FILL_STARTS}
            leaseOf={{ application: item.id }}
          />
        ) : (
          <LiveViewSlot
            key={item.id}
            label={item.title}
            detail={item.company}
            idle={SUBMIT_WAITS}
            leaseOf={{ application: item.id }}
            status={submitStatus(item)}
            submit
          />
        ),
      )}
    </LiveViews>
  );
}
