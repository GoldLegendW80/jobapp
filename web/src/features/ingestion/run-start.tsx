import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Play } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type RefObject,
} from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { errorDetail, isApiError } from "@/lib/api";

import {
  documentsKey,
  listDocuments,
  loadFailureSentence,
  type IngestionDocument,
} from "./documents-api";
import { kindLabel, sizeLabel, statusView } from "./labels";
import {
  acceptConsent,
  actionFailure,
  consentKey,
  getConsent,
  getEstimate,
  IDLE_POLL_MS,
  isActive,
  listRuns,
  runKey,
  refreshRuns,
  runsKey,
  startRefusal,
  startRun,
  tokens,
  type Consent,
  type Estimate,
  type Run,
  type RunList,
  type Selection,
} from "./runs-api";

/** The heading of the run on the page (run-progress.tsx): it takes the focus after a start. */
export const RUN_HEADING_ID = "run-heading";

const CHECKBOX = "mt-0.5 size-4 shrink-0 accent-primary";

/**
 * Step 3, first part: choosing the documents and starting a run. Nothing is sent to a model
 * from here until the user has read the estimate in the dialog and clicked Start. While a run
 * is active this part steps aside: the page shows that run (run-progress.tsx).
 */
export function RunStart() {
  const documents = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
  });
  const runs = useQuery({
    queryKey: runsKey,
    queryFn: ({ signal }) => listRuns(signal),
    refetchInterval: IDLE_POLL_MS,
  });
  /** The documents ticked, in the order they were ticked: the run reads them in that order. */
  const [chosen, setChosen] = useState<readonly string[]>([]);
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);

  if (runs.isPending || documents.isPending)
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading
      </p>
    );
  // Nothing to show yet: a background read that fails keeps this part and its dialog.
  if (runs.data === undefined || documents.data === undefined)
    return (
      <Alert variant="destructive">
        <AlertTitle>This step could not be loaded</AlertTitle>
        <AlertDescription>
          {loadFailureSentence(runs.error ?? documents.error)}
        </AlertDescription>
      </Alert>
    );
  // One run at a time: the active one is on the page, and it has the buttons.
  if (runs.data.active_run_id !== null) return null;

  const items = documents.data.items;
  const selected = chosen.filter((id) =>
    items.some((one) => one.id === id && canBeChosen(one)),
  );
  const selectable = items.filter(canBeChosen);
  const toggle = (id: string, on: boolean) => {
    setChosen((old) => {
      const others = old.filter((one) => one !== id);
      return on ? [...others, id] : others;
    });
  };

  return (
    <section aria-labelledby="start-heading" className="flex flex-col gap-3">
      <h3 id="start-heading" className="text-base font-semibold tracking-tight">
        {runs.data.runs.length > 0 ? "Start another run" : "Start a run"}
      </h3>
      <p className="text-sm text-muted-foreground">
        Choose the documents the agent reads, in the order you want them read.
        Before anything is sent, you see what it would cost in tokens and you
        decide.
      </p>
      {runs.isError || documents.isError ? (
        <p role="status" className="text-sm text-muted-foreground">
          The server could not be reached just now. This page keeps trying.
        </p>
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm">
          You have no document yet. Add some in the Documents step first.
        </p>
      ) : (
        <>
          <fieldset className="flex flex-col gap-2">
            <legend className="sr-only">Documents to read</legend>
            <ul className="flex flex-col divide-y rounded-lg border">
              {items.map((one) => (
                <li key={one.id} className="min-w-0 px-3 py-2">
                  <label className="flex min-w-0 items-start gap-3 text-sm">
                    <input
                      type="checkbox"
                      className={CHECKBOX}
                      checked={selected.includes(one.id)}
                      disabled={!canBeChosen(one)}
                      onChange={(event) => {
                        toggle(one.id, event.target.checked);
                      }}
                    />
                    <span className="flex min-w-0 flex-col">
                      <span className="font-medium break-words">
                        {one.name}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {kindLabel(one)}, {sizeLabel(one.size)}
                        {canBeChosen(one)
                          ? null
                          : `. ${statusView(one).label}: it cannot be read yet`}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              ref={opener}
              disabled={selected.length === 0}
              onClick={() => {
                setOpen(true);
              }}
            >
              <Play aria-hidden="true" />
              See the estimate
            </Button>
            {selectable.length > 1 ? (
              <Button
                variant="outline"
                onClick={() => {
                  setChosen(
                    selected.length === selectable.length
                      ? []
                      : selectable.map((one) => one.id),
                  );
                }}
              >
                {selected.length === selectable.length
                  ? "Clear the selection"
                  : "Select all"}
              </Button>
            ) : null}
            <span
              className="text-xs text-muted-foreground tabular-nums"
              role="status"
            >
              {selected.length} of {items.length} selected
            </span>
          </div>
        </>
      )}
      {open ? (
        <StartDialog
          documentIds={selected}
          opener={opener}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </section>
  );
}

/** The server's sentence for a selection it refuses (over a limit of a run): what to change. */
function selectionRefusal(error: unknown): string | undefined {
  return isApiError(error) && error.kind === "http" && error.status === 422
    ? errorDetail(error)
    : undefined;
}

/**
 * A document the run can read now: its text is there, or its page images wait for the agent,
 * which transcribes them in the first steps of the run. The estimate has the last word on it.
 */
function canBeChosen(document: IngestionDocument): boolean {
  return (
    document.readable &&
    (document.status === "done" || document.status === "awaiting_transcription")
  );
}

/**
 * The start dialog: the estimate exactly as the server gave it, the consent text the first
 * time, and one button that starts the run with the `quote_id` of the estimate on screen. A
 * start the server refuses because the figures changed replaces what is shown and waits for
 * another click.
 */
export function StartDialog({
  documentIds,
  opener,
  onClose,
}: {
  documentIds: string[];
  /** The button that opened the dialog: it takes the focus back when nothing was started. */
  opener: RefObject<HTMLButtonElement>;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const boxId = useId();
  const [reread, setReread] = useState<readonly string[]>([]);
  /** The estimate on screen, with the selection it was asked for. */
  const [shown, setShown] = useState<{
    estimate: Estimate;
    selection: Selection;
  }>();
  const [notice, setNotice] = useState<string>();
  const [failure, setFailure] = useState<string>();
  /** Reasons the start itself gave for documents, by id. */
  const [refused, setRefused] = useState<Record<string, string>>({});
  /** The version of the consent text the box was ticked for, never a bare yes. */
  const [tickedVersion, setTickedVersion] = useState<number | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
    staleTime: 0,
    gcTime: 0,
  });

  const ask = useMutation({
    mutationFn: (selection: Selection) =>
      getEstimate(selection).then((estimate) => ({ estimate, selection })),
    onSuccess: (answer) => {
      setShown(answer);
      setFailure(undefined);
    },
    onError: (error) => {
      setFailure(
        selectionRefusal(error) ??
          actionFailure(
            error,
            "The estimate could not be computed. Try again in a moment.",
          ),
      );
    },
  });
  const askEstimate = ask.mutate;
  const key = documentIds.join(",");
  useEffect(() => {
    askEstimate({
      document_ids: key === "" ? [] : key.split(","),
      reread_document_ids: [...reread],
    });
  }, [askEstimate, key, reread]);

  const say = useCallback((sentence: string) => {
    setNotice(sentence);
    // The notice takes the focus so a screen reader hears it and the next Tab is in the dialog.
    window.requestAnimationFrame(() => noticeRef.current?.focus());
  }, []);

  const start = useMutation({
    mutationFn: async (current: {
      estimate: Estimate;
      selection: Selection;
      consent: Consent;
    }): Promise<Run> => {
      if (!current.consent.accepted)
        client.setQueryData(
          consentKey,
          await acceptConsent(current.consent.version),
        );
      return startRun(current.selection, current.estimate.quote_id);
    },
    onSuccess: (run) => {
      started.current = true;
      client.setQueryData(runKey(run.id), run);
      client.setQueryData<RunList>(runsKey, (old) => ({
        runs: [
          {
            id: run.id,
            status: run.status,
            partial: run.partial,
            created_at: run.created_at,
            finished_at: run.finished_at,
          },
          ...(old?.runs ?? []).filter((one) => one.id !== run.id),
        ],
        active_run_id: isActive(run.status) ? run.id : null,
      }));
      refreshRuns(client);
      onClose();
    },
    onError: (error, current) => {
      const refusal = startRefusal(error);
      setFailure(undefined);
      setRefused({});
      switch (refusal.kind) {
        case "estimate_changed":
          // The new figures are on screen; nothing starts until the next click.
          setShown({
            estimate: refusal.estimate,
            selection: current.selection,
          });
          say(
            "The estimate has changed since you opened it. Nothing was started. Read the new figures, then start again.",
          );
          void consent.refetch();
          break;
        case "consent_required":
          setTickedVersion(null);
          say(refusal.sentence);
          void consent.refetch();
          break;
        case "active_run":
          toast.info("A run is already in progress. This page shows it.");
          void client.invalidateQueries({ queryKey: runsKey });
          onClose();
          break;
        case "unreadable_documents":
          setRefused(
            Object.fromEntries(
              refusal.documents.map((one) => [one.id, one.reason]),
            ),
          );
          say(refusal.sentence);
          askEstimate(current.selection);
          break;
        case "other":
          if (!current.consent.accepted) void consent.refetch();
          say(refusal.sentence);
          break;
      }
    },
  });

  const estimate = shown?.estimate;
  const text = consent.data;
  const ticked = text !== undefined && tickedVersion === text.version;
  /** A text other than the one the box was ticked for: the tick no longer counts. */
  const changed =
    text !== undefined &&
    tickedVersion !== null &&
    tickedVersion !== text.version;
  const agreed = text !== undefined && (text.accepted || ticked);
  const ready =
    estimate !== undefined &&
    shown !== undefined &&
    text !== undefined &&
    estimate.can_start &&
    !ask.isPending;

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !start.isPending) onClose();
      }}
    >
      <DialogContent
        className="max-h-[90dvh] overflow-y-auto sm:max-w-xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (!started.current) {
            opener.current?.focus();
            return;
          }
          // The button that opened the dialog is gone: the run on the page takes the focus.
          window.requestAnimationFrame(() =>
            document.getElementById(RUN_HEADING_ID)?.focus(),
          );
        }}
      >
        <DialogHeader>
          <DialogTitle>Before the run starts</DialogTitle>
          <DialogDescription>
            This is what the agent would read and what it is expected to use.
            Nothing is sent until you start the run.
          </DialogDescription>
        </DialogHeader>

        <div
          ref={noticeRef}
          tabIndex={-1}
          className="outline-none empty:hidden"
        >
          {notice ? (
            <Alert>
              <AlertTitle>Read this first</AlertTitle>
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          ) : null}
        </div>
        {failure ? (
          <Alert variant="destructive">
            <AlertDescription>{failure}</AlertDescription>
          </Alert>
        ) : null}

        {estimate === undefined ? (
          failure ? null : (
            <p
              role="status"
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              <LoaderCircle
                aria-hidden="true"
                className="size-4 animate-spin"
              />
              Computing the estimate
            </p>
          )
        ) : (
          <EstimateView
            estimate={estimate}
            reread={reread}
            refused={refused}
            busy={ask.isPending || start.isPending}
            onReread={(id, on) => {
              setReread((old) =>
                on ? [...old, id].sort() : old.filter((one) => one !== id),
              );
            }}
          />
        )}

        {text === undefined ? (
          consent.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {loadFailureSentence(consent.error)}
              </AlertDescription>
            </Alert>
          ) : null
        ) : text.accepted ? (
          <p className="text-sm text-muted-foreground" data-testid="reminder">
            {text.reminder}
          </p>
        ) : (
          <section
            aria-labelledby={`${boxId}-heading`}
            className="flex flex-col gap-2 rounded-lg border p-3"
          >
            <h4 id={`${boxId}-heading`} className="text-sm font-semibold">
              What is sent, and to whom
            </h4>
            <p className="text-sm" data-testid="consent-text">
              {text.text}
            </p>
            {changed ? (
              <p className="text-sm font-medium" data-testid="consent-changed">
                The text changed after you ticked the box. Read it again and
                tick the box if you agree.
              </p>
            ) : null}
            <label
              htmlFor={boxId}
              className="flex items-start gap-3 text-sm font-medium"
            >
              <input
                id={boxId}
                type="checkbox"
                className={CHECKBOX}
                checked={ticked}
                disabled={start.isPending}
                onChange={(event) => {
                  setTickedVersion(event.target.checked ? text.version : null);
                }}
              />
              I have read this and I agree to send the selected documents
            </label>
          </section>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            disabled={start.isPending}
            onClick={onClose}
          >
            Not now
          </Button>
          <Button
            disabled={!ready || !agreed || start.isPending}
            onClick={() => {
              if (!ready) return;
              start.mutate({
                estimate,
                selection: shown.selection,
                consent: text,
              });
            }}
          >
            {start.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : (
              <Play aria-hidden="true" />
            )}
            Start the run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const count = (value: number, one: string, many: string) =>
  `${tokens(value)} ${value === 1 ? one : many}`;

/** The figures of one estimate, each one as the server gave it. */
function EstimateView({
  estimate,
  reread,
  refused,
  busy,
  onReread,
}: {
  estimate: Estimate;
  reread: readonly string[];
  refused: Record<string, string>;
  busy: boolean;
  onReread: (id: string, on: boolean) => void;
}) {
  const { cost, limits } = estimate;
  return (
    <div className="flex flex-col gap-4 text-sm" data-testid="estimate">
      {estimate.blockers.length > 0 ? (
        <Alert variant="destructive">
          <AlertTitle>The run cannot start yet</AlertTitle>
          <AlertDescription>
            <ul className="flex flex-col gap-1">
              {estimate.blockers.map((one) => (
                <li key={one.code}>
                  {one.reason}{" "}
                  {one.code === "no_credential" ? (
                    <Link to="/account" className="font-medium underline">
                      Open your account page
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <section aria-label="What is read" className="flex flex-col gap-2">
        <h4 className="font-semibold">What is read</h4>
        <ul className="flex flex-col divide-y rounded-lg border">
          {estimate.documents.map((one) => {
            const again = reread.includes(one.id);
            return (
              <li key={one.id} className="flex min-w-0 flex-col gap-1 p-3">
                <span className="font-medium break-words">{one.name}</span>
                <span className="text-xs text-muted-foreground">
                  {count(one.characters, "character", "characters")}
                  {one.images > 0
                    ? `, ${count(one.images, "image", "images")}`
                    : null}
                  , {count(one.steps, "step", "steps")}
                  {one.reused_steps > 0
                    ? `, ${tokens(one.reused_steps)} already read and reused`
                    : null}
                </span>
                {refused[one.id] ? (
                  <span className="text-xs text-destructive">
                    {refused[one.id]}
                  </span>
                ) : null}
                {one.reused_steps > 0 || again ? (
                  <label className="flex items-start gap-2 text-xs">
                    <input
                      type="checkbox"
                      className={CHECKBOX}
                      checked={again}
                      disabled={busy}
                      onChange={(event) => {
                        onReread(one.id, event.target.checked);
                      }}
                    />
                    <span>
                      Read {one.name} again
                      <span className="block text-muted-foreground">
                        Reading it again uses tokens again.
                      </span>
                    </span>
                  </label>
                ) : null}
              </li>
            );
          })}
          {estimate.unreadable.map((one) => (
            <li key={one.id} className="flex min-w-0 flex-col gap-1 p-3">
              <span className="font-medium break-words">{one.name}</span>
              <span className="text-xs text-destructive">
                Not read: {refused[one.id] ?? one.reason}
              </span>
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground">
          In all: {count(estimate.documents.length, "document", "documents")},{" "}
          {count(estimate.characters, "character", "characters")},{" "}
          {count(estimate.images, "image", "images")},{" "}
          {count(estimate.steps, "step", "steps")}
          {estimate.reused_steps > 0
            ? `, of which ${tokens(estimate.reused_steps)} already read and reused`
            : null}
          .
        </p>
      </section>

      <section aria-label="Tokens" className="flex flex-col gap-1">
        <h4 className="font-semibold">Tokens</h4>
        <p data-testid="estimate-range">
          About {tokens(estimate.tokens.low)} to {tokens(estimate.tokens.high)}{" "}
          tokens.
        </p>
        <p data-testid="estimate-ceiling">
          The run pauses at {tokens(estimate.tokens.ceiling)} tokens, and asks
          you before going on.
        </p>
        {estimate.model ? (
          <p className="text-muted-foreground" data-testid="estimate-model">
            {estimate.model_reason}
          </p>
        ) : null}
      </section>

      {cost ? (
        <section aria-label="Estimated cost" className="flex flex-col gap-1">
          <h4 className="font-semibold">Estimated cost</h4>
          <p data-testid="estimate-cost">
            About {cost.low} to {cost.high} {cost.currency}, as an estimate.
          </p>
          <p data-testid="estimate-cost-ceiling">
            The pause at {tokens(estimate.tokens.ceiling)} tokens is estimated
            at about {cost.ceiling} {cost.currency}. It is an estimate, not a
            limit on what you pay.
          </p>
          <p className="text-muted-foreground">{cost.note}</p>
        </section>
      ) : estimate.cost_note ? (
        <p data-testid="estimate-quota">{estimate.cost_note}</p>
      ) : null}

      <p className="text-xs text-muted-foreground">
        Limits of a run: {tokens(limits.documents)} documents,{" "}
        {tokens(limits.characters)} characters, {tokens(limits.images)} images,{" "}
        {tokens(limits.steps)} steps, {tokens(limits.step_minutes)} minutes for
        a step and {tokens(limits.run_minutes)} minutes in all.
      </p>
    </div>
  );
}
