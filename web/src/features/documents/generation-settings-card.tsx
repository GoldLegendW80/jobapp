/**
 * The documents part of the profile's Search tab (SPEC-documents.md, "Profile page, Search
 * options tab"): the card "Documents for new postings" and, after it, the Writing rules card.
 *
 * The switch, the threshold and the safety ceiling are fields of the Search form: they are saved
 * with the rest of the section by its own Save (`PUT /api/profile/search`, its 409 dialog
 * included). The counts and the spend come from `GET /api/documents/spend`; before a lower
 * threshold is saved, the same call asked at that value says what it would add.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useWatch, type UseFormReturn } from "react-hook-form";

import { AdvancedSection } from "@/components/advanced-section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import type { SearchForm } from "@/features/profile/editor-forms";
import { Group, TextField } from "@/features/profile/section-ui";

import {
  failureSentence,
  getSpend,
  spendKey,
  type Estimate,
  type Spend,
} from "./documents-api";
import { money, tokenRange, tokens } from "./labels";
import { WritingRulesCard } from "./writing-rules-card";

/** How long the threshold rests before the count at that value is asked for. */
const SETTLE_MS = 300;

type Form = UseFormReturn<SearchForm>;

export function DocumentsSearchOptions({ form }: { form: Form }) {
  return (
    <>
      <GenerationSettingsCard form={form} />
      <WritingRulesCard />
    </>
  );
}

const postings = (count: number): string =>
  count === 1 ? "1 posting" : `${tokens(count)} postings`;

/** "about 1,000 to 2,000 tokens, about USD 0.10 to USD 0.20" (the cost on an API key only). */
function estimateSentence(estimate: Estimate): string {
  const range = tokenRange(estimate.tokens);
  if (estimate.cost === null) return range;
  const { currency, low, high } = estimate.cost;
  return `${range}, about ${money(currency, low)} to ${money(currency, high)}`;
}

function useSettled<T>(value: T): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSettled(value);
    }, SETTLE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [value]);
  return settled;
}

export function GenerationSettingsCard({ form }: { form: Form }) {
  const { control } = form;
  const auto = useWatch({ control, name: "documents.auto" });
  const draft = useWatch({ control, name: "documents.min_fit" });
  // The values of the last save (the form is reset to them), which the api counts from.
  const saved = form.formState.defaultValues?.documents;
  const savedFit = saved?.min_fit ?? draft;
  const ceiling = Number(saved?.daily_ceiling ?? 0);
  const key = (fit: number) =>
    [...spendKey(fit), saved?.auto, savedFit, saved?.daily_ceiling] as const;
  const current = useQuery({
    queryKey: key(savedFit),
    queryFn: ({ signal }) => getSpend(savedFit, signal),
  });
  const asked = useSettled(draft);
  const atDraft = useQuery({
    queryKey: key(asked),
    queryFn: ({ signal }) => getSpend(asked, signal),
    enabled: asked !== savedFit,
  });
  const ceilingError = form.formState.errors.documents?.daily_ceiling;
  const [advanced, setAdvanced] = useState(false);

  return (
    <Group
      title="Documents for new postings"
      description="A CV and a cover letter written for each new posting that fits you, ready when you open it."
    >
      <div className="flex flex-col gap-2" data-testid="documents-generation">
        <FormField
          control={control}
          name="documents.auto"
          render={({ field }) => (
            <label className="flex w-fit items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                role="switch"
                aria-checked={field.value}
                className="size-4 accent-primary"
                name={field.name}
                ref={field.ref}
                checked={field.value}
                onBlur={field.onBlur}
                onChange={(event) => {
                  field.onChange(event.target.checked);
                }}
              />
              Write documents automatically
            </label>
          )}
        />
        <p className="text-sm" aria-live="polite">
          {auto
            ? `For postings with a fit of ${String(draft)} or more.`
            : 'No documents are written automatically. "Write now" on a posting still writes its CV and letter.'}
        </p>
      </div>
      <Today query={current} ceiling={ceiling} />
      <SpendFigures query={current} />
      <AdvancedSection
        open={advanced || ceilingError !== undefined}
        onOpenChange={setAdvanced}
      >
        <Threshold
          form={form}
          draft={draft}
          asked={asked}
          savedFit={savedFit}
          atSaved={current}
          atDraft={atDraft}
        />
        <TextField
          control={control}
          name="documents.daily_ceiling"
          label="Safety ceiling per day"
          inputMode="numeric"
          autoComplete="off"
          className="max-w-48"
          description="Protects you from a broken sweep. The threshold is what limits spend."
        />
      </AdvancedSection>
    </Group>
  );
}

type SpendQuery = UseQueryResult<Spend>;

