import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, LoaderCircle, Wand2 } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";

import {
  failureSentence,
  fitDocument,
  postingDocumentsKey,
  versionPage1Url,
  versionPdfUrl,
  writeNow,
  type BaseCv,
  type Kind,
  type PostingDocument,
  type WaitReason,
} from "./documents-api";
import { failedSentence, formatWhen, WAIT_SENTENCES } from "./labels";
import { cardState, type CardState } from "./posting-documents-state";

/** Width of the page 1 image on a card: sharp on a phone, light on a laptop. */
const PREVIEW_WIDTH = 480;

/** Where each wait reason is settled, for the link under the sentence. */
const WAIT_LINKS: Record<WaitReason, { to: string; label: string } | null> = {
  consent: { to: "/profile/cv", label: "Open the CV tab" },
  credential: { to: "/account", label: "Open your account" },
  base_cv: { to: "/profile/cv", label: "Open the CV tab" },
  daily_ceiling: null,
};

const TITLES: Record<Kind, string> = { cv: "CV", letter: "Letter" };

/** The document inside a sentence: "the CV", "the letter". */
const NOUNS: Record<Kind, string> = { cv: "CV", letter: "letter" };

/** Waits of an automatic generation only, which a click replaces; on any other a click would wait too. */
const CLICK_REPLACES: ReadonlySet<WaitReason> = new Set([
  "daily_ceiling",
  "base_cv",
]);

/** One document of the posting: its state, its page 1, and what the person can do next. */
export function PostingDocumentCard({
  jobId,
  kind,
  document,
  fit,
  threshold,
  cvs,
  choice,
  onChoose,
  cvReady,
  letterWritten,
}: {
  jobId: string;
  kind: Kind;
  document: PostingDocument | undefined;
  fit: number | null;
  threshold: { auto: boolean; min: number } | undefined;
  /** The base CVs, for the CV card's "Based on". */
  cvs: BaseCv[];
  /** The base CV the person picked on the CV card, if any. */
  choice: string | undefined;
  onChoose: (id: string) => void;
  /** The posting's CV has a version: the letter card offers its button only then, because a letter is written from the CV. */
  cvReady: boolean;
  /** The posting's letter has a version: a CV written again takes the letter along. */
  letterWritten: boolean;
}) {
  const queryClient = useQueryClient();
  const selectId = useId();
  const state = cardState(document, fit, threshold);
  const latest = document?.latest ?? null;
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: postingDocumentsKey(jobId) });

  const write = useMutation({
    mutationFn: ({ base, again }: { base?: string; again?: boolean }) =>
      writeNow(jobId, {
        // The letter follows a new CV (written after it, from it), as the automatic chain does.
        kinds:
          kind === "cv" && (again === true || letterWritten)
            ? ["cv", "letter"]
            : [kind],
        ...(base === undefined ? {} : { base_document_id: base }),
      }),
    onSettled: refresh,
  });
  const shorten = useMutation({
    mutationFn: (id: string) => fitDocument(id),
    onSettled: refresh,
  });
  const busy = write.isPending || shorten.isPending;
  const failure = write.isError
    ? failureSentence(write.error)
    : shorten.isError
      ? failureSentence(shorten.error)
      : undefined;

  const defaultCv = cvs.find((one) => one.is_default) ?? cvs[0];
  const currentBase = document?.base_document_id ?? defaultCv?.id;
  const selected = choice ?? currentBase ?? "";
  const baseName = cvs.find((one) => one.id === currentBase)?.name;
  const rewrite =
    kind === "cv" &&
    latest !== null &&
    choice !== undefined &&
    choice !== currentBase;
  const clickWouldWait =
    state.tag === "waiting" &&
    (state.reason === null || !CLICK_REPLACES.has(state.reason));
  const canWrite =
    (kind === "cv" || cvReady) &&
    (state.tag === "empty" ||
      state.tag === "under" ||
      state.tag === "manual" ||
      (state.tag === "waiting" && !clickWouldWait) ||
      state.tag === "failed");

  return (
    <article
      aria-label={TITLES[kind]}
      data-testid={`posting-${kind}`}
      className="flex min-w-0 flex-col gap-3 rounded-lg border p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold">{TITLES[kind]}</h3>
        {latest !== null ? (
          <Badge variant="outline">Version {latest.number}</Badge>
        ) : null}
      </div>

      <StateLine state={state} kind={kind} hasVersion={latest !== null} />

      {document !== undefined && latest !== null && latest.has_pdf ? (
        <img
          src={versionPage1Url(document.id, latest.number, PREVIEW_WIDTH)}
          alt={`Page 1 of the ${NOUNS[kind]}, version ${String(latest.number)}`}
          loading="lazy"
          className="w-full max-w-64 rounded border bg-white"
        />
      ) : null}

      {kind === "cv" && cvs.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={selectId} className="text-sm text-muted-foreground">
            Based on:{" "}
            <span className="font-medium text-foreground">
              {baseName ??
                (currentBase === undefined ? "" : "a CV you deleted")}
            </span>
          </label>
          <NativeSelect
            id={selectId}
            value={selected}
            onChange={(event) => {
              onChoose(event.target.value);
            }}
          >
            {cvs.map((one) => (
              <option key={one.id} value={one.id}>
                {one.name}
              </option>
            ))}
          </NativeSelect>
        </div>
      ) : null}

      {failure !== undefined ? (
        <Alert variant="destructive">
          <AlertDescription>{failure}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {document !== undefined && latest !== null ? (
          <Button asChild variant="outline" size="sm">
            <Link to={`/jobs/${jobId}/documents/${kind}`}>
              <ExternalLink aria-hidden="true" />
              Open <span className="sr-only">the {NOUNS[kind]}</span>
            </Link>
          </Button>
        ) : null}
        {document !== undefined && latest !== null && latest.has_pdf ? (
          <Button asChild variant="outline" size="sm">
            <a
              href={versionPdfUrl(document.id, latest.number)}
              download
              aria-label={`Download PDF of the ${NOUNS[kind]}`}
            >
              <Download aria-hidden="true" />
              Download PDF
            </a>
          </Button>
        ) : null}
        {canWrite ? (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              write.mutate({ base: kind === "cv" ? choice : undefined });
            }}
          >
            {write.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            {state.tag === "failed" ? "Try again" : "Write now"}
          </Button>
        ) : null}
        {rewrite ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy || state.tag === "writing" || clickWouldWait}
            onClick={() => {
              write.mutate({ base: choice, again: true });
            }}
          >
            Write it again from this CV
          </Button>
        ) : null}
        {state.tag === "overflow" && kind === "cv" && document !== undefined ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => {
              shorten.mutate(document.id);
            }}
          >
            <Wand2 aria-hidden="true" />
            Make it fit
          </Button>
        ) : null}
      </div>
      {rewrite ? (
        <p className="text-sm text-muted-foreground">
          The letter is then written again from the new CV.
        </p>
      ) : null}
    </article>
  );
}

