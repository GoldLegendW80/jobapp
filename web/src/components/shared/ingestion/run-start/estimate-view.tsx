import { useId } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";
import { cn } from "@/lib/utils";

import { tokens, type Estimate } from "@/features/ingestion/api/runs-api";
import { TWO_LINE_LABEL } from "@/features/ingestion/helpers/two-line-label";

const count = (value: number, one: string, many: string) =>
  `${tokens(value)} ${value === 1 ? one : many}`;

/** The figures of one estimate, each one as the server gave it. */
export function EstimateView({
  estimate,
  reread,
  refused,
  busy,
  onReread,
}: {
  estimate: Estimate;
  reread: readonly string[];
  refused: Record<string, string>;
  busy: boolean;
  onReread: (id: string, on: boolean) => void;
}) {
  const { cost, limits } = estimate;
  const boxes = useId();
  return (
    <div className="flex flex-col gap-4 text-sm" data-testid="estimate">
      <section aria-label="What is read" className="flex flex-col gap-2">
        <h4 className="font-semibold">What is read</h4>
        <ul className="flex flex-col divide-y rounded-lg border">
          {estimate.documents.map((one) => {
            const again = reread.includes(one.id);
            return (
              <li key={one.id} className="flex min-w-0 flex-col gap-1 p-3">
                <span className="font-medium break-words">{one.name}</span>
                <span className="text-xs text-muted-foreground">
                  {count(one.characters, "character", "characters")}
                  {one.images > 0
                    ? `, ${count(one.images, "image", "images")}`
                    : null}
                  , {count(one.steps, "step", "steps")}
                  {one.reused_steps > 0
                    ? `, ${tokens(one.reused_steps)} already read and reused`
                    : null}
                </span>
                {refused[one.id] ? (
                  <span className="text-xs text-destructive">
                    {refused[one.id]}
                  </span>
                ) : null}
                {one.reused_steps > 0 || again ? (
                  <Field
                    orientation="horizontal"
                    className="items-start gap-2"
                    data-disabled={busy || undefined}
                  >
                    <Checkbox
                      id={`${boxes}-${one.id}`}
                      className="mt-px"
                      checked={again}
                      disabled={busy}
                      onCheckedChange={(state) => {
                        onReread(one.id, state === true);
                      }}
                    />
                    <FieldLabel
                      htmlFor={`${boxes}-${one.id}`}
                      className={cn(TWO_LINE_LABEL, "text-xs")}
                    >
                      <span>Read {one.name} again</span>
                      <span className="text-muted-foreground">
                        Reading it again uses tokens again.
                      </span>
                    </FieldLabel>
                  </Field>
                ) : null}
              </li>
            );
          })}
          {estimate.unreadable.map((one) => (
            <li key={one.id} className="flex min-w-0 flex-col gap-1 p-3">
              <span className="font-medium break-words">{one.name}</span>
              <span className="text-xs text-destructive">
                Not read: {refused[one.id] ?? one.reason}
              </span>
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground">
          In all: {count(estimate.documents.length, "document", "documents")},{" "}
          {count(estimate.characters, "character", "characters")},{" "}
          {count(estimate.images, "image", "images")},{" "}
          {count(estimate.steps, "step", "steps")}
          {estimate.reused_steps > 0
            ? `, of which ${tokens(estimate.reused_steps)} already read and reused`
            : null}
          .
        </p>
      </section>

      <section aria-label="Tokens" className="flex flex-col gap-1">
        <h4 className="font-semibold">Tokens</h4>
        <p data-testid="estimate-range">
          About {tokens(estimate.tokens.low)} to {tokens(estimate.tokens.high)}{" "}
          tokens.
        </p>
        <p data-testid="estimate-ceiling">
          The run pauses at {tokens(estimate.tokens.ceiling)} tokens, and asks
          you before going on.
        </p>
        {estimate.model ? (
          <p className="text-muted-foreground" data-testid="estimate-model">
            {estimate.model_reason}
          </p>
        ) : null}
      </section>

      {cost ? (
        <section aria-label="Estimated cost" className="flex flex-col gap-1">
          <h4 className="font-semibold">Estimated cost</h4>
          <p data-testid="estimate-cost">
            About {cost.low} to {cost.high} {cost.currency}, as an estimate.
          </p>
          <p data-testid="estimate-cost-ceiling">
            The pause at {tokens(estimate.tokens.ceiling)} tokens is estimated
            at about {cost.ceiling} {cost.currency}. It is an estimate, not a
            limit on what you pay.
          </p>
          <p className="text-muted-foreground">{cost.note}</p>
        </section>
      ) : estimate.cost_note ? (
        <p data-testid="estimate-quota">{estimate.cost_note}</p>
      ) : null}

      <p className="text-xs text-muted-foreground">
        Limits of a run: {tokens(limits.documents)} documents,{" "}
        {tokens(limits.characters)} characters, {tokens(limits.images)} images,{" "}
        {tokens(limits.steps)} steps, {tokens(limits.step_minutes)} minutes for
        a step and {tokens(limits.run_minutes)} minutes in all.
      </p>
    </div>
  );
}
