import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { isApiError } from "@/lib/api";

import {
  baseCvsKey,
  captureDesign,
  documentKey,
  failureSentence,
  type BaseCv,
  type BaseCvs,
} from "./documents-api";
import { isOpen } from "./labels";
import { galleryPath } from "./template-choice";
import { CaptureConsent } from "./capture-consent";

function sentence(capture: BaseCv["design_generation"]): string {
  if (capture === null) return "Your CV's design has not been copied yet.";
  if (capture.error === "binding_gone")
    return "Upload your original CV again to copy its design.";
  switch (capture.status) {
    case "running":
      return "Copying your CV's design.";
    case "done":
      return "Your CV's design has been copied.";
    case "failed":
      return "Only the design copy failed. Your current template and content are still available.";
    case "cancelled":
      return "The design copy stopped. Your current template and content are still available.";
    case "waiting":
      if (capture.wait_reason === "consent")
        return "To copy your CV's design, use Confirm what is sent below.";
      if (capture.wait_reason === "credential")
        return "To copy your CV's design, add a model credential in your account.";
      if (capture.wait_reason === "daily_ceiling")
        return "The design copy is waiting for tomorrow's daily safety ceiling.";
      return "Waiting to copy your CV's design.";
    default:
      return "Waiting to copy your CV's design.";
  }
}

/** The api's own sentence for a refusal (409, 422); a fixed one for any other failure. */
function retryFailure(error: unknown): string {
  return isApiError(error) && (error.status === 409 || error.status === 422)
    ? failureSentence(error)
    : "The design could not be started. Try again.";
}

const stampOf = (capture: BaseCv["design_generation"]) =>
  capture === null ? "" : `${capture.id} ${capture.status}`;

/** Capture errors and progress belong to this CV, independently of its converted content. */
export function CaptureStatus({ cv }: { cv: BaseCv }) {
  const client = useQueryClient();
  const inFlight = useRef(false);
  const titleId = useId();
  const errorId = useId();
  // The capture the refresh after the last attempt returned: its error describes only that one.
  const [settledOn, setSettledOn] = useState<string>();
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: baseCvsKey }),
      client.invalidateQueries({ queryKey: documentKey(cv.id) }),
    ]);
  const retry = useMutation({
    mutationFn: () => captureDesign(cv.id),
    onSuccess: (capture) => {
      client.setQueryData<BaseCvs>(baseCvsKey, (list) =>
        list === undefined
          ? list
          : {
              ...list,
              base_cvs: list.base_cvs.map((one) =>
                one.id === cv.id ? { ...one, design_generation: capture } : one,
              ),
            },
      );
    },
    onSettled: async () => {
      try {
        await refresh();
      } finally {
        inFlight.current = false;
        const list = client.getQueryData<BaseCvs>(baseCvsKey);
        const mine = list?.base_cvs.find((one) => one.id === cv.id);
        setSettledOn(stampOf(mine?.design_generation ?? null));
      }
    },
  });
  const capture = cv.design_generation;
  const error =
    retry.isError && settledOn === stampOf(capture) ? retry.error : null;
  // A 422 is not transient: this CV no longer has the uploaded file a capture reads.
  const refused = isApiError(error) && error.status === 422;
  const busy = retry.isPending || isOpen(cv.generation) || isOpen(capture);
  return (
    <Alert role="group" aria-labelledby={titleId}>
      <AlertTitle id={titleId}>CV design</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <div role="status" aria-live="polite" className="flex flex-col gap-2">
          <p>{sentence(capture)}</p>
          {isOpen(capture) ? (
            <p>Other changes may wait while the design is copied.</p>
          ) : null}
          {error !== null ? (
            <p id={errorId} className="text-destructive">
              {retryFailure(error)}
            </p>
          ) : null}
        </div>
        {capture?.status === "waiting" && capture.wait_reason === "consent" ? (
          <CaptureConsent documentId={cv.id} />
        ) : null}
        {capture?.status === "waiting" &&
        capture.wait_reason === "credential" ? (
          <Link to="/account" className="underline underline-offset-4">
            Add a model credential
          </Link>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={`Try the design again for ${cv.name}`}
            aria-disabled={busy || refused}
            aria-describedby={refused ? errorId : undefined}
            className="aria-disabled:opacity-50"
            onClick={() => {
              if (inFlight.current || busy || refused) return;
              inFlight.current = true;
              retry.mutate();
            }}
          >
            Try the design again
          </Button>
          <Button asChild variant="link" size="sm">
            <Link
              to={galleryPath(cv.id)}
              aria-label={`Choose a template for ${cv.name}`}
            >
              Choose a template
            </Link>
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
