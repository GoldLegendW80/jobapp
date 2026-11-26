import { CircleStop } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

import type { QueueItem } from "../api/queue-api";
import { useCancelAll } from "../api/use-application-actions";
import { cancelKindOf } from "../helpers/apply-section-rules";
import {
  CANCEL_ALL_FROM,
  cancelAllOf,
  cancelResultLine,
} from "../helpers/queue-state";

/** One application the confirmation names, as it showed it. */
interface Named {
  id: string;
  company: string;
  title: string;
  /** The assistant already acted on a send button: the site may have it. */
  maybeSent: boolean;
}

/**
 * "Cancel all" above the applications under way (A24; owner, 2026-11-26: "je veux pouvoir cancel
 * une application à tout moment"): one confirmation names each posting and says which ones may
 * already have been sent, and one click cancels exactly those, each through the same cancel as
 * its own button; what an Apply to a selection started is among them. The list is the one read
 * when the confirmation opened, never refreshed under the person's eyes, and the result of each
 * is said in words.
 */
export function CancelAll({ items }: { items: readonly QueueItem[] }) {
  const results = useRef<HTMLDivElement>(null);
  const [named, setNamed] = useState<Named[] | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const going = cancelAllOf(items);

  const cancel = useCancelAll<Named>({
    onSuccess: (done, list) => {
      const byId = new Map(list.map((one) => [one.id, one]));
      setLines(
        done.results.map((result) =>
          cancelResultLine(
            byId.get(result.application_id) ?? {
              company: "An application",
              title: "",
            },
            result,
          ),
        ),
      );
      if (done.left_out === 0) {
        toast.success(
          done.stopped === 1
            ? "1 application stopped."
            : `${String(done.stopped)} applications stopped.`,
        );
      }
      setNamed(null);
    },
    onError: (error) => {
      setLines([
        `Cancel all could not finish. ${describeError(error)} Open each application to see where it stands.`,
      ]);
      setNamed(null);
    },
  });
  const maybeSent = (named ?? []).filter((one) => one.maybeSent).length;

  return (
    <div className="flex flex-col gap-3">
      {going.length >= CANCEL_ALL_FROM ? (
        <Button
          variant="outline"
          className="w-full sm:w-fit"
          disabled={cancel.isPending}
          onClick={() => {
            setLines([]);
            cancel.reset();
            setNamed(
              going.map((item) => ({
                id: item.id,
                company: item.company,
                title: item.title,
                maybeSent: cancelKindOf(item) === "cancelAfterSend",
              })),
            );
          }}
        >
          <CircleStop aria-hidden="true" />
          Cancel all ({going.length})
        </Button>
      ) : null}
      {/* Always in the page, so a screen reader hears the results when they arrive. */}
      <div
        ref={results}
        role="status"
        tabIndex={-1}
        className="outline-none empty:hidden"
      >
        {lines.length > 0 ? (
          <Alert>
            <AlertDescription>
              <ul className="flex flex-col gap-1" aria-label="Cancel all">
                {lines.map((line, index) => (
                  <li key={`${String(index)}:${line}`}>{line}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>
      <AlertDialog
        open={named !== null}
        onOpenChange={(open) => {
          if (!open && !cancel.isPending) setNamed(null);
        }}
      >
        <AlertDialogContent
          className="max-h-[90dvh] overflow-y-auto"
          onCloseAutoFocus={(event) => {
            if (lines.length === 0) return;
            // The results take the focus: the button is gone with what it cancelled.
            event.preventDefault();
            results.current?.focus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              Cancel {named?.length ?? 0} applications?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {maybeSent > 0
                ? "The assistant stops each one now and sends nothing more. Those marked below may already have been sent: check the site or your mail for them afterwards."
                : "The assistant stops each one now. Nothing is sent."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul
            aria-label="Applications to cancel"
            className="flex flex-col divide-y rounded-lg border text-sm"
          >
            {(named ?? []).map((one) => (
              <li key={one.id} className="flex min-w-0 flex-col gap-0.5 p-3">
                <span className="font-medium [overflow-wrap:anywhere]">
                  {one.company}, {one.title}
                </span>
                <span
                  className={
                    one.maybeSent ? "text-warning" : "text-muted-foreground"
                  }
                >
                  {one.maybeSent
                    ? "May already have been sent: a send button was clicked."
                    : "Not sent yet."}
                </span>
              </li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cancel.isPending}>
              Keep them
            </AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={cancel.isPending || named === null}
              onClick={() => {
                if (named) cancel.mutate(named);
              }}
            >
              {cancel.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Cancel all {named?.length ?? 0}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
