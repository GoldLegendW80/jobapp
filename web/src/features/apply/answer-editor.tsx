import { LoaderCircle } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";

import type { Answer } from "./apply-api";
import { editorKind, valueText } from "./apply-state";

const MAX_CHARS = 5000;

/**
 * Type or correct one answer: two buttons for a yes or no, the field's own choices as a select,
 * a text box otherwise. A yes or no and a choice save at once; text saves with its button, and
 * says so while it is unsaved so that the page can keep Approve waiting for it.
 */
export function AnswerEditor({
  answer,
  saving,
  onSave,
  onDirtyChange,
}: {
  answer: Answer;
  saving: boolean;
  onSave: (value: string | boolean) => Promise<boolean>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const kind = editorKind(answer);
  const maxChars = answer.max_chars ?? MAX_CHARS;
  const documentLetter = maxChars === 10000;
  const helpId = useId();
  const saved = valueText(answer.value);
  const [draft, setDraft] = useState(saved);
  const dirty = kind !== "yes_no" && kind !== "select" && draft !== saved;
  useEffect(() => {
    onDirtyChange(dirty);
    return () => {
      onDirtyChange(false);
    };
  }, [dirty, onDirtyChange]);
  const name = `Answer for ${answer.label}`;

  if (kind === "yes_no") {
    return (
      <div role="group" aria-label={name} className="flex gap-2">
        {([true, false] as const).map((choice) => (
          <Button
            key={String(choice)}
            type="button"
            size="sm"
            variant={answer.value === choice ? "default" : "outline"}
            aria-pressed={answer.value === choice}
            disabled={saving}
            onClick={() => {
              void onSave(choice);
            }}
          >
            {choice ? "Yes" : "No"}
          </Button>
        ))}
      </div>
    );
  }
  if (kind === "select") {
    return (
      <NativeSelect
        aria-label={name}
        value={saved}
        disabled={saving}
        onChange={(event) => {
          if (event.target.value !== "") void onSave(event.target.value);
        }}
      >
        <option value="">Choose one</option>
        {(answer.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </NativeSelect>
    );
  }
  const Box = kind === "long" || documentLetter ? Textarea : Input;
  const hasControl =
    documentLetter && /\p{Cc}/u.test(draft.replaceAll("\n", ""));
  const tooLong = draft.length > maxChars;
  return (
    <div className="flex flex-col gap-2">
      <Box
        aria-label={name}
        aria-describedby={helpId}
        aria-invalid={hasControl || tooLong}
        value={draft}
        maxLength={maxChars}
        disabled={saving}
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        onPaste={(event) => {
          if (!documentLetter) return;
          event.preventDefault();
          const box = event.currentTarget;
          // Keep the whole paste so an oversized letter gets a visible error, not a cut value.
          setDraft(
            draft.slice(0, box.selectionStart ?? 0) +
              event.clipboardData.getData("text") +
              draft.slice(box.selectionEnd ?? 0),
          );
        }}
      />
      <p id={helpId} className="text-xs text-muted-foreground">
        {draft.length} / {maxChars} characters
        {documentLetter
          ? ". Letter text keeps its spacing and line breaks. Some symbols count as two characters."
          : null}
      </p>
      {hasControl ? (
        <p role="alert">
          Remove tabs, carriage returns and other control characters before
          saving the letter.
        </p>
      ) : null}
      {tooLong ? (
        <p role="alert">
          Shorten this answer to {maxChars} characters before saving.
        </p>
      ) : null}
      <Button
        type="button"
        size="sm"
        className="w-full sm:w-fit"
        disabled={
          saving || !dirty || draft.trim() === "" || hasControl || tooLong
        }
        onClick={() => {
          void onSave(documentLetter ? draft : draft.trim());
        }}
      >
        {saving ? (
          <LoaderCircle aria-hidden="true" className="animate-spin" />
        ) : null}
        Save answer
      </Button>
    </div>
  );
}
