/**
 * Actions of /jobs: the selection (checkboxes, Shift ranges, everything in the view up to the
 * bulk cap), the bulk bar (set status, add a note, with Undo after a status change), the "Add a
 * posting" dialog and the page menu with Rescore.
 */
import {
  useQueryClient,
  type InfiniteData,
  type QueryKey,
} from "@tanstack/react-query";
import {
  CircleAlert,
  EllipsisVertical,
  LoaderCircle,
  Plus,
  RefreshCw,
  StickyNote,
  X,
} from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import {
  addJob,
  BULK_MAX,
  bulkUpdate,
  duplicateOf,
  jobsKey,
  rescoreAll,
  undoBulk,
  type JobListPage,
  type RescoreResult,
} from "./api";
import { CONTRACT_TYPES, STATUSES, statusLabel } from "./labels";
import { plural, rescoreSummary, type Selection } from "./selection";

const LIST_KEY: QueryKey = [...jobsKey, "list"];

/* --- bulk bar -------------------------------------------------------------------------- */

type ListData = InfiniteData<JobListPage, string | null>;

/** Set `status` on the given rows in every cached list page; returns the snapshot to restore. */
function setCachedStatus(
  client: ReturnType<typeof useQueryClient>,
  ids: ReadonlySet<string>,
  status: string,
) {
  const snapshot = client.getQueriesData<ListData>({ queryKey: LIST_KEY });
  client.setQueriesData<ListData>({ queryKey: LIST_KEY }, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.map((item) =>
              ids.has(item.id) ? { ...item, status } : item,
            ),
          })),
        }
      : data,
  );
  return snapshot;
}

export function BulkBar({ selection }: { selection: Selection }) {
  const client = useQueryClient();
  const id = useId();
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmApplied, setConfirmApplied] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const count = selection.ids.length;

  const refresh = () => {
    void client.invalidateQueries({ queryKey: jobsKey });
  };

  const undo = (token: string) => {
    undoBulk(token).then(
      (result) => {
        const skipped = result.skipped.length
          ? ` ${plural(result.skipped.length, "posting")} changed since and kept ${result.skipped.length === 1 ? "its" : "their"} new status.`
          : "";
        toast.success(
          `Undone: ${plural(result.restored.length, "posting")} back to ${result.restored.length === 1 ? "its" : "their"} previous status.${skipped}`,
        );
        refresh();
      },
      (error: unknown) => {
        toast.error(
          isApiError(error) && (error.status === 410 || error.status === 409)
            ? (errorDetail(error) ?? "Nothing to undo.")
            : `The undo failed. ${describeError(error)}`,
        );
        refresh();
      },
    );
  };

  const applyStatus = (next: string) => {
    const ids = selection.ids;
    const snapshot = setCachedStatus(client, new Set(ids), next);
    setBusy(true);
    bulkUpdate(ids, { status: next }).then(
      (result) => {
        setBusy(false);
        setStatus("");
        selection.clear();
        refresh();
        const message = `${plural(result.changed, "posting")} set to ${statusLabel(next)}${result.unchanged ? `, ${result.unchanged} already were` : ""}.`;
        const token = result.undo_token;
        if (token)
          toast.success(message, {
            duration: 15_000,
            action: {
              label: "Undo",
              onClick: () => {
                undo(token);
              },
            },
          });
        else toast.success(message);
      },
      (error: unknown) => {
        setBusy(false);
        for (const [key, data] of snapshot) client.setQueryData(key, data);
        toast.error(`Nothing was changed. ${describeError(error)}`);
        refresh();
      },
    );
  };

  if (count === 0 && !selection.selectingAll) return null;

  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className="sticky bottom-3 z-30 flex flex-col gap-2 rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg sm:bottom-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <p
          role="status"
          aria-live="polite"
          className="text-sm font-medium tabular-nums"
        >
          {selection.selectingAll ? (
            <span className="inline-flex items-center gap-2">
              <LoaderCircle
                aria-hidden="true"
                className="size-4 animate-spin"
              />
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
              <option value="">Set status</option>
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
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : null}
              Apply
            </Button>
          </form>
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
          Only the first {BULK_MAX} of {plural(selection.cappedFrom, "posting")}{" "}
          in this view are selected: a bulk action takes {BULK_MAX} at a time.
        </p>
      ) : null}
      <AlertDialog open={confirmApplied} onOpenChange={setConfirmApplied}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Mark {plural(count, "posting")} as applied?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Postings without an applied date get today. You can undo this for
              10 minutes.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              onClick={() => {
                setConfirmApplied(false);
                applyStatus("applied");
              }}
            >
              Mark as applied
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {noteOpen ? (
        <BulkNoteDialog
          ids={selection.ids}
          onClose={() => {
            setNoteOpen(false);
          }}
          onDone={() => {
            setNoteOpen(false);
            selection.clear();
            refresh();
          }}
        />
      ) : null}
    </div>
  );
}

