/**
 * The editable parts of /jobs/:id: status, notes (autosaved), fit set by hand, and the details
 * dialog. Every change goes through `useJobEditor` (use-job-editor.ts): optimistic, one request
 * at a time, rolled back when the api refuses.
 */
import { CircleAlert, LoaderCircle, Pencil, RotateCcw } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link } from "react-router";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { describeError, errorDetail } from "@/lib/api";

import { duplicateOf, isStale, type JobDetail, type JobPatch } from "./api";
import { CONTRACT_TYPES, STATUSES, statusLabel } from "./labels";
import {
  failure,
  NOTES_DEBOUNCE_MS,
  NOTES_MAX,
  STALE_MESSAGE,
  type Save,
} from "./use-job-editor";

/* --- status ---------------------------------------------------------------------------- */

export function StatusControl({ job, save }: { job: JobDetail; save: Save }) {
  const id = useId();
  const [confirming, setConfirming] = useState(false);

  const apply = (status: string) => {
    save({ status }, { status }).then(
      () => {
        toast.success(`Status set to ${statusLabel(status)}.`);
      },
      (error: unknown) => {
        toast.error(failure(error));
      },
    );
  };

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Status</Label>
      <NativeSelect
        id={id}
        value={job.status}
        className="sm:w-48"
        onChange={(event) => {
          const next = event.target.value;
          if (next === job.status) return;
          if (next === "applied") setConfirming(true);
          else apply(next);
        }}
      >
        {!(STATUSES as readonly string[]).includes(job.status) ? (
          <option value={job.status}>{statusLabel(job.status)}</option>
        ) : null}
        {STATUSES.map((status) => (
          <option key={status} value={status}>
            {statusLabel(status)}
          </option>
        ))}
      </NativeSelect>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Mark this posting as applied?</AlertDialogTitle>
            <AlertDialogDescription>
              {job.applied_on
                ? "The applied date stays as it is."
                : "Today becomes its applied date."}{" "}
              The files of an applied posting are what the company received:
              they are never regenerated.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              onClick={() => {
                setConfirming(false);
                apply("applied");
              }}
            >
              Mark as applied
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/* --- notes ----------------------------------------------------------------------------- */

type NotesState = "idle" | "dirty" | "saving" | "saved" | "error";

export function NotesEditor({ job, save }: { job: JobDetail; save: Save }) {
  const id = useId();
  const [draft, setDraft] = useState(job.notes);
  const [state, setState] = useState<NotesState>("idle");
  const [error, setError] = useState("");
  // The text the server holds as far as this editor knows.
  const saved = useRef(job.notes);
  const inFlight = useRef<string | null>(null);
  const latest = useRef(draft);
  useEffect(() => {
    latest.current = draft;
  });

  // A change from elsewhere (a bulk note, a reload) replaces a draft the user has not touched.
  const [seen, setSeen] = useState(job.notes);
  if (job.notes !== seen) {
    setSeen(job.notes);
    if (draft === seen) setDraft(job.notes);
  }

  const commit = useCallback(
    (text: string) => {
      if (text === saved.current || text === inFlight.current) return;
      if (text.length > NOTES_MAX) return;
      inFlight.current = text;
      setState("saving");
      save({ notes: text }).then(
        (result) => {
          saved.current = result.notes;
          latest.current =
            text === latest.current ? result.notes : latest.current;
          inFlight.current = null;
          setState(latest.current === result.notes ? "saved" : "dirty");
        },
        (failed: unknown) => {
          inFlight.current = null;
          setError(failure(failed));
          setState("error");
        },
      );
    },
    [save],
  );

  useEffect(() => {
    if (draft === saved.current || state === "error") return;
    const timer = setTimeout(() => {
      commit(draft);
    }, NOTES_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [draft, commit, state]);

  const tooLong = draft.length > NOTES_MAX;
  const label = {
    idle: "",
    dirty: "Unsaved changes",
    saving: "Saving",
    saved: "Saved",
    error: "",
  }[state];

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id={`${id}-title`} className="text-lg font-semibold tracking-tight">
          Notes
        </h2>
        <p
          role="status"
          aria-live="polite"
          className="flex items-center gap-1 text-xs text-muted-foreground"
          data-testid="notes-status"
        >
          {state === "saving" ? (
            <LoaderCircle aria-hidden="true" className="size-3 animate-spin" />
          ) : null}
          {label}
        </p>
      </div>
      <Label htmlFor={id} className="sr-only">
        Notes
      </Label>
      <Textarea
        id={id}
        value={draft}
        rows={5}
        placeholder="What you learnt, who you talked to, what comes next."
        aria-invalid={tooLong || state === "error"}
        aria-describedby={`${id}-help`}
        onChange={(event) => {
          setDraft(event.target.value);
          setState("dirty");
          setError("");
        }}
        onBlur={() => {
          if (!tooLong) commit(draft);
        }}
        className="min-h-28 text-sm"
      />
      <p id={`${id}-help`} className="text-xs text-muted-foreground">
        {tooLong
          ? `Too long: ${draft.length.toLocaleString("en-GB")} characters, at most ${NOTES_MAX.toLocaleString("en-GB")}.`
          : "Saved automatically as you type."}
      </p>
      {state === "error" ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-wrap items-center gap-3">
            <span>{error}</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setState("dirty");
                commit(draft);
              }}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
    </section>
  );
}

/* --- fit set by hand ------------------------------------------------------------------- */

export function FitControl({ job, save }: { job: JobDetail; save: Save }) {
  const id = useId();
  const [value, setValue] = useState(job.fit === null ? "" : String(job.fit));
  const [shown, setShown] = useState(job.fit);
  if (job.fit !== shown) {
    setShown(job.fit);
    setValue(job.fit === null ? "" : String(job.fit));
  }
  const number = Number(value);
  const valid =
    value.trim() !== "" &&
    Number.isInteger(number) &&
    number >= 0 &&
    number <= 100;
  const unchanged = valid && number === job.fit && job.fit_overridden;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid || unchanged) return;
    save({ fit: number }, { fit: number, fit_overridden: true }).then(
      () => {
        toast.success(`Fit set by hand to ${number}.`);
      },
      (error: unknown) => {
        toast.error(failure(error));
      },
    );
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Fit</Label>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          min={0}
          max={100}
          step={1}
          value={value}
          aria-invalid={value !== "" && !valid}
          aria-describedby={`${id}-help`}
          onChange={(event) => {
            setValue(event.target.value);
          }}
          className="w-24 tabular-nums"
        />
        <Button
          type="submit"
          variant="outline"
          size="sm"
          disabled={!valid || unchanged}
        >
          Set by hand
        </Button>
        {job.fit_overridden ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              save({ fit: null }, { fit_overridden: false }).then(
                (result) => {
                  toast.success(
                    result.fit === null
                      ? "The fit is computed again."
                      : `The fit is computed again: ${result.fit}.`,
                  );
                },
                (error: unknown) => {
                  toast.error(
                    isStale(error)
                      ? STALE_MESSAGE
                      : `Not saved. ${errorDetail(error)?.replace(/^fit: /, "") ?? describeError(error)}`,
                  );
                },
              );
            }}
          >
            <RotateCcw aria-hidden="true" />
            Use computed
          </Button>
        ) : null}
      </div>
      <p id={`${id}-help`} className="text-xs text-muted-foreground">
        {job.fit_overridden
          ? "Set by hand: a rescore keeps it."
          : "Computed from the posting and your skills. A fit set by hand is kept by every rescore."}
      </p>
    </form>
  );
}

