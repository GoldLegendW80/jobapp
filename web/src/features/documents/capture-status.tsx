import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { isApiError } from "@/lib/api";

import {
  baseCvsKey,
  captureDesign,
  documentKey,
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

/** Capture errors and progress belong to this CV, independently of its converted content. */
export function CaptureStatus({ cv }: { cv: BaseCv }) {
  const client = useQueryClient();
  const inFlight = useRef(false);
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
      }
    },
  });
  const capture = cv.design_generation;
  const busy = retry.isPending || isOpen(cv.generation) || isOpen(capture);
  return (
    <Alert role="status" aria-live="polite">
      <AlertTitle>CV design</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <p>{sentence(capture)}</p>
        {isOpen(capture) ? (
          <p>Other changes may wait while the design is copied.</p>
        ) : null}
        {capture?.status === "waiting" && capture.wait_reason === "consent" ? (
          <CaptureConsent documentId={cv.id} />
        ) : null}
        {capture?.status === "waiting" &&
        capture.wait_reason === "credential" ? (
          <Link to="/account" className="underline underline-offset-4">
            Add a model credential
          </Link>
        ) : null}
        {retry.isError ? (
          <p className="text-destructive">
            {isApiError(retry.error) && retry.error.status === 409
              ? "Another change is in progress for this CV. Its latest state has been refreshed."
              : "The design could not be started. Try again."}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-disabled={busy}
            className="aria-disabled:opacity-50"
            onClick={() => {
              if (inFlight.current || busy) return;
              inFlight.current = true;
              retry.mutate();
            }}
          >
            Try the design again
          </Button>
          <Button asChild variant="link" size="sm">
            <Link to={galleryPath(cv.id)}>Choose a template</Link>
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