function BulkNoteDialog({
  ids,
  onClose,
  onDone,
}: {
  ids: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    setPending(true);
    setError("");
    bulkUpdate(ids, { notes_append: text.trim() }).then(
      (result) => {
        setPending(false);
        toast.success(`Note added to ${plural(result.changed, "posting")}.`);
        onDone();
      },
      (failure: unknown) => {
        setPending(false);
        setError(`Nothing was changed. ${describeError(failure)}`);
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Add a note to {plural(ids.length, "posting")}
          </DialogTitle>
          <DialogDescription>
            The note goes on a new line at the end of each posting&apos;s notes.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-2">
          <Label htmlFor={`${id}-note`}>Note</Label>
          <Textarea
            id={`${id}-note`}
            value={text}
            rows={4}
            maxLength={100_000}
            onChange={(event) => {
              setText(event.target.value);
            }}
          />
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            disabled={pending || !text.trim()}
            aria-busy={pending}
          >
            {pending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            Add note
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* --- add a posting --------------------------------------------------------------------- */

interface Draft {
  company: string;
  title: string;
  location: string;
  contract_type: string;
  url: string;
  posting_text: string;
}

const EMPTY_DRAFT: Draft = {
  company: "",
  title: "",
  location: "",
  contract_type: "",
  url: "",
  posting_text: "",
};

export function AddPostingButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Plus aria-hidden="true" />
        Add a posting
      </Button>
      {open ? (
        <AddPostingDialog
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

function AddPostingDialog({ onClose }: { onClose: () => void }) {
  const id = useId();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{
    text: string;
    existing?: string;
  } | null>(null);
  const missing = !draft.company.trim() || !draft.title.trim();

  const field = (name: keyof Draft) => ({
    id: `${id}-${name}`,
    value: draft[name],
    onChange: (event: { target: { value: string } }) => {
      setDraft((old) => ({ ...old, [name]: event.target.value }));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (missing) return;
    setPending(true);
    setError(null);
    addJob({
      company: draft.company.trim(),
      title: draft.title.trim(),
      location: draft.location.trim(),
      ...(draft.contract_type ? { contract_type: draft.contract_type } : {}),
      url: draft.url.trim() || null,
      posting_text: draft.posting_text.trim() || null,
    }).then(
      (job) => {
        setPending(false);
        void client.invalidateQueries({ queryKey: jobsKey });
        toast.success(
          job.fit === null
            ? "Posting added."
            : `Posting added, fit ${job.fit}.`,
        );
        onClose();
        void navigate(`/jobs/${job.id}`);
      },
      (failure: unknown) => {
        setPending(false);
        const duplicate = duplicateOf(failure);
        if (duplicate)
          setError({
            text: "This posting is already in your list.",
            existing: duplicate.id,
          });
        else setError({ text: `Not added. ${describeError(failure)}` });
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a posting</DialogTitle>
          <DialogDescription>
            For a posting from a site the app does not sweep. With its text, it
            is scored against your skills at once.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-company`}>Company</Label>
              <Input {...field("company")} required maxLength={1000} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-title`}>Title</Label>
              <Input {...field("title")} required maxLength={1000} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-location`}>Location</Label>
              <Input {...field("location")} maxLength={1000} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`${id}-contract_type`}>Contract type</Label>
              <NativeSelect {...field("contract_type")}>
                <option value="">Read from the title</option>
                {CONTRACT_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </NativeSelect>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-url`}>Link to the posting</Label>
            <Input
              {...field("url")}
              type="url"
              inputMode="url"
              placeholder="https://"
              maxLength={1000}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-posting_text`}>Posting text</Label>
            <Textarea
              {...field("posting_text")}
              rows={8}
              maxLength={1_000_000}
              className="max-h-72 text-sm"
              placeholder="Paste the job description here."
            />
          </div>
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{error.text}</p>
                {error.existing ? (
                  <Link
                    to={`/jobs/${error.existing}`}
                    className="underline underline-offset-4"
                    onClick={onClose}
                  >
                    Open the existing posting
                  </Link>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            disabled={pending || missing}
            aria-busy={pending}
          >
            {pending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            Add posting
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* --- page menu: rescore ---------------------------------------------------------------- */

export function PageMenu({ selected }: { selected: string[] }) {
  const [target, setTarget] = useState<"all" | "selected" | null>(null);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon-sm" aria-label="More actions">
            <EllipsisVertical aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => {
              setTarget("all");
            }}
          >
            <RefreshCw aria-hidden="true" />
            Rescore all postings
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={selected.length === 0}
            onSelect={() => {
              setTarget("selected");
            }}
          >
            <RefreshCw aria-hidden="true" />
            Rescore the selected postings
            {selected.length ? ` (${selected.length})` : ""}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {target ? (
        <RescoreDialog
          ids={target === "selected" ? selected : undefined}
          onClose={() => {
            setTarget(null);
          }}
        />
      ) : null}
    </>
  );
}

function RescoreDialog({
  ids,
  onClose,
}: {
  ids?: string[];
  onClose: () => void;
}) {
  const id = useId();
  const client = useQueryClient();
  const [overwrite, setOverwrite] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<RescoreResult | null>(null);
  const [error, setError] = useState("");
  const what = ids
    ? `the ${plural(ids.length, "selected posting")}`
    : "all postings";

  const run = () => {
    setRunning(true);
    setError("");
    rescoreAll({ ids, overwriteManual: overwrite }, setProgress).then(
      (result) => {
        setRunning(false);
        void client.invalidateQueries({ queryKey: jobsKey });
        toast.success(rescoreSummary(result));
        onClose();
      },
      (failure: unknown) => {
        setRunning(false);
        void client.invalidateQueries({ queryKey: jobsKey });
        setError(
          isApiError(failure) && failure.status === 409
            ? (errorDetail(failure) ?? "Rescoring is not possible now.")
            : `The rescore stopped. ${describeError(failure)}`,
        );
      },
    );
  };

  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !running) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Rescore {what}?</AlertDialogTitle>
          <AlertDialogDescription>
            Each posting with a text is scored again against the skills of your
            profile. Fits you set by hand are kept unless you choose otherwise.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex items-start gap-2">
          <input
            id={`${id}-overwrite`}
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={overwrite}
            disabled={running}
            onChange={(event) => {
              setOverwrite(event.target.checked);
            }}
          />
          <Label
            htmlFor={`${id}-overwrite`}
            className="leading-snug font-normal"
          >
            Also replace the fits I set by hand
          </Label>
        </div>
        {overwrite ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              Every fit you set by hand will be replaced by the computed one.
              This cannot be undone.
            </AlertDescription>
          </Alert>
        ) : null}
        {running ? (
          <p
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 text-sm"
          >
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            {progress
              ? `Rescoring: ${plural(progress.scored, "posting")} done${progress.remaining ? `, ${progress.remaining} to go` : ""}`
              : "Rescoring"}
          </p>
        ) : null}
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              <p>{error}</p>
              {error.includes("Profile") ? (
                <Link to="/profile" className="underline underline-offset-4">
                  Open your profile
                </Link>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={running}>Cancel</AlertDialogCancel>
          <Button
            variant={overwrite ? "destructive" : "default"}
            disabled={running}
            aria-busy={running}
            onClick={run}
          >
            {overwrite ? "Rescore and replace" : "Rescore"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