function StateLine({
  state,
  kind,
  hasVersion,
}: {
  state: CardState;
  kind: Kind;
  hasVersion: boolean;
}) {
  const noun = NOUNS[kind];
  switch (state.tag) {
    case "waiting": {
      const link = state.reason ? WAIT_LINKS[state.reason] : null;
      return (
        <div className="flex flex-col gap-1 text-sm" data-testid="card-state">
          <p>
            {state.reason
              ? WAIT_SENTENCES[state.reason]
              : "The writer will start shortly."}
          </p>
          {link ? (
            <Link to={link.to} className="w-fit underline underline-offset-4">
              {link.label}
            </Link>
          ) : null}
        </div>
      );
    }
    case "writing":
      return (
        <p
          role="status"
          data-testid="card-state"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          {hasVersion ? "Writing a new version" : `Writing the ${noun}`}
        </p>
      );
    case "failed":
      return (
        <Alert variant="destructive" data-testid="card-state">
          <AlertDescription>
            Writing failed. {failedSentence(state.error, kind)}
          </AlertDescription>
        </Alert>
      );
    case "drawing":
      return (
        <p
          role="status"
          data-testid="card-state"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Drawing the page
        </p>
      );
    case "sent":
      return (
        <p className="text-sm" data-testid="card-state">
          Sent on {formatWhen(state.at)}
        </p>
      );
    case "overflow":
      return (
        <Alert variant="destructive" data-testid="card-state">
          <AlertDescription>
            {kind === "cv"
              ? "Longer than one page. The writer can shorten it to fit."
              : "Longer than one page. Shorten the longest fields in Edit, or choose a more compact template."}
          </AlertDescription>
        </Alert>
      );
    case "ready":
      return (
        <p className="text-sm" data-testid="card-state">
          Ready, on one page
        </p>
      );
    case "unrendered":
      return (
        <p className="text-sm text-muted-foreground" data-testid="card-state">
          {state.message ?? "Preview not available right now"}
        </p>
      );
    case "under":
      return (
        <p className="text-sm text-muted-foreground" data-testid="card-state">
          Fit {state.fit} is under your threshold of {state.min}
        </p>
      );
    case "manual":
      return (
        <p className="text-sm text-muted-foreground" data-testid="card-state">
          Documents are not written automatically. Write it when you want it.
        </p>
      );
    case "empty":
      return (
        <p className="text-sm text-muted-foreground" data-testid="card-state">
          Not written yet
        </p>
      );
  }
}
