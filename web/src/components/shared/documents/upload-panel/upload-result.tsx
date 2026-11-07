import type { ReactNode } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import type { BaseCv } from "@/features/documents/api/base-cvs-api";
import {
  failedSentence,
  isOpen,
  WAIT_SENTENCES,
} from "@/features/documents/helpers/labels";
import { FILL_REVIEW } from "@/features/documents/helpers/upload-cv";
import { UploadStatus } from "./upload-status";

/** The converted CV: its state, then where it opens, the default, and the profile completion. */
export function UploadResult({
  cv,
  openHref,
  defaulting,
  onDefault,
  complete,
}: {
  cv: BaseCv;
  openHref: (baseId: string) => string;
  defaulting: boolean;
  onDefault: () => void;
  /** The start of the run that completes the profile from this CV, or its progress. */
  complete: ReactNode;
}) {
  const generation = cv.generation;
  if (isOpen(generation))
    return generation?.status === "waiting" && generation.wait_reason ? (
      <p className="text-sm" data-testid="upload-waiting">
        {WAIT_SENTENCES[generation.wait_reason]}
      </p>
    ) : (
      <UploadStatus>Reading your CV</UploadStatus>
    );
  if (generation?.status === "failed" || generation?.status === "cancelled")
    return (
      <Alert variant="destructive">
        <AlertTitle>Your CV could not be converted</AlertTitle>
        <AlertDescription>{failedSentence(generation.error)}</AlertDescription>
      </Alert>
    );
  return (
    <div className="flex flex-col gap-3" data-testid="upload-result">
      <p className="text-sm">
        <bdi className="font-medium">{cv.name}</bdi> is ready to edit
        {cv.is_default ? ". It is your default CV." : "."}{" "}
        <Link
          to={openHref(cv.id)}
          className="font-medium underline underline-offset-4"
        >
          Open it
        </Link>
      </p>
      {cv.is_default ? null : (
        <Button
          variant="outline"
          className="w-fit"
          disabled={defaulting}
          onClick={onDefault}
        >
          Make this my default CV
        </Button>
      )}
      {complete}
      <p className="text-xs text-muted-foreground">
        Nothing changes in your profile until you accept the proposed changes on
        the{" "}
        <Link to={FILL_REVIEW} className="underline underline-offset-4">
          Fill my profile page
        </Link>
        , which opens by itself when the reading ends.
      </p>
    </div>
  );
}
