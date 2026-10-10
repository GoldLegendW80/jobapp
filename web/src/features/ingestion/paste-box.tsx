import { useMutation } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  countCharacters,
  failureSentence,
  pasteDocument,
  type Limits,
  type StoredDocument,
} from "./documents-api";
import { needsConfirmation } from "./labels";

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
  const mutation = useMutation({
    mutationFn: pasteDocument,
    onSuccess: (stored) => {
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
      <Label htmlFor={fieldId}>Or paste a text</Label>
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
        <p
          id={countId}
          className={cn(
            "text-xs text-muted-foreground tabular-nums",
            tooLong && "font-medium text-destructive",
          )}
        >
          {tooLong
            ? `Too long: ${number.format(count)} of ${number.format(limits.paste_characters)} characters. Save it as a text file and add the file instead.`
            : `${number.format(count)} of ${number.format(limits.paste_characters)} characters`}
        </p>
        <Button
          type="submit"
          variant="outline"
          disabled={empty || tooLong || mutation.isPending}
          aria-busy={mutation.isPending}
        >
          {mutation.isPending ? (
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          ) : null}
          Add this text
        </Button>
      </div>
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
