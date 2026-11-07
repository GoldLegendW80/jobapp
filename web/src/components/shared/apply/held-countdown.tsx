import { Hourglass } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";

import { refusalOf } from "@/features/apply/api/apply-api";
import { useCancelHold } from "@/features/apply/api/use-application-actions";
import { ConfirmDialog } from "./confirm-dialog";
import {
  clockText,
  holdLeft,
  spokenLeft,
} from "@/features/apply/helpers/full-auto-state";

const TICK_MS = 1000;

const ASK = {
  title: "Cancel this application?",
  sentence:
    "The hold stops and nothing is sent. If the app already started submitting, it is too late and the page says so.",
  confirm: "Cancel the application",
};

/**
 * The hold of a Full auto item (SPEC-apply.md, "Frontend"): the time left before the app checks
 * everything once more and submits, read on the server's clock (`offset`, server minus page), and
 * Cancel. The server has the last word: a Cancel that comes too late shows its sentence.
 */
export function HeldCountdown({
  applicationId,
  until,
  offset,
}: {
  applicationId: string;
  until: string;
  offset: number;
}) {
  const [pageNow, setPageNow] = useState(() => Date.now());
  const [asking, setAsking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const left = holdLeft(until, offset, pageNow);
  const running = left > 0;

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      setPageNow(Date.now());
    }, TICK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [running]);

  const cancel = useCancelHold(applicationId, {
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: () => {
      setAsking(false);
      toast.success("Cancelled during the hold. Nothing was sent.");
    },
    onError: (error) => {
      setAsking(false);
      setNotice(refusalOf(error)?.detail ?? describeError(error));
    },
  });

  return (
    // A note, not an alert: the box was never announced, and its timer ticks every second.
    <Alert variant="warning" role="note">
      <Hourglass aria-hidden="true" />
      <AlertDescription className="gap-2 text-foreground">
        {left > 0 ? (
          <p>
            Full auto submits this application in{" "}
            <span
              role="timer"
              aria-label={spokenLeft(left)}
              className="font-semibold tabular-nums"
            >
              {clockText(left)}
            </span>
            , after a last check, unless you cancel.
          </p>
        ) : (
          <p role="status">
            The hold has ended: the app checks everything once more, then
            submits. You can still cancel until it starts.
          </p>
        )}
        {notice ? (
          <Alert variant="destructive">
            <AlertDescription>{notice}</AlertDescription>
          </Alert>
        ) : null}
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={cancel.isPending}
          onClick={() => {
            setAsking(true);
          }}
        >
          Cancel application
        </Button>
        <ConfirmDialog
          ask={asking ? ASK : null}
          pending={cancel.isPending}
          onCancel={() => {
            setAsking(false);
          }}
          onConfirm={() => {
            cancel.mutate();
          }}
        />
      </AlertDescription>
    </Alert>
  );
}
