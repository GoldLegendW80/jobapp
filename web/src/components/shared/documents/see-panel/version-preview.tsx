import { Download, ExternalLink } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  versionPage1Url,
  versionPdfUrl,
  type VersionSummary,
} from "@/features/documents/api/documents-api";
import { fitLabel, formatWhen } from "@/features/documents/helpers/labels";

/** Width of the page image asked for: sharp on a phone held upright and on a laptop. */
const PREVIEW_WIDTH = 900;

/** One version's page: whether it fits, when it was sent, its image and its PDF. */
export function VersionPreview({
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
          {version.has_preview ? (
            <img
              // The address names the version, so a new version is a new image.
              src={versionPage1Url(documentId, version.number, PREVIEW_WIDTH)}
              alt={`Page 1 of version ${String(version.number)}`}
              className={cn(
                "w-full max-w-xl rounded-md border bg-white shadow-sm",
                overflow && "border-destructive",
              )}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              This is the PDF your command line made, kept as it was sent. It
              has no preview here: open it to see it.
            </p>
          )}
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
