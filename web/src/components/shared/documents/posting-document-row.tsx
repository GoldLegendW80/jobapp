import { Download, ExternalLink, Wand2 } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";

import {
  failureSentence,
  versionPage1Url,
  versionPdfUrl,
  type Kind,
  type WaitReason,
} from "@/features/documents/api/documents-api";
import { type PostingDocument } from "@/features/documents/api/posting-documents-api";
import {
  useFitPostingDocument,
  useWriteNow,
} from "@/features/documents/api/use-posting-documents";
import { type BaseCv } from "@/features/documents/api/base-cvs-api";
import {
  failedSentence,
  formatWhen,
  WAIT_SENTENCES,
} from "@/features/documents/helpers/labels";
import {
  cardState,
  type CardState,
} from "@/features/documents/helpers/posting-documents-state";

/** Width of the page 1 thumbnail of a row: sharp at twice its size on screen. */
const PREVIEW_WIDTH = 160;

/** Where each wait reason is settled, for the link under the sentence. */
const WAIT_LINKS: Record<WaitReason, { to: string; label: string } | null> = {
  consent: { to: "/profile#your-cv", label: "Open your CVs" },
  credential: { to: "/account", label: "Open your account" },
  base_cv: { to: "/profile#your-cv", label: "Open your CVs" },
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

/** One document of the posting, as a row: its page 1, its state, and what the person can do next. */
export function PostingDocumentRow({
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
  const selectId = useId();
  const state = cardState(document, fit, threshold);
  const latest = document?.latest ?? null;

  const write = useWriteNow(jobId);
  const writeFrom = ({ base, again }: { base?: string; again?: boolean }) => {
    write.mutate({
      // The letter follows a new CV (written after it, from it), as the automatic chain does.
      kinds:
        kind === "cv" && (again === true || letterWritten)
          ? ["cv", "letter"]
          : [kind],
      ...(base === undefined ? {} : { base_document_id: base }),
    });
  };
  const shorten = useFitPostingDocument(jobId);
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
      className="flex min-w-0 gap-4 py-5 first:pt-0 last:pb-0"
    >
      {document !== undefined && latest !== null && latest.has_preview ? (
        <img
          src={versionPage1Url(document.id, latest.number, PREVIEW_WIDTH)}
          alt={`Page 1 of the ${NOUNS[kind]}, version ${String(latest.number)}`}
          loading="lazy"
          className="h-auto w-16 shrink-0 self-start rounded-sm border bg-white"
        />
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">{TITLES[kind]}</h3>
          {latest !== null ? (
            <Badge variant="outline">Version {latest.number}</Badge>
          ) : null}
        </div>

        <StateLine state={state} kind={kind} hasVersion={latest !== null} />

        {kind === "cv" && cvs.length > 0 ? (
          <Field className="gap-1.5">
            <FieldLabel
              htmlFor={selectId}
              className="block leading-5 font-normal text-muted-foreground"
            >
              Based on:{" "}
              <span className="font-medium text-foreground">
                {baseName ??
                  (currentBase === undefined ? "" : "a CV you deleted")}
              </span>
            </FieldLabel>
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
          </Field>
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
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => {
                writeFrom({ base: kind === "cv" ? choice : undefined });
              }}
            >
              {write.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              {state.tag === "failed" ? "Try again" : "Write now"}
            </Button>
          ) : null}
          {rewrite ? (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || state.tag === "writing" || clickWouldWait}
              onClick={() => {
                writeFrom({ base: choice, again: true });
              }}
            >
              Write it again from this CV
            </Button>
          ) : null}
          {state.tag === "overflow" &&
          kind === "cv" &&
          document !== undefined ? (
            <Button
              size="sm"
              variant="outline"
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
      </div>
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
          <Spinner aria-hidden="true" role={undefined} />
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
          <Spinner aria-hidden="true" role={undefined} />
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
              : "Longer than one page. Open it to shorten the longest texts, or choose a more compact template."}
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