/* --- details --------------------------------------------------------------------------- */

interface Details {
  company: string;
  title: string;
  location: string;
  contract_type: string;
  url: string;
}

function detailsOf(job: JobDetail): Details {
  return {
    company: job.company,
    title: job.title,
    location: job.location,
    contract_type: job.contract_type,
    url: job.url ?? "",
  };
}

export function EditDetailsButton({
  job,
  save,
}: {
  job: JobDetail;
  save: Save;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Pencil aria-hidden="true" />
        Edit details
      </Button>
      {open ? (
        <EditDetailsDialog
          job={job}
          save={save}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

function EditDetailsDialog({
  job,
  save,
  onClose,
}: {
  job: JobDetail;
  save: Save;
  onClose: () => void;
}) {
  const id = useId();
  const [values, setValues] = useState<Details>(() => detailsOf(job));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{
    text: string;
    duplicate?: string;
  } | null>(null);
  const initial = detailsOf(job);
  const missing = !values.company.trim() || !values.title.trim();

  const field = (name: keyof Details) => ({
    id: `${id}-${name}`,
    value: values[name],
    onChange: (event: { target: { value: string } }) => {
      setValues((old) => ({ ...old, [name]: event.target.value }));
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (missing) return;
    const patch: JobPatch = {};
    for (const name of Object.keys(values) as Array<keyof Details>) {
      if (values[name].trim() === initial[name].trim()) continue;
      if (name === "url") patch.url = values.url.trim() || null;
      else patch[name] = values[name].trim();
    }
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    setPending(true);
    setError(null);
    save(patch).then(
      () => {
        setPending(false);
        toast.success("Details saved.");
        onClose();
      },
      (failed: unknown) => {
        setPending(false);
        const duplicate = duplicateOf(failed);
        if (duplicate)
          setError({
            text: "Another posting in your list has these details.",
            duplicate: duplicate.id,
          });
        else setError({ text: failure(failed) });
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit details</DialogTitle>
          <DialogDescription>
            Company, title, location, contract type and link of this posting.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-3">
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
              {CONTRACT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </NativeSelect>
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
          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>
                <p>{error.text}</p>
                {error.duplicate ? (
                  <Link
                    to={`/jobs/${error.duplicate}`}
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
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={onClose}
          >
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
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
