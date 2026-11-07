import { tokens, type Estimate } from "@/features/ingestion/api/runs-api";

/** Under the button: where the run pauses, and what an API key may pay, as estimates. */
export function EstimateLine({ estimate }: { estimate: Estimate }) {
  const { cost } = estimate;
  return (
    <p className="text-sm text-muted-foreground" data-testid="estimate-line">
      The run pauses at {tokens(estimate.tokens.ceiling)} tokens and asks you
      before going on.
      {cost
        ? ` On your API key, about ${cost.low} to ${cost.high} ${cost.currency}, as an estimate.`
        : null}
    </p>
  );
}
