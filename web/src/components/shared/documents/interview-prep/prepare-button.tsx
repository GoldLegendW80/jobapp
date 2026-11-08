import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { isApiError } from "@/lib/api";

import { failureSentence } from "@/features/documents/api/documents-api";
import { CONSENT_REFUSAL } from "@/features/documents/api/interview-prep-api";
import {
  useAcceptConsent,
  useConsent,
} from "@/features/documents/api/use-consent";
import { usePrepare } from "@/features/documents/api/use-interview-prep";
import { ConsentDialog } from "../consent-dialog";

/**
 * Prepare (or Prepare again) on the person's click. Without the consent, the dialog of what is
 * sent opens and its agreement starts the preparation; any other refusal is said under it.
 */
export function PrepareButton({
  jobId,
  label,
  variant = "default",
}: {
  jobId: string;
  label: string;
  variant?: "default" | "outline";
}) {
  const prepare = usePrepare(jobId);
  const [asking, setAsking] = useState(false);
  const needsConsent =
    isApiError(prepare.error) &&
    prepare.error.status === 409 &&
    failureSentence(prepare.error).startsWith(CONSENT_REFUSAL);
  return (
    <div className="flex flex-col gap-3">
      <Button
        variant={variant}
        size="sm"
        className="self-start"
        disabled={prepare.isPending}
        onClick={() => {
          prepare.mutate(undefined, {
            onError: (error) => {
              if (
                isApiError(error) &&
                error.status === 409 &&
                failureSentence(error).startsWith(CONSENT_REFUSAL)
              )
                setAsking(true);
            },
          });
        }}
      >
        {label}
      </Button>
      {prepare.isError && !needsConsent ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(prepare.error)}</AlertDescription>
        </Alert>
      ) : null}
      {asking ? (
        <PrepareConsent
          onClose={() => {
            setAsking(false);
          }}
          onAgreed={() => {
            setAsking(false);
            prepare.mutate();
          }}
        />
      ) : null}
    </div>
  );
}

function PrepareConsent({
  onClose,
  onAgreed,
}: {
  onClose: () => void;
  onAgreed: () => void;
}) {
  const consent = useConsent();
  const accept = useAcceptConsent({ onAccepted: onAgreed });
  if (consent.data === undefined) return null;
  return (
    <ConsentDialog
      consent={consent.data}
      open
      busy={accept.isPending}
      error={accept.isError ? failureSentence(accept.error) : undefined}
      title="What is sent to prepare your interview"
      agreeLabel="Agree and prepare"
      onAgree={(version) => {
        accept.mutate(version);
      }}
      onClose={onClose}
    />
  );
}
