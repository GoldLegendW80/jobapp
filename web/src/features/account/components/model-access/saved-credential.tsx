import type { UseQueryResult } from "@tanstack/react-query";
import { CircleAlert, KeyRound, RotateCw, Timer } from "lucide-react";

import { LoadingCard } from "@/components/blocks/loading-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { exactTime } from "@/features/admin/helpers/relative-time";
import { message } from "@/features/admin/helpers/errors";
import { formatWait } from "@/features/auth/helpers/retry-countdown";

import type { ModelCredential } from "@/features/agents/api/agents-api";
import { KIND_LABEL } from "@/features/agents/helpers/model-credential";

/** The credential saved, with Verify and Remove; or why there is none to show. */
export function SavedCredential({
  query,
  saved,
  verifying,
  verifyLocked,
  verifyWait,
  onVerify,
  onRemove,
}: {
  query: UseQueryResult<ModelCredential | null>;
  saved: ModelCredential | null;
  verifying: boolean;
  verifyLocked: boolean;
  verifyWait: number;
  onVerify: () => void;
  onRemove: () => void;
}) {
  if (query.isPending)
    return <LoadingCard>Loading your credential</LoadingCard>;
  if (query.isError)
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertDescription>
          <p>{message(query.error)}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void query.refetch();
            }}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  if (!saved)
    return (
      <p className="text-sm text-muted-foreground" data-testid="no-credential">
        No credential saved. Agents need one to run.
      </p>
    );
  return (
    <section
      aria-label="Saved credential"
      className="flex flex-col gap-4 rounded-lg border bg-muted/30 p-4"
    >
      <div className="flex items-start gap-3">
        <KeyRound
          aria-hidden="true"
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
        />
        <dl className="grid min-w-0 gap-1 text-sm">
          <div>
            <dt className="sr-only">Type</dt>
            <dd className="font-medium">{KIND_LABEL[saved.kind]}</dd>
          </div>
          <div className="flex flex-wrap gap-x-1">
            <dt className="text-muted-foreground">Ends with</dt>
            <dd className="font-mono" data-testid="credential-last4">
              {saved.last4}
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-1">
            <dt className="text-muted-foreground">Status</dt>
            <dd data-testid="credential-status">
              {saved.verified_at ? (
                <>
                  Verified on{" "}
                  <time dateTime={saved.verified_at}>
                    {exactTime(saved.verified_at)}
                  </time>
                </>
              ) : (
                "Not verified"
              )}
            </dd>
          </div>
        </dl>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          variant="outline"
          className="w-full sm:w-auto"
          onClick={onRemove}
        >
          Remove
        </Button>
        <Button
          variant="secondary"
          className="w-full sm:w-auto"
          disabled={verifying || verifyLocked}
          aria-busy={verifying}
          onClick={onVerify}
        >
          {verifying ? (
            <>
              <Spinner aria-hidden="true" role={undefined} />
              Verifying
            </>
          ) : verifyLocked ? (
            <>
              <Timer aria-hidden="true" />
              <span className="tabular-nums">
                Try again in {formatWait(verifyWait)}
              </span>
            </>
          ) : (
            "Verify"
          )}
        </Button>
      </div>
    </section>
  );
}
