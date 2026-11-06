/**
 * The card "Writing rules" of the Search tab (SPEC-documents.md, Decision 9): the rules code
 * enforces on the CVs and letters the writer makes, through `GET` and `PUT /api/documents/settings`. Each change
 * shows at once and is saved at once, the whole value at a time, one save after the other in the
 * order asked; a refused save puts back what the api answered last (between two tabs the last
 * save wins: the api keeps no revision). It sits inside the Search form, so nothing here submits it.
 */
import { Plus, X } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Group } from "@/components/shared/profile/section-ui";

import { failureSentence } from "@/features/documents/api/documents-api";
import { type DocumentSettings } from "@/features/documents/api/document-settings-api";
import {
  useDocumentSettings,
  useSaveDocumentSettings,
} from "@/features/documents/api/use-document-settings";

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

export function WritingRulesCard() {
  const query = useDocumentSettings();
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
  const id = useId();
  // Its own read of the cache, so that a render of this card alone sees the api's last answer.
  const saved = useDocumentSettings().data ?? first;
  const [phrase, setPhrase] = useState("");
  const [problem, setProblem] = useState<string>();
  // The value of the last change, set in the click itself so the switch moves with it.
  const [asked, setAsked] = useState<DocumentSettings>();
  const mutation = useSaveDocumentSettings<Save>({
    onSaved: () => {
      toast.success("Writing rules saved.");
    },
    onError: ({ added }) => {
      if (added !== undefined) setPhrase((now) => (now === "" ? added : now));
    },
    onSettled: ({ value }) => {
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
      <Field orientation="horizontal">
        <Switch
          id={`${id}-dash-switch`}
          aria-describedby={`${id}-dash`}
          checked={shown.no_dash_separators}
          onCheckedChange={(checked) => {
            save({ ...shown, no_dash_separators: checked });
          }}
        />
        <FieldContent className="gap-1">
          <FieldLabel htmlFor={`${id}-dash-switch`}>
            No dashes as separators
          </FieldLabel>
          <FieldDescription id={`${id}-dash`} className="text-xs">
            An em dash, an en dash or a spaced hyphen used as a separator
            becomes a comma. Hyphenated words and ranges of numbers keep a plain
            hyphen.
          </FieldDescription>
        </FieldContent>
      </Field>

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
              <Badge
                asChild
                key={one}
                variant="secondary"
                className="max-w-full min-w-0 overflow-visible border-border py-0.5 pr-0.5 pl-2.5 font-normal whitespace-normal"
              >
                <li>
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
                        banned_phrases: phrases.filter(
                          (other) => other !== one,
                        ),
                      });
                    }}
                  >
                    <X aria-hidden="true" />
                  </Button>
                </li>
              </Badge>
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
                <Spinner aria-hidden="true" role={undefined} />
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
