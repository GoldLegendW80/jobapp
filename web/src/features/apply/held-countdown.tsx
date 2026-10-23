import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Hourglass } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";

import { cancelApplication, refusalOf } from "./apply-api";
import { ConfirmDialog } from "./confirm-dialog";
import { clockText, holdLeft, spokenLeft } from "./full-auto-state";

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
  const queryClient = useQueryClient();
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

  const cancel = useMutation({
    mutationFn: () => cancelApplication(applicationId),
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
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["apply"] });
    },
  });

  return (
    <div className="flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950">
      <div className="flex items-start gap-2">
        <Hourglass aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
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
      </div>
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
    </div>
  );
}
