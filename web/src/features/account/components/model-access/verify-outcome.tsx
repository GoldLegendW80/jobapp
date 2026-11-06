import { CircleAlert, CircleCheck, Info } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

import type { VerifyOutcome as Outcome } from "@/features/agents/api/use-model-access";

/** What the last Verify said: verified, a note, or why it failed. */
export function VerifyOutcome({ outcome }: { outcome: Outcome }) {
  return (
    <Alert
      variant={outcome.tone === "error" ? "destructive" : "default"}
      aria-live="polite"
      data-testid="verify-result"
    >
      {outcome.tone === "success" ? (
        <CircleCheck aria-hidden="true" className="text-success" />
      ) : outcome.tone === "info" ? (
        <Info aria-hidden="true" />
      ) : (
        <CircleAlert aria-hidden="true" />
      )}
      <AlertDescription
        className={cn(outcome.tone !== "error" && "text-foreground")}
      >
        {outcome.text}
      </AlertDescription>
    </Alert>
  );
}
