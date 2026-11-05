/**
 * Ideas the person adds in one click instead of typing (owner, 2026-11-05: "I want an agent to
 * find the best job titles according to the profile"; SPEC-profile, "Job title suggestions").
 *
 * `TitleSuggestions`, under Job titles: the job titles agent's last suggestions, each "+ Analytics
 * engineer" with its one line reason under it. A click adds the title to the list being edited
 * (the form's draft: the person still saves as before), and the idea leaves the suggestions.
 * "More ideas" asks the agent again (a few times a day; on an API key the cost estimate shows
 * before the click, on a subscription nothing is said about money). The states: nothing yet
 * (with the one thing that makes ideas possible: a model key, or a CV), on its way, failed (the
 * reason and Try again), and nothing new after the checks. Opening a page never calls a model:
 * the server answers the stored ideas, already checked against the saved search.
 *
 * `PlaceSuggestion`, under Places: the city of the profile (else its country), "+ Lyon, from your
 * profile", while the list does not hold it. Read from the profile the page already loaded; no
 * model.
 *
 * Both take the list as it is now and an `onAdd`, so the forms of the Search tab and Find jobs
 * (search-lists.tsx) and the wizard's text fields (onboarding/step-panels.tsx) share them.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { money } from "@/features/documents/labels";
import { describeError } from "@/lib/api";
import { SUGGESTION_WORDS as W } from "@/lib/wording";

import { getProfile, profileKey } from "./api";
import {
  askMoreTitles,
  placeOf,
  sameTitle,
  titleSuggestionsKey,
  useTitleSuggestions,
  type TitleSuggestions as Suggestions,
} from "./title-suggestions-api";

const MUTED = "text-xs text-muted-foreground";
const LINK = "font-medium text-foreground underline underline-offset-4";

/** The money line before "More ideas": an API key only. */
function costLine(data: Suggestions): string | null {
  const cost = data.estimate?.cost;
  if (!cost) return null;
  return `About ${money(cost.currency, cost.low)} to ${money(cost.currency, cost.high)} each time, at list prices.`;
}

export function TitleSuggestions({
  current,
  onAdd,
  label,
  quiet = false,
  links = true,
}: {
  /** the titles in the list being edited, so an added idea leaves at once */
  current: readonly string[];
  onAdd: (title: string) => void;
  /** the list's name, for the buttons' names ("Job titles", "Roles") */
  label: string;
  /** the wizard: nothing at all until a key and something to read exist */
  quiet?: boolean;
  /** false in a dialog: what is missing is said without a link that would drop the draft */
  links?: boolean;
}) {
  const id = useId();
  const client = useQueryClient();
  const query = useTitleSuggestions();
  const more = useMutation({
    mutationFn: askMoreTitles,
    onSuccess: (data) => {
      client.setQueryData(titleSuggestionsKey, data);
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: titleSuggestionsKey });
    },
  });
  const data = query.data;
  if (quiet && data?.needs !== null) return null;
  if (query.isPending) return null;
  if (!data) {
    return (
      <Section id={id}>
        <p className={MUTED}>
          Job title ideas could not be loaded. {describeError(query.error)}{" "}
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            onClick={() => void query.refetch()}
          >
            {W.retry}
          </Button>
        </p>
      </Section>
    );
  }
  if (data.needs === "model_key" || data.needs === "cv") {
    const key = data.needs === "model_key";
    return (
      <Section id={id}>
        {links ? (
          <p className={MUTED}>
            {key ? W.needsKey : W.needsCv}{" "}
            <Link to={key ? "/account" : "/profile/fill"} className={LINK}>
              {key ? W.addKey : W.addCv}
            </Link>
          </p>
        ) : (
          <p className={MUTED}>{key ? W.needsKeyThere : W.needsCvThere}</p>
        )}
      </Section>
    );
  }
  const shown = data.suggestions.filter(
    (one) => !current.some((title) => sameTitle(title, one.title)),
  );
  const running = data.status === "running" || more.isPending;
  const failed = data.status === "failed" && !running;
  const never = data.refreshed_at === null;
  const canAsk = data.asks_left > 0 && !running;
  const ask = () => {
    more.mutate();
  };
  const cost = costLine(data);
  return (
    <Section id={id}>
      {shown.length > 0 ? (
        <ul
          aria-labelledby={`${id}-heading`}
          className="flex flex-wrap gap-x-4 gap-y-2"
        >
          {shown.map((one, index) => (
            <li
              key={one.title}
              className="flex max-w-full min-w-0 flex-col items-start gap-0.5 sm:max-w-64"
            >
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 max-w-full rounded-full px-2.5 font-normal"
                aria-label={`Add ${one.title} to ${label}`}
                aria-describedby={
                  one.reason ? `${id}-reason-${index}` : undefined
                }
                onClick={() => {
                  onAdd(one.title);
                }}
              >
                <Plus aria-hidden="true" />
                <span className="truncate" lang={one.language}>
                  {one.title}
                </span>
              </Button>
              {one.reason ? (
                <span
                  id={`${id}-reason-${index}`}
                  className={`${MUTED} pl-1`}
                  lang={one.language}
                >
                  {one.reason}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : running ? (
        <div aria-hidden="true" className="flex flex-wrap gap-2">
          <Skeleton className="h-7 w-36 rounded-full" />
          <Skeleton className="h-7 w-44 rounded-full" />
          <Skeleton className="h-7 w-28 rounded-full" />
        </div>
      ) : failed ? null : never ? (
        <p className={MUTED}>{W.first}</p>
      ) : (
        <p className={MUTED}>{W.none}</p>
      )}
      <p role="status" className={MUTED}>
        {running ? (
          <span className="inline-flex items-center gap-1.5">
            <Spinner aria-hidden="true" className="size-3" />
            {W.running}
          </span>
        ) : failed && data.error ? (
          data.error
        ) : more.isError ? (
          describeError(more.error)
        ) : null}
      </p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {canAsk ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-2 h-7 px-2"
            onClick={ask}
          >
            <RefreshCw aria-hidden="true" />
            {failed ? W.retry : never ? W.suggest : W.more}
          </Button>
        ) : null}
        {canAsk && cost ? <span className={MUTED}>{cost}</span> : null}
        {!running && data.asks_left === 0 ? (
          <span className={MUTED}>{W.askedEnough(data.asks_per_day)}</span>
        ) : null}
      </div>
    </Section>
  );
}

function Section({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex flex-col gap-1.5"
    >
      <h5 id={`${id}-heading`} className="text-xs font-medium">
        {W.heading}
      </h5>
      {children}
    </section>
  );
}

export function PlaceSuggestion({
  current,
  onAdd,
  label,
}: {
  current: readonly string[];
  onAdd: (place: string) => void;
  label: string;
}) {
  // The profile the page already loaded: never a request of its own.
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
    enabled: false,
  });
  const place = placeOf(profile.data?.sections.identity);
  if (!place || current.some((one) => sameTitle(one, place))) return null;
  return (
    <div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 max-w-full rounded-full px-2.5 font-normal"
        aria-label={`Add ${place}, ${W.fromProfile}, to ${label}`}
        onClick={() => {
          onAdd(place);
        }}
      >
        <Plus aria-hidden="true" />
        <span className="truncate">
          {place}
          <span className="text-muted-foreground">, {W.fromProfile}</span>
        </span>
      </Button>
    </div>
  );
}
