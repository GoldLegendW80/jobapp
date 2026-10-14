import { useQuery } from "@tanstack/react-query";
import { Download, ExternalLink, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { PanelProps } from "./document-view";
import {
  correctionSchema,
  getVersion,
  listVersions,
  versionKey,
  versionPage1Url,
  versionPdfUrl,
  versionsKey,
  type Correction,
  type Kind,
  type VersionSummary,
} from "./documents-api";
import {
  failedSentence,
  fieldLabel,
  fitLabel,
  formatWhen,
  isOpen,
  ORIGIN_LABELS,
  templateLabel,
  WAIT_SENTENCES,
} from "./labels";

/** Width of the page image asked for: sharp on a phone held upright and on a laptop. */
const PREVIEW_WIDTH = 900;

/** The See view: the page, whether it fits one page, the corrections and the version history. */
export function SeePanel({ document, generation }: PanelProps) {
  const [chosen, setChosen] = useState<number>();
  const latestNumber = document.latest?.number;
  const history = useQuery({
    // A new version comes with a render or a generation: the key follows the document's latest.
    queryKey: [...versionsKey(document.id), latestNumber ?? 0],
    queryFn: ({ signal }) => listVersions(document.id, signal),
  });
  const versions = history.data ?? [];
  // The list can be a moment behind the document: the document's own latest is always shown.
  const rows =
    document.latest !== null &&
    !versions.some((one) => one.number === document.latest?.number)
      ? [document.latest, ...versions]
      : versions;
  const shown =
    rows.find((one) => one.number === chosen) ?? document.latest ?? undefined;

  const open = isOpen(generation);
  const rendering = document.render.state !== "idle";

  return (
    <div className="flex flex-col gap-6">
      <Status
        open={open}
        rendering={rendering}
        generation={generation}
        renderMessage={document.render.message}
        kind={document.kind}
        hasVersion={shown !== undefined}
      />
      {shown === undefined ? (
        open ? null : (
          <p className="text-sm text-muted-foreground">
            Nothing has been written for this document yet.
          </p>
        )
      ) : (
        <Preview
          documentId={document.id}
          version={shown}
          isLatest={shown.number === latestNumber}
          rendering={rendering}
        />
      )}
      {shown !== undefined && shown.corrections_count > 0 ? (
        <Corrections documentId={document.id} version={shown} />
      ) : null}
      <History
        rows={rows}
        loading={history.isPending}
        error={history.isError ? describeError(history.error) : undefined}
        shown={shown?.number}
        onChoose={setChosen}
      />
    </div>
  );
}

function Status({
  open,
  rendering,
  generation,
  renderMessage,
  kind,
  hasVersion,
}: {
  open: boolean;
  rendering: boolean;
  generation: PanelProps["generation"];
  renderMessage: string | null;
  kind: Kind;
  hasVersion: boolean;
}) {
  if (open && generation !== null) {
    if (generation.status === "waiting") {
      return (
        <Alert>
          <AlertTitle>Waiting to start</AlertTitle>
          <AlertDescription>
            {generation.wait_reason
              ? WAIT_SENTENCES[generation.wait_reason]
              : "The writer will start shortly."}
          </AlertDescription>
        </Alert>
      );
    }
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        {hasVersion
          ? "Writing a new version"
          : "Your first CV is being built from your profile"}
      </div>
    );
  }
  if (generation?.status === "failed") {
    return (
      <Alert variant="destructive">
        <AlertTitle>The last attempt failed</AlertTitle>
        <AlertDescription>
          {failedSentence(
            "error" in generation ? generation.error : null,
            kind,
          )}
        </AlertDescription>
      </Alert>
    );
  }
  if (rendering) {
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Drawing the page
      </div>
    );
  }
  if (renderMessage) {
    return (
      <Alert variant="destructive">
        <AlertTitle>The page could not be drawn</AlertTitle>
        <AlertDescription>{renderMessage}</AlertDescription>
      </Alert>
    );
  }
  return null;
}

