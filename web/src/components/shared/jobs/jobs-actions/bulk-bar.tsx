/**
 * The bulk bar of /jobs and its map: set a status (with Undo after), Apply, Like, add a note,
 * for the postings ticked in the list or on the map.
 */
import { Send, StickyNote, X } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import {
  BulkApplyDialog,
  type BulkApplyRequest,
} from "@/components/shared/apply/bulk-apply";
import { LikeButton } from "@/components/shared/apply/like-button";

import { BULK_MAX } from "@/features/jobs/api/jobs-api";
import { useBulkStatus } from "@/features/jobs/api/use-bulk-status";
import { STATUSES, statusLabel } from "@/features/jobs/helpers/labels";
import { plural, type BulkSelection } from "@/features/jobs/helpers/selection";
import { BulkNoteDialog } from "./bulk-note-dialog";
import { MarkAppliedDialog } from "./mark-applied-dialog";

export function BulkBar({ selection }: { selection: BulkSelection }) {
  const id = useId();
  const bulk = useBulkStatus();
  const [status, setStatus] = useState("");
  const [confirmApplied, setConfirmApplied] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  // Apply's confirmation, kept here: it outlives the bar once the click cleared the selection.
  const [applying, setApplying] = useState<BulkApplyRequest | null>(null);
  // Each opening of Apply's confirmation asks for its own preview.
  const opens = useRef(0);
  const count = selection.ids.length;
  const busy = bulk.busy;

  const applyStatus = (next: string) => {
    bulk.setStatus(selection.ids, next, () => {
      setStatus("");
      selection.clear();
    });
  };

  const hidden = count === 0 && !selection.selectingAll;

  return (
    <>
      {hidden ? null : (
        <div
          role="region"
          aria-label="Bulk actions"
          // Above the phone's bottom tab bar (--bottom-bar, app-shell.tsx).
          className="sticky bottom-[calc(var(--bottom-bar,0px)+0.75rem)] z-30 flex flex-col gap-2 rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg sm:bottom-4"
        >
          <div className="flex flex-wrap items-center gap-2">
            <p
              role="status"
              aria-live="polite"
              className="text-sm font-medium tabular-nums"
            >
              {selection.selectingAll ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner aria-hidden="true" role={undefined} />
                  Selecting
                </span>
              ) : (
                `${plural(count, "posting")} selected`
              )}
            </p>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <form
                className="flex items-center gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!status) return;
                  if (status === "applied") setConfirmApplied(true);
                  else applyStatus(status);
                }}
              >
                <Label htmlFor={`${id}-status`} className="sr-only">
                  New status
                </Label>
                <NativeSelect
                  id={`${id}-status`}
                  value={status}
                  className="w-40"
                  onChange={(event) => {
                    setStatus(event.target.value);
                  }}
                >
                  <option value="">New status</option>
                  {STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {statusLabel(value)}
                    </option>
                  ))}
                </NativeSelect>
                <Button
                  type="submit"
                  size="sm"
                  disabled={!status || busy || count === 0}
                >
                  {busy ? (
                    <Spinner aria-hidden="true" role={undefined} />
                  ) : null}
                  Set status
                </Button>
              </form>
              <Button
                size="sm"
                disabled={busy || count === 0}
                onClick={() => {
                  opens.current += 1;
                  setApplying({ ids: selection.ids, key: opens.current });
                }}
              >
                <Send aria-hidden="true" />
                Apply
              </Button>
              <LikeButton ids={selection.ids} onDone={selection.clear} />
              <Button
                variant="outline"
                size="sm"
                disabled={busy || count === 0}
                onClick={() => {
                  setNoteOpen(true);
                }}
              >
                <StickyNote aria-hidden="true" />
                Add note
              </Button>
              <Button variant="ghost" size="sm" onClick={selection.clear}>
                <X aria-hidden="true" />
                Clear
              </Button>
            </div>
          </div>
          {selection.cappedFrom !== null ? (
            <p role="note" className="text-xs font-medium text-warning">
              Only the first {BULK_MAX} of{" "}
              {plural(selection.cappedFrom, "posting")} in this view are
              selected: a bulk action takes {BULK_MAX} at a time.
            </p>
          ) : null}
          <MarkAppliedDialog
            open={confirmApplied}
            count={count}
            onOpenChange={setConfirmApplied}
            onConfirm={() => {
              setConfirmApplied(false);
              applyStatus("applied");
            }}
          />
          {noteOpen ? (
            <BulkNoteDialog
              ids={selection.ids}
              onClose={() => {
                setNoteOpen(false);
              }}
              onDone={() => {
                setNoteOpen(false);
                selection.clear();
                bulk.refresh();
              }}
            />
          ) : null}
        </div>
      )}
      <BulkApplyDialog
        request={applying}
        onClose={() => {
          setApplying(null);
        }}
        onApplied={selection.clear}
      />
    </>
  );
}
