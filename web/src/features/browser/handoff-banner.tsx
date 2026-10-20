import { Check, KeyRound, MonitorPlay, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { describeError, errorDetail } from "@/lib/api";

import { LiveBrowser } from "./live-browser";
import { useHandoff } from "./use-handoff";

const AT_THE_KEYBOARD =
  "You have the keyboard and the mouse: sign in, then click I'm signed in.";

function minutesText(minutes: number | null): string {
  if (minutes === null) return "";
  return minutes === 1 ? "1 minute left." : `${String(minutes)} minutes left.`;
}

/**
 * The sign in hand off for the person (SPEC-browser, "The sign in hand off"; browser B12b): an
 * assistant met a login or sign up wall while the person watched and waits for them. The site,
 * the minutes left, "Open the live view" (the person at the keyboard, in a browser started
 * without any debugging port), "I'm signed in" and "Cancel". Nothing when no hand off waits.
 * `liveOpen`: the live view shows from the start (Account, where the person was watching).
 */
export function HandoffBanner({ liveOpen = false }: { liveOpen?: boolean }) {
  const { handoff, done, cancel } = useHandoff();
  const [open, setOpen] = useState(liveOpen);
  const title = useId();
  if (!handoff) return null;
  const busy = done.isPending || cancel.isPending;
  const failure = done.error ?? cancel.error;
  return (
    <section
      aria-labelledby={title}
      className="flex flex-col gap-3 rounded-md border border-amber-500/60 bg-amber-50 p-3 text-amber-950 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <h3 id={title} className="flex items-center gap-2 font-medium">
        <KeyRound aria-hidden className="size-4 shrink-0" />
        <span className="break-words">Sign in to {handoff.site}</span>
      </h3>
      <p className="text-sm">
        The assistant waits for you. Sign in or sign up yourself in the live
        view, then click I&apos;m signed in. The app never sees what you type.{" "}
        {minutesText(handoff.minutesLeft)}
      </p>
      {open && <LiveBrowser person status={AT_THE_KEYBOARD} />}
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {errorDetail(failure) ?? describeError(failure)}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {!open && (
          <Button
            type="button"
            onClick={() => {
              setOpen(true);
            }}
          >
            <MonitorPlay aria-hidden />
            Open the live view
          </Button>
        )}
        <Button
          type="button"
          variant={open ? "default" : "outline"}
          disabled={busy}
          onClick={() => {
            done.mutate();
          }}
        >
          <Check aria-hidden />
          I&apos;m signed in
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => {
            cancel.mutate();
          }}
        >
          <X aria-hidden />
          Cancel
        </Button>
      </div>
    </section>
  );
}
