/**
 * Ideas the person adds in one click instead of typing (owner, 2026-11-05: "I want an agent to
 * find the best job titles according to the profile"; SPEC-profile, "Job title suggestions").
 *
 * `TitleSuggestions`, under Job titles: the job titles agent's ideas, each "+ Analytics engineer"
 * with its one line reason under it and a "Not for me" icon button beside it. A click adds the
 * title to the list being edited (the form's draft: the person still saves as before), and the
 * idea leaves the suggestions; "Not for me" removes it for good. "More ideas" asks the agent
 * again and its ideas join after the ones shown (owner: "add to them"). Under them, a short chat:
 * "Tell the assistant what you want to do" and Send, which runs the agent with the message (one
 * ask, as More ideas); the last message shows under the box with Clear, and the automatic
 * refreshes read the last few too. A few asks a day; on an API key the cost estimate shows before
 * the click, on a subscription nothing is said about money. The states: nothing yet (a model key,
 * or something to read: a CV, a profile or a message, which the box gives at once), on its way,
 * failed (the reason and Try again), nothing new after the checks, and a run that added nothing.
 * Opening a page never calls a model: the server answers the stored ideas, already checked
 * against the saved search.
 *
 * `PlaceSuggestion`, under Places: the city of the profile (else its country), "+ Lyon, from your
 * profile", while the list does not hold it. Read from the profile the page already loaded; no
 * model.
 *
 * Both take the list as it is now and an `onAdd`, so the forms of the Search tab and Find jobs
 * (search-lists.tsx) and the wizard's text fields (onboarding/components/steps/) share them. Both
 * sit inside those forms: nothing here is a form, and Enter in the message box sends the message
 * instead of submitting the page's form.
 */
import { Plus, RefreshCw, Send, X } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { money } from "@/features/documents/helpers/labels";
import { describeError } from "@/lib/api";
import { SUGGESTION_WORDS as W } from "@/lib/wording";

import {
  placeOf,
  sameTitle,
  useTitleSuggestions,
  type TitleSuggestions as Suggestions,
} from "@/features/profile/api/title-suggestions-api";
import {
  useAskMoreTitles,
  useClearTitleMessages,
  useDismissTitle,
} from "@/features/profile/api/use-title-actions";
import { useProfile } from "@/features/profile/api/use-profile";

const MUTED = "text-xs text-muted-foreground";
const LINK = "font-medium text-foreground underline underline-offset-4";

/** The money line before "More ideas" and Send: an API key only. */
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
  const query = useTitleSuggestions();
  const [draft, setDraft] = useState("");
  // Ideas dismissed in this view, hidden at once while the server records it.
  const [gone, setGone] = useState<readonly string[]>([]);
  const more = useAskMoreTitles({
    onAsked: (message) => {
      if (message !== undefined) setDraft("");
    },
  });
  const dismiss = useDismissTitle({
    onError: (title) => {
      setGone((list) => list.filter((one) => one !== title));
    },
  });
  const clear = useClearTitleMessages();
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
  if (data.needs === "model_key") {
    return (
      <Section id={id}>
        {links ? (
          <p className={MUTED}>
            {W.needsKey}{" "}
            <Link to="/account" className={LINK}>
              {W.addKey}
            </Link>
          </p>
        ) : (
          <p className={MUTED}>{W.needsKeyThere}</p>
        )}
      </Section>
    );
  }
  const shown = data.suggestions.filter(
    (one) =>
      !current.some((title) => sameTitle(title, one.title)) &&
      !gone.includes(one.title),
  );
  const empty = data.needs === "input";
  const running = data.status === "running" || more.isPending;
  const failed = data.status === "failed" && !running;
  const never = data.refreshed_at === null;
  // No daily limit (owner, 2026-11-06): one run at a time only.
  const canAsk = !running;
  const cost = costLine(data);
  const message = draft.trim();
  const send = () => {
    if (canAsk && message) more.mutate(message);
  };
  const editError = dismiss.isError
    ? dismiss.error
    : clear.isError
      ? clear.error
      : null;
  const last = data.messages.at(-1);
  return (
    <Section id={id}>
      {empty ? (
        links ? (
          <p className={MUTED}>
            {W.needsInput}{" "}
            <Link to="/profile/fill" className={LINK}>
              {W.addCv}
            </Link>
          </p>
        ) : (
          <p className={MUTED}>{W.needsInputThere}</p>
        )
      ) : shown.length > 0 ? (
        <ul
          aria-labelledby={`${id}-heading`}
          className="flex flex-wrap gap-x-4 gap-y-2"
        >
          {shown.map((one, index) => (
            <li
              key={one.title}
              className="flex max-w-full min-w-0 flex-col items-start gap-0.5 sm:max-w-64"
            >
              <span className="flex max-w-full min-w-0 items-center gap-0.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 max-w-full min-w-0 rounded-full px-2.5 font-normal"
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
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      className="text-muted-foreground"
                      aria-label={`${W.notForMe}: ${one.title}`}
                      onClick={() => {
                        setGone((list) => [...list, one.title]);
                        dismiss.mutate(one.title);
                      }}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{W.notForMe}</TooltipContent>
                </Tooltip>
              </span>
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
        ) : editError ? (
          describeError(editError)
        ) : data.note && shown.length > 0 ? (
          data.note
        ) : null}
      </p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {canAsk && !empty ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-2 h-7 px-2"
            onClick={() => {
              more.mutate(undefined);
            }}
          >
            <RefreshCw aria-hidden="true" />
            {failed ? W.retry : never ? W.suggest : W.more}
          </Button>
        ) : null}
        {canAsk && cost ? <span className={MUTED}>{cost}</span> : null}
      </div>
      <div className="flex flex-col gap-1.5 pt-1">
        <Label htmlFor={`${id}-message`} className="text-xs">
          {W.chatLabel}
        </Label>
        <div className="flex items-end gap-2">
          <Textarea
            id={`${id}-message`}
            value={draft}
            rows={1}
            maxLength={data.message_max}
            placeholder={W.chatPlaceholder}
            autoComplete="off"
            disabled={!canAsk}
            className="min-h-9 resize-none py-1.5"
            onChange={(event) => {
              setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              // One line: Enter sends the message (never the page's form, never a new line),
              // except while an input method composes a word.
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send();
              }
            }}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-9"
            disabled={!canAsk || !message}
            onClick={send}
          >
            <Send aria-hidden="true" />
            {W.send}
          </Button>
        </div>
        {last ? (
          <p className={`${MUTED} flex flex-wrap items-baseline gap-x-2`}>
            <span className="min-w-0 break-words">
              {W.lastMessage(last.text)}
              {data.messages.length > 1
                ? ` ${W.earlier(data.messages.length - 1)}`
                : null}
            </span>
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              aria-label={W.clearName}
              disabled={clear.isPending}
              onClick={() => {
                clear.mutate();
              }}
            >
              {W.clear}
            </Button>
          </p>
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
  const profile = useProfile({ enabled: false });
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