function Preview({
  documentId,
  version,
  isLatest,
  rendering,
}: {
  documentId: string;
  version: VersionSummary;
  isLatest: boolean;
  rendering: boolean;
}) {
  const overflow = version.fit === "overflow";
  return (
    <section
      aria-label={`Version ${String(version.number)}`}
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">
          Version {version.number}
          {isLatest ? ", the latest" : ""}
        </h3>
        <Badge
          variant={overflow ? "destructive" : "secondary"}
          data-testid="fit"
        >
          {fitLabel(version)}
        </Badge>
        {version.frozen_at !== null ? (
          <Badge variant="outline">
            Sent on {formatWhen(version.frozen_at)}
          </Badge>
        ) : null}
        {rendering && isLatest ? (
          <span className="text-xs text-muted-foreground">
            A newer page is being drawn
          </span>
        ) : null}
      </div>
      {overflow ? (
        <Alert variant="destructive">
          <AlertTitle>This is longer than one page</AlertTitle>
          <AlertDescription>
            It is kept and can be downloaded as it is. Shorten the longest
            fields, or choose a more compact template.
          </AlertDescription>
        </Alert>
      ) : null}
      {version.has_pdf ? (
        <>
          <img
            // The address names the version, so a new version is a new image.
            src={versionPage1Url(documentId, version.number, PREVIEW_WIDTH)}
            alt={`Page 1 of version ${String(version.number)}`}
            className={cn(
              "w-full max-w-xl rounded-md border bg-white shadow-sm",
              overflow && "border-destructive",
            )}
          />
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline">
              <a
                href={versionPdfUrl(documentId, version.number)}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink aria-hidden="true" />
                Open the PDF
              </a>
            </Button>
            <Button asChild variant="outline">
              <a href={versionPdfUrl(documentId, version.number)} download>
                <Download aria-hidden="true" />
                Download PDF
              </a>
            </Button>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          This version has no page yet.
        </p>
      )}
    </section>
  );
}

function Corrections({
  documentId,
  version,
}: {
  documentId: string;
  version: VersionSummary;
}) {
  const query = useQuery({
    queryKey: versionKey(documentId, version.number),
    queryFn: ({ signal }) => getVersion(documentId, version.number, signal),
  });
  const entries: Correction[] = (query.data?.corrections ?? []).flatMap(
    (one) => {
      const parsed = correctionSchema.safeParse(one);
      return parsed.success ? [parsed.data] : [];
    },
  );
  return (
    <section
      aria-labelledby="corrections-heading"
      className="flex flex-col gap-2 rounded-lg border p-3"
    >
      <h3 id="corrections-heading" className="text-sm font-semibold">
        Corrected by the fact check ({version.corrections_count})
      </h3>
      <p className="text-sm text-muted-foreground">
        The writer checks every claim against your profile. What it could not
        support was changed or removed, and you can type it back by hand.
      </p>
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the corrections
        </p>
      ) : query.isError ? (
        <p className="text-sm text-destructive">{describeError(query.error)}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {entries.map((one, index) => (
            <li key={`${one.field_path}-${String(index)}`} className="text-sm">
              <p className="font-medium">{fieldLabel(one.field_path)}</p>
              <p>
                <span className="text-muted-foreground">Before: </span>
                {one.before}
              </p>
              <p>
                <span className="text-muted-foreground">After: </span>
                {one.after === "" ? "Removed" : one.after}
              </p>
              <p className="text-muted-foreground">
                {one.reason} (changed by{" "}
                {one.by === "code" ? "a rule" : "the writer"})
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function History({
  rows,
  loading,
  error,
  shown,
  onChoose,
}: {
  rows: VersionSummary[];
  loading: boolean;
  error: string | undefined;
  shown: number | undefined;
  onChoose: (number: number) => void;
}) {
  if (rows.length === 0 && !loading && error === undefined) return null;
  return (
    <section aria-labelledby="history-heading" className="flex flex-col gap-2">
      <h3 id="history-heading" className="text-sm font-semibold">
        Versions
      </h3>
      {error !== undefined ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : loading && rows.length === 0 ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the versions
        </p>
      ) : (
        <ul className="flex flex-col divide-y rounded-lg border">
          {rows.map((one) => (
            <li
              key={one.number}
              className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"
            >
              <div className="flex min-w-0 flex-col gap-0.5">
                <span className="font-medium">
                  Version {one.number}, {ORIGIN_LABELS[one.origin]}
                </span>
                <span className="text-muted-foreground">
                  {formatWhen(one.created_at)},{" "}
                  {templateLabel(one.template_slug)}, {fitLabel(one)}
                  {one.approved_at !== null ? ", approved" : ""}
                  {one.frozen_at !== null
                    ? `, sent on ${formatWhen(one.frozen_at)}`
                    : ""}
                </span>
              </div>
              <Button
                variant={one.number === shown ? "secondary" : "outline"}
                size="sm"
                aria-pressed={one.number === shown}
                aria-label={
                  one.number === shown
                    ? `Shown, version ${String(one.number)}`
                    : `Open version ${String(one.number)}`
                }
                onClick={() => {
                  onChoose(one.number);
                }}
              >
                {one.number === shown ? "Shown" : "Open"}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
