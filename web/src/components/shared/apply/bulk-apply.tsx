import { CircleAlert, RotateCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

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
import { PostingNotices } from "@/components/shared/jobs/posting/posting-notices";
import { plural } from "@/features/jobs/helpers/selection";
import { describeError } from "@/lib/api";

import { refusalOf } from "@/features/apply/api/apply-api";
import type {
  BulkPreview,
  BulkStarted,
} from "@/features/apply/api/bulk-apply-api";
import {
  useBulkPreview,
  useStartBulkApply,
} from "@/features/apply/api/use-bulk-apply";
import {
  beyondLine,
  bulkDoes,
  documentsLine,
  leftOutLines,
  nameOf,
  summaryLine,
  writingLine,
} from "@/features/apply/helpers/bulk-apply-words";

/** One opening of the confirmation: the selection as it was clicked. */
export interface BulkApplyRequest {
  ids: readonly string[];
  /** A new read on every opening, never an older one. */
  key: number;
}

/**
 * Apply to the selected postings (owner, 2026-11-06: "in the job list and map, i want being able
 * with the multi select to apply at all selected"): one confirmation names every posting the
 * click applies to, with its documents ready or written first and their estimate, what the
 * review box makes it do, and apart, each selected posting it leaves out with its reason. "Apply
 * to N" is the person's approval of exactly those (CLAUDE.md rule 4): the api starts each through
 * its own Apply click, and the dialog then says what started and what was left out since, with a
 * link to Applications. The read is made once per opening, so the list never moves under the
 * person's eyes; `onApplied` clears the selection once the click is answered.
 */
export function BulkApplyDialog({
  request,
  onClose,
  onApplied,
}: {
  request: BulkApplyRequest | null;
  onClose: () => void;
  onApplied: () => void;
}) {
  const [notice, setNotice] = useState<string | null>(null);
  const [done, setDone] = useState<{
    started: BulkStarted;
    shown: BulkPreview;
  } | null>(null);
  const preview = useBulkPreview(request);
  const start = useStartBulkApply({
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (started, shown) => {
      setDone({ started, shown });
      onApplied();
    },
    onError: (error) => {
      const refusal = refusalOf(error);
      if (refusal?.code === "settings_changed") {
        // What the click does changed: show the confirmation as it reads now.
        setNotice(refusal.detail);
        void preview.refetch();
        return;
      }
      // Some may have started before the answer was lost: Applications shows what did.
      setNotice(
        `The click could not be confirmed. ${describeError(error)} Open Applications to see what started.`,
      );
    },
  });

  const close = () => {
    if (start.isPending) return;
    setNotice(null);
    setDone(null);
    start.reset();
    onClose();
  };
  const shown = preview.data;
  const count = shown?.items.length ?? 0;

  return (
    <AlertDialog
      open={request !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <AlertDialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        {done ? (
          <Summary {...done} onClose={close} />
        ) : (
          <>
            <AlertDialogHeader>
              <AlertDialogTitle>
                {shown
                  ? count > 0
                    ? `Apply to ${plural(count, "posting")}?`
                    : "None of these can be applied to"
                  : "Apply to the selected postings"}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {shown
                  ? count > 0
                    ? `${bulkDoes(shown.review_before_send)} Your click approves exactly the postings listed here.`
                    : "Each one is listed below with its reason."
                  : "Reading the selected postings."}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {preview.isPending && request !== null ? (
              <p
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <Spinner aria-hidden="true" role={undefined} />
                Reading the selected postings
              </p>
            ) : null}
            {preview.isError ? (
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription className="flex flex-col gap-2">
                  <p>
                    The selected postings could not be read.{" "}
                    {describeError(preview.error)}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-fit"
                    disabled={preview.isFetching}
                    onClick={() => {
                      void preview.refetch();
                    }}
                  >
                    <RotateCw aria-hidden="true" />
                    Try again
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}
            {notice ? (
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>{notice}</AlertDescription>
              </Alert>
            ) : null}
            {shown ? <Lists shown={shown} /> : null}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={start.isPending}>
                Cancel
              </AlertDialogCancel>
              {shown && count > 0 ? (
                <Button
                  disabled={start.isPending || preview.isFetching}
                  aria-busy={start.isPending}
                  onClick={() => {
                    start.mutate(shown);
                  }}
                >
                  {start.isPending ? (
                    <Spinner aria-hidden="true" role={undefined} />
                  ) : null}
                  Apply to {count}
                </Button>
              ) : null}
            </AlertDialogFooter>
          </>
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}

function Lists({ shown }: { shown: BulkPreview }) {
  const writing = writingLine(shown);
  const beyond = beyondLine(shown);
  return (
    <div className="flex min-w-0 flex-col gap-3 text-sm">
      {shown.items.length > 0 ? (
        <ul
          aria-label="Postings to apply to"
          className="flex flex-col divide-y rounded-lg border"
        >
          {shown.items.map((item) => (
            <li
              key={item.posting_id}
              className="flex min-w-0 flex-col gap-0.5 p-3"
            >
              <span className="font-medium [overflow-wrap:anywhere]">
                {nameOf(item)}
              </span>
              <span className="text-muted-foreground">
                {documentsLine(item)}
              </span>
              <PostingNotices notices={item.notices} compact className="mt-1" />
            </li>
          ))}
        </ul>
      ) : null}
      {writing ? (
        <p className="text-muted-foreground">
          {writing} {shown.cost_note}
        </p>
      ) : null}
      {beyond ? (
        <p role="note" className="font-medium text-warning">
          {beyond}
        </p>
      ) : null}
      {shown.left_out.length > 0 ? (
        <section
          aria-labelledby="bulk-left-out"
          className="flex flex-col gap-1"
        >
          <h3 id="bulk-left-out" className="font-medium">
            Not applied to ({shown.left_out.length})
          </h3>
          <ul
            aria-labelledby="bulk-left-out"
            className="flex flex-col divide-y rounded-lg border"
          >
            {shown.left_out.map((one) => (
              <li
                key={one.posting_id}
                className="flex min-w-0 flex-col gap-0.5 p-3"
              >
                <span className="font-medium [overflow-wrap:anywhere]">
                  {nameOf(one)}
                </span>
                <span className="text-muted-foreground">{one.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Summary({
  started,
  shown,
  onClose,
}: {
  started: BulkStarted;
  shown: BulkPreview;
  onClose: () => void;
}) {
  const lines = leftOutLines(started, shown);
  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>
          {started.started > 0
            ? `Applying to ${plural(started.started, "posting")}`
            : "Nothing was started"}
        </AlertDialogTitle>
        <AlertDialogDescription>
          {started.started > 0
            ? `${bulkDoes(shown.review_before_send)} Follow each one in Applications.`
            : "Each one is listed below with its reason."}
        </AlertDialogDescription>
      </AlertDialogHeader>
      <div role="status" className="flex flex-col gap-2 text-sm">
        <p className="font-medium">{summaryLine(started)}</p>
        {lines.length > 0 ? (
          <ul aria-label="Left out" className="flex flex-col gap-1">
            {lines.map((line, index) => (
              <li
                key={`${String(index)}:${line}`}
                className="[overflow-wrap:anywhere]"
              >
                {line}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <AlertDialogFooter>
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
        <Button asChild>
          <Link to="/applications" onClick={onClose}>
            Open Applications
          </Link>
        </Button>
      </AlertDialogFooter>
    </>
  );
}
