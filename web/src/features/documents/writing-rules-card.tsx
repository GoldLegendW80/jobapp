/**
 * The card "Writing rules" of the Search tab (SPEC-documents.md, Decision 9): the rules code
 * enforces on every CV and letter, through `GET` and `PUT /api/documents/settings`. Each change
 * is saved at once, the whole value at a time, with the controls held while it saves; the card
 * shows what the api answered (between two tabs the last save wins: the api keeps no revision).
 * It sits inside the Search form, so nothing here submits it.
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

export function WritingRulesCard() {
  const query = useQuery({
    queryKey: settingsKey,
    queryFn: ({ signal }) => getSettings(signal),
  });
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

function Rules({ saved }: { saved: DocumentSettings }) {
  const client = useQueryClient();
  const id = useId();
  const [phrase, setPhrase] = useState("");
  const [problem, setProblem] = useState<string>();
  const mutation = useMutation({
    mutationFn: putSettings,
    onSuccess: (value) => {
      client.setQueryData(settingsKey, value);
      toast.success("Writing rules saved.");
    },
  });
  const busy = mutation.isPending;
  const phrases = saved.banned_phrases;
  const full = phrases.length >= PHRASES_MAX;

  const save = (value: DocumentSettings) => {
    mutation.mutate(value, {
      onSuccess: () => {
        setPhrase("");
      },
    });
  };
  const add = () => {
    const text = phrase.split(/\s+/u).filter(Boolean).join(" ");
    if (text === "" || busy || full) return;
    if (phrases.some((one) => fold(one) === fold(text))) {
      setProblem("This phrase is already in the list.");
      return;
    }
    setProblem(undefined);
    save({ ...saved, banned_phrases: [...phrases, text] });
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
            aria-checked={saved.no_dash_separators}
            aria-describedby={`${id}-dash`}
            className="size-4 accent-primary"
            checked={saved.no_dash_separators}
            disabled={busy}
            onChange={(event) => {
              save({ ...saved, no_dash_separators: event.target.checked });
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
                  disabled={busy}
                  onClick={() => {
                    setProblem(undefined);
                    save({
                      ...saved,
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
              disabled={busy || phrase.trim() === ""}
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
