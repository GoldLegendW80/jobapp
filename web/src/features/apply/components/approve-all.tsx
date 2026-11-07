import { CheckCheck } from "lucide-react";
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

import { documentsSentence, leftFields } from "../helpers/apply-state";
import type { QueueItem } from "../api/queue-api";
import { useApprovals, useApproveReady } from "../api/use-approvals";
import {
  isReviewable,
  readyForAll,
  readyResultLine,
} from "../helpers/queue-state";

/** One application the confirmation names, as it showed it. */
interface Named {
  id: string;
  answersHash: string;
  company: string;
  title: string;
  documents: string;
  left: string[];
}

/** At least this many complete forms show the button: one form has its own Approve. */
export const APPROVE_ALL_FROM = 2;

/**
 * "Approve all ready" above the queue (owner, 2026-11-05): one confirmation names every company,
 * the posting and the documents each form sends, and one click approves exactly those, each
 * through the same gate as its own Approve and submit, with the hash of the form shown. The
 * approvals are the cards' own reads (`approvalKey`), so nothing is read twice. An item that
 * changed or stopped waiting after the confirmation opened is left out, and the result of each
 * is said in words. `unsaved` counts the answers typed in a card and not saved yet: such a form
 * waits for its own click.
 */
export function ApproveAll({
  items,
  unsaved,
}: {
  items: readonly QueueItem[];
  unsaved: Readonly<Record<string, number>>;
}) {
  const results = useRef<HTMLDivElement>(null);
  const [named, setNamed] = useState<Named[] | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const candidates = items.filter(
    (item) => item.state === "ready" && isReviewable(item),
  );
  const approvals = useApprovals(candidates.map((item) => item.id));
  const ready: Named[] = [];
  candidates.forEach((item, index) => {
    const approval = approvals[index]?.data;
    if (!readyForAll(item, approval, unsaved[item.id] ?? 0)) return;
    ready.push({
      id: item.id,
      answersHash: approval.answers_hash,
      company: item.company,
      title: item.title,
      documents: documentsSentence(approval),
      left: leftFields(approval.answers).map((answer) => answer.label),
    });
  });
  const waitingMore = candidates.length - ready.length;

  const approve = useApproveReady<Named>({
    onSuccess: (done, list) => {
      const byId = new Map(list.map((one) => [one.id, one]));
      setLines(
        done.results.map((result) => {
          const one = byId.get(result.application_id);
          return readyResultLine(
            one ?? { company: "An application", title: "" },
            result,
          );
        }),
      );
      if (done.left_out === 0) {
        toast.success(
          done.approved === 1
            ? "1 application approved. It is being sent."
            : `${String(done.approved)} applications approved. They are being sent.`,
        );
      }
      setNamed(null);
    },
    onError: (error) => {
      setLines([`Nothing was approved. ${describeError(error)}`]);
      setNamed(null);
    },
  });

  const summary =
    approve.data === undefined
      ? null
      : approve.data.left_out === 0
        ? `${plural(approve.data.approved)} approved.`
        : `${plural(approve.data.approved)} approved, ${String(approve.data.left_out)} left out.`;

  return (
    <div className="flex flex-col gap-3">
      {ready.length >= APPROVE_ALL_FROM ? (
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
          <Button
            className="w-full sm:w-fit"
            disabled={approve.isPending}
            onClick={() => {
              setLines([]);
              approve.reset();
              setNamed(ready);
            }}
          >
            <CheckCheck aria-hidden="true" />
            Approve all ready ({ready.length})
          </Button>
          <p className="text-sm text-muted-foreground">
            {waitingMore > 0
              ? `${plural(ready.length)} complete. ${String(waitingMore)} more ${waitingMore === 1 ? "needs" : "need"} you first.`
              : `${plural(ready.length)} complete, each checked again before it is sent.`}
          </p>
        </div>
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
            <AlertDescription className="flex flex-col gap-1">
              {summary ? <p className="font-medium">{summary}</p> : null}
              <ul
                className="flex flex-col gap-1"
                aria-label="Approve all ready"
              >
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
          if (!open && !approve.isPending) setNamed(null);
        }}
      >
        <AlertDialogContent
          className="max-h-[90dvh] overflow-y-auto"
          onCloseAutoFocus={(event) => {
            if (lines.length === 0) return;
            // The results take the focus: the button may be gone with what it approved.
            event.preventDefault();
            results.current?.focus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              Submit {named?.length ?? 0} applications?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The app fills each form again with exactly the answers you saw,
              then clicks Submit. It cannot be taken back. An application that
              changed since you opened this is left out.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul
            aria-label="Applications to submit"
            className="flex flex-col divide-y rounded-lg border text-sm"
          >
            {(named ?? []).map((one) => (
              <li key={one.id} className="flex min-w-0 flex-col gap-0.5 p-3">
                <span className="font-medium [overflow-wrap:anywhere]">
                  {one.company}, {one.title}
                </span>
                <span className="text-muted-foreground">
                  Sends: {one.documents}
                </span>
                {one.left.length > 0 ? (
                  <span className="text-warning">
                    Left empty: {one.left.join(", ")}.
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={approve.isPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              disabled={approve.isPending || named === null}
              onClick={() => {
                if (named) approve.mutate(named);
              }}
            >
              {approve.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Approve and submit all
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function plural(count: number): string {
  return count === 1 ? "1 form" : `${String(count)} forms`;
}
