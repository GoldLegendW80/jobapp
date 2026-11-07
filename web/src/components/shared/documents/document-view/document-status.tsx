import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";

import type { Kind } from "@/features/documents/api/documents-api";
import {
  failedSentence,
  WAIT_SENTENCES,
} from "@/features/documents/helpers/labels";
import type { PanelProps } from "../document-view";

/** What the document is doing, above its page: waiting, writing, failed, drawing, or not drawn. */
export function DocumentStatus({
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
        <Spinner aria-hidden="true" role={undefined} />
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
        <Spinner aria-hidden="true" role={undefined} />
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
