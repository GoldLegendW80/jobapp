import { Download, ExternalLink } from "lucide-react";

import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  versionPage1Url,
  versionPdfUrl,
  type VersionSummary,
} from "@/features/documents/api/documents-api";

/** Width of the page image asked for: sharp in the side rail and on a phone held upright. */
const PREVIEW_WIDTH = 900;

/** A version's PDF: Download first (`primary` makes it the page's main action), then Open. */
export function PdfActions({
  documentId,
  version,
  primary = false,
}: {
  documentId: string;
  version: VersionSummary;
  primary?: boolean;
}) {
  const href = versionPdfUrl(documentId, version.number);
  return (
    <>
      <Button asChild variant={primary ? "default" : "outline"}>
        <a href={href} download>
          <Download aria-hidden="true" />
          Download PDF
        </a>
      </Button>
      <Button asChild variant="outline">
        <a href={href} target="_blank" rel="noopener noreferrer">
          <ExternalLink aria-hidden="true" />
          Open the PDF
        </a>
      </Button>
    </>
  );
}

/**
 * Page 1 of the PDF of a version, as the renderer drew it: the page that is sent, beside the
 * page edited here. With `pdf`, its PDF links are the section's actions, when no page header
 * holds them.
 */
export function PrintedPage({
  documentId,
  version,
  pdf,
}: {
  documentId: string;
  version: VersionSummary;
  pdf: boolean;
}) {
  return (
    <Section
      title="Printed page"
      description={`Page 1 of the PDF of version ${String(version.number)}, as it is sent.`}
      actions={
        pdf && version.has_pdf ? (
          <PdfActions documentId={documentId} version={version} />
        ) : undefined
      }
    >
      {!version.has_pdf ? (
        <p className="text-sm text-muted-foreground">
          This version has no page yet.
        </p>
      ) : version.has_preview ? (
        <img
          // The address names the version, so a new version is a new image.
          src={versionPage1Url(documentId, version.number, PREVIEW_WIDTH)}
          alt={`Page 1 of version ${String(version.number)}`}
          className={cn(
            "w-full rounded-md border bg-white shadow-sm",
            version.fit === "overflow" && "border-destructive",
          )}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          This is the PDF your command line made, kept as it was sent. It has no
          preview here: open it to see it.
        </p>
      )}
    </Section>
  );
}
