import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  countCharacters,
  failureSentence,
  type Limits,
  type StoredDocument,
} from "@/features/ingestion/api/documents-api";
import { usePasteDocument } from "@/features/ingestion/api/use-documents";
import { needsConfirmation } from "@/features/ingestion/helpers/labels";

const number = new Intl.NumberFormat("en");

function pastedSentence(stored: StoredDocument): string {
  if (stored.duplicate)
    return `This text is already in your documents, as ${stored.name}.`;
  return needsConfirmation(stored)
    ? "Text added. It looks like it holds a password or a key: check it in the list below."
    : "Text added.";
}

/**
 * Paste a text instead of a file: something copied from a web page, a message, a note. It
 * becomes a document like any other. The count shown is the one the server enforces.
 */
export function PasteBox({
  limits,
  onStored,
}: {
  limits: Limits;
  onStored: (stored: StoredDocument) => void;
}) {
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string }>();
  const fieldId = useId();
  const countId = useId();
  const mutation = usePasteDocument({
    onPasted: (stored) => {
      setText("");
      setOutcome({ ok: true, message: pastedSentence(stored) });
      onStored(stored);
    },
    onError: (error) => {
      setOutcome({ ok: false, message: failureSentence(error) });
    },
  });

  const count = countCharacters(text);
  const tooLong = count > limits.paste_characters;
  const empty = text.trim() === "";
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (empty || tooLong || mutation.isPending) return;
        setOutcome(undefined);
        mutation.mutate(text);
      }}
    >
      <Field className="gap-2" data-invalid={tooLong || undefined}>
        <FieldLabel htmlFor={fieldId}>Or paste a text</FieldLabel>
        <Textarea
          id={fieldId}
          rows={5}
          value={text}
          placeholder="For example the About section of your LinkedIn page, or notes on a project."
          aria-describedby={countId}
          aria-invalid={tooLong || undefined}
          className="max-h-72"
          onChange={(event) => {
            setText(event.target.value);
            if (outcome) setOutcome(undefined);
          }}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          {/* Beside the button: none of the description's margins. */}
          <FieldDescription
            id={countId}
            className={cn(
              "text-xs tabular-nums nth-last-2:mt-0",
              tooLong && "font-medium text-destructive",
            )}
          >
            {tooLong
              ? `Too long: ${number.format(count)} of ${number.format(limits.paste_characters)} characters. Save it as a text file and add the file instead.`
              : `${number.format(count)} of ${number.format(limits.paste_characters)} characters`}
          </FieldDescription>
          <Button
            type="submit"
            variant="outline"
            disabled={empty || tooLong || mutation.isPending}
            aria-busy={mutation.isPending}
          >
            {mutation.isPending ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            Add this text
          </Button>
        </div>
      </Field>
      {outcome?.ok === false ? (
        <p role="alert" className="text-sm text-destructive">
          {outcome.message}
        </p>
      ) : null}
      <p
        role="status"
        aria-live="polite"
        className="text-sm text-muted-foreground empty:hidden"
      >
        {outcome?.ok ? outcome.message : ""}
      </p>
    </form>
  );
}
