/**
 * The card "Writing rules" of the Search tab (SPEC-documents.md, Decision 9): the rules code
 * enforces on the CVs and letters the writer makes, through `GET` and `PUT /api/documents/settings`. Each change
 * shows at once and is saved at once, the whole value at a time, one save after the other in the
 * order asked; a refused save puts back what the api answered last (between two tabs the last
 * save wins: the api keeps no revision). It sits inside the Search form, so nothing here submits it.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Plus, X } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Group } from "@/features/profile/section-ui";

import {
  failureSentence,
  getSettings,
  putSettings,
  settingsKey,
  type DocumentSettings,
} from "./documents-api";

/** settings.py: BANNED_PHRASES_MAX and BANNED_PHRASE_MAX. */
const PHRASES_MAX = 50;
const PHRASE_MAX = 100;

/** Close to the api's fold (case and accents), to say "already listed" before a save. */
const fold = (text: string): string =>
  text
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase("en")
    .split(/\s+/u)
    .filter((word) => word !== "")
    .join(" ");

/** One save: the whole value, and the phrase it adds, put back in its box when it is refused. */
interface Save {
  value: DocumentSettings;
  added?: string;
}

const useSettings = () =>
  useQuery({
    queryKey: settingsKey,
    queryFn: ({ signal }) => getSettings(signal),
  });

export function WritingRulesCard() {
  const query = useSettings();
  return (
    <Group
      title="Writing rules"
      description="Rules the writer follows for your CVs and letters. What breaks one is corrected."
    >
      {query.isPending ? (
        <p className="text-sm text-muted-foreground">
          Reading your writing rules.
        </p>
      ) : query.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(query.error)}</AlertDescription>
        </Alert>
      ) : (
        <Rules saved={query.data} />
      )}
    </Group>
  );
}

function Rules({ saved: first }: { saved: DocumentSettings }) {
  const client = useQueryClient();
  const id = useId();
  // Its own read of the cache, so that a render of this card alone sees the api's last answer.
  const saved = useSettings().data ?? first;
  const [phrase, setPhrase] = useState("");
  const [problem, setProblem] = useState<string>();
  // The value of the last change, set in the click itself so the switch moves with it.
  const [asked, setAsked] = useState<DocumentSettings>();
  const mutation = useMutation({
    mutationFn: ({ value }: Save) => putSettings(value),
    // Each PUT is the whole value: one at a time, so the last one asked is the last one kept.
    scope: { id: "documents-settings" },
    onSuccess: (value) => {
      client.setQueryData(settingsKey, value);
      toast.success("Writing rules saved.");
    },
    onError: (_error, { added }) => {
      if (added !== undefined) setPhrase((now) => (now === "" ? added : now));
    },
    onSettled: (_data, _error, { value }) => {
      setAsked((now) => (now === value ? undefined : now));
    },
  });
  const busy = mutation.isPending;
  const shown = asked ?? saved;
  const phrases = shown.banned_phrases;
  const full = phrases.length >= PHRASES_MAX;

  const save = (value: DocumentSettings, added?: string) => {
    setAsked(value);
    mutation.mutate({ value, added });
  };
  const add = () => {
    const text = phrase.split(/\s+/u).filter(Boolean).join(" ");
    if (text === "" || full) return;
    if (phrases.some((one) => fold(one) === fold(text))) {
      setProblem("This phrase is already in the list.");
      return;
    }
    setProblem(undefined);
    setPhrase("");
    save({ ...shown, banned_phrases: [...phrases, text] }, text);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  };

  return (
    <div className="flex flex-col gap-4" aria-busy={busy}>
      <div className="flex flex-col gap-1">
        <label className="flex w-fit items-center gap-2 text-sm font-medium">
          <input
            type="checkbox"
            role="switch"
            aria-checked={shown.no_dash_separators}
            aria-describedby={`${id}-dash`}
            className="size-4 accent-primary"
            checked={shown.no_dash_separators}
            onChange={(event) => {
              save({ ...shown, no_dash_separators: event.target.checked });
            }}
          />
          No dashes as separators
        </label>
        <p id={`${id}-dash`} className="text-xs text-muted-foreground">
          An em dash, an en dash or a spaced hyphen used as a separator becomes
          a comma. Hyphenated words and ranges of numbers keep a plain hyphen.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h4 id={`${id}-phrases`} className="text-sm font-medium">
            Banned phrases
          </h4>
          <span className="text-xs tabular-nums text-muted-foreground">
            {phrases.length} of {PHRASES_MAX}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          A sentence holding one of these words or phrases is corrected. Case
          and accents do not matter.
        </p>
        {phrases.length === 0 ? (
          <p className="text-sm text-muted-foreground">No banned phrase yet.</p>
        ) : (
          <ul
            aria-labelledby={`${id}-phrases`}
            className="flex flex-wrap gap-1.5"
          >
            {phrases.map((one) => (
              <li
                key={one}
                className="inline-flex max-w-full min-w-0 items-center gap-1 rounded-full border bg-secondary py-0.5 pr-0.5 pl-2.5 text-xs text-secondary-foreground"
              >
                <span className="min-w-0 break-words">{one}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-6 shrink-0 rounded-full"
                  aria-label={`Remove ${one}`}
                  onClick={() => {
                    setProblem(undefined);
                    save({
                      ...shown,
                      banned_phrases: phrases.filter((other) => other !== one),
                    });
                  }}
                >
                  <X aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        {full ? (
          <p className="text-sm">
            {`You have ${String(PHRASES_MAX)} phrases, the most kept. Remove one to add another.`}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="New banned phrase"
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${id}-problem` : undefined}
              className="min-w-0 flex-1 basis-48"
              maxLength={PHRASE_MAX}
              autoComplete="off"
              placeholder="passionate about"
              value={phrase}
              onChange={(event) => {
                setPhrase(event.target.value);
                setProblem(undefined);
              }}
              onKeyDown={onKeyDown}
            />
            <Button
              type="button"
              variant="outline"
              disabled={phrase.trim() === ""}
              onClick={add}
            >
              {busy ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : (
                <Plus aria-hidden="true" />
              )}
              Add
            </Button>
          </div>
        )}
        {problem ? (
          <p id={`${id}-problem`} className="text-sm text-destructive">
            {problem}
          </p>
        ) : null}
      </div>

      {mutation.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {`The writing rules were not saved. ${failureSentence(mutation.error)}`}
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
