import { Wand2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import {
  failureSentence,
  type VersionSummary,
} from "@/features/documents/api/documents-api";
import { useFitDocument } from "@/features/documents/api/use-document";
import { isOpen } from "@/features/documents/helpers/labels";
import { canShorten } from "@/features/documents/helpers/template-choice";
import type { PanelProps } from "../document-view";

/**
 * Above a page that runs over one page: it is kept as it is, and how to make it fit. For the
 * latest version of a posting's CV, Make it fit asks the writer for a shorter version.
 */
export function FitNote({
  document,
  generation,
  version,
  isLatest,
}: PanelProps & { version: VersionSummary; isLatest: boolean }) {
  const shorten = useFitDocument(document);
  if (version.fit !== "overflow") return null;
  const offer =
    isLatest &&
    canShorten(document) &&
    !isOpen(generation) &&
    document.render.state === "idle";
  return (
    <>
      <Alert variant="destructive" data-testid="overflow-note">
        <AlertTitle>This is longer than one page</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-2">
          <p>
            {offer
              ? "It is kept and can be downloaded as it is. Make it fit asks the writer for a shorter version; a more compact template helps too."
              : "It is kept and can be downloaded as it is. Shorten the longest texts of the page, or choose a more compact template."}
          </p>
          {offer ? (
            <Button
              size="sm"
              variant="outline"
              disabled={shorten.isPending}
              onClick={() => {
                shorten.mutate();
              }}
            >
              <Wand2 aria-hidden="true" />
              Make it fit
            </Button>
          ) : null}
        </AlertDescription>
      </Alert>
      {shorten.isSuccess ? (
        <p role="status" className="text-sm text-muted-foreground">
          A shorter version is being written. It shows here when it is ready.
        </p>
      ) : shorten.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(shorten.error)}</AlertDescription>
        </Alert>
      ) : null}
    </>
  );
}