function Today({ query, ceiling }: { query: SpendQuery; ceiling: number }) {
  if (query.isPending) {
    return (
      <p className="text-sm text-muted-foreground">
        {"Reading today's count."}
      </p>
    );
  }
  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{failureSentence(query.error)}</AlertDescription>
      </Alert>
    );
  }
  const { written_today, waiting } = query.data;
  return (
    <ul className="flex flex-col gap-1 text-sm" data-testid="documents-today">
      <li>
        Today: {postings(written_today)} written, the safety ceiling is{" "}
        {tokens(ceiling)}.
      </li>
      {ceiling > 0 && written_today >= ceiling ? (
        <li className="font-medium">
          {
            "Today's safety ceiling is reached: new postings wait until tomorrow."
          }
        </li>
      ) : null}
      <li>
        {waiting === 0
          ? "No posting is waiting."
          : `${postings(waiting)} waiting.`}
      </li>
    </ul>
  );
}

function SpendFigures({ query }: { query: SpendQuery }) {
  const id = useId();
  if (!query.isSuccess) return null;
  const { today, last_30_days: month, priced, cost_note: note } = query.data;
  const used = (spent: Spend["today"]) => spent.input + spent.output;
  const nothing = used(month) === 0 && Number(month.cost_usd) === 0;
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-2"
      data-testid="documents-spend"
    >
      <h4 id={id} className="text-sm font-medium">
        Spend
      </h4>
      {nothing ? (
        <p className="text-sm text-muted-foreground">
          Nothing spent on documents yet.
        </p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Tokens today</dt>
          <dd className="tabular-nums">{tokens(used(today))}</dd>
          <dt className="text-muted-foreground">Tokens over 30 days</dt>
          <dd className="tabular-nums">{tokens(used(month))}</dd>
          <dt className="text-muted-foreground">
            {priced ? "Cost" : "Estimate at API prices"}
          </dt>
          <dd className="tabular-nums">
            {money("USD", today.cost_usd)} today, {money("USD", month.cost_usd)}{" "}
            over 30 days
          </dd>
        </dl>
      )}
      {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
    </section>
  );
}

function Threshold({
  form,
  draft,
  asked,
  savedFit,
  atSaved,
  atDraft,
}: {
  form: Form;
  draft: number;
  asked: number;
  savedFit: number;
  atSaved: SpendQuery;
  atDraft: SpendQuery;
}) {
  const answer = asked === savedFit ? atSaved : atDraft;
  return (
    <FormField
      control={form.control}
      name="documents.min_fit"
      render={({ field }) => (
        <FormItem>
          <div className="flex items-baseline justify-between gap-2">
            <FormLabel>Fit score threshold</FormLabel>
            <output className="text-sm tabular-nums text-muted-foreground">
              {field.value} / 100
            </output>
          </div>
          <FormControl>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              className="w-full accent-primary"
              name={field.name}
              ref={field.ref}
              value={field.value}
              onBlur={field.onBlur}
              onChange={(event) => {
                field.onChange(Number(event.target.value));
              }}
            />
          </FormControl>
          <FormDescription>
            {
              'New postings with a fit at or above it get their documents without a click. Under it, "Write now" on the posting writes them.'
            }
          </FormDescription>
          <div aria-live="polite" data-testid="documents-threshold">
            <ThresholdFigures
              answer={answer}
              settled={asked === draft}
              draft={draft}
              savedFit={savedFit}
            />
          </div>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function ThresholdFigures({
  answer,
  settled,
  draft,
  savedFit,
}: {
  answer: SpendQuery;
  settled: boolean;
  draft: number;
  savedFit: number;
}) {
  if (!settled || answer.isPending) {
    return (
      <p className="text-sm text-muted-foreground">
        Counting the postings of the last 30 days.
      </p>
    );
  }
  if (answer.isError) {
    return (
      <p className="text-sm text-destructive">
        {failureSentence(answer.error)}
      </p>
    );
  }
  const {
    postings_30_days: count,
    newly,
    newly_estimate: estimate,
  } = answer.data;
  const have = count === 1 ? "has" : "have";
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>
        {postings(count)} of the last 30 days {have} a fit of {draft} or more.
      </p>
      {draft === 0 ? (
        <Alert>
          <TriangleAlert aria-hidden="true" />
          <AlertDescription>
            At 0 every posting with a fit score qualifies: {postings(count)} of
            the last 30 days.
          </AlertDescription>
        </Alert>
      ) : null}
      {draft === 100 ? (
        <p className="text-muted-foreground">
          At 100 only a perfect fit qualifies.
        </p>
      ) : null}
      {draft < savedFit && newly !== undefined && estimate !== undefined ? (
        <p className="font-medium" data-testid="documents-newly">
          {newly === 0
            ? `Lowering it to ${String(draft)} adds no posting to write.`
            : `Lowering it to ${String(draft)} adds ${postings(newly)} to write, ${estimateSentence(estimate)}.`}
        </p>
      ) : null}
      {draft > savedFit ? (
        <p className="text-muted-foreground">
          Raising it removes nothing already written.
        </p>
      ) : null}
    </div>
  );
}
