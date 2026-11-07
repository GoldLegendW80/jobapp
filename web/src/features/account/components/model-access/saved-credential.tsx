import type { UseQueryResult } from "@tanstack/react-query";
import { Timer } from "lucide-react";

import { LoadError } from "@/components/blocks/load-error";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { exactTime } from "@/features/admin/helpers/relative-time";
import { formatWait } from "@/features/auth/helpers/retry-countdown";

import type { ModelCredential } from "@/features/agents/api/agents-api";
import { KIND_LABEL } from "@/features/agents/helpers/model-credential";

/** The credential saved, as plain facts with Verify and Remove; or why there is none to show. */
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
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading your credential
      </p>
    );
  if (query.isError)
    return (
      <LoadError query={query}>Your credential could not be loaded.</LoadError>
    );
  if (!saved)
    return (
      <p className="text-sm text-muted-foreground" data-testid="no-credential">
        No credential saved. Agents need one to run.
      </p>
    );
  return (
    <section aria-label="Saved credential" className="flex flex-col gap-3">
      <h3 className="text-sm font-medium">Current credential</h3>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Type</dt>
        <dd>{KIND_LABEL[saved.kind]}</dd>
        <dt className="text-muted-foreground">Ends with</dt>
        <dd className="font-mono" data-testid="credential-last4">
          {saved.last4}
        </dd>
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
      </dl>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
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
        <Button variant="outline" onClick={onRemove}>
          Remove
        </Button>
      </div>
    </section>
  );
}
