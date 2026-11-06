import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";

import type { Answer } from "@/features/apply/api/apply-api";
import { editorKind, valueText } from "@/features/apply/helpers/apply-state";

const MAX_CHARS = 5000;

/**
 * Type or correct one answer: two buttons for a yes or no, the field's own choices as a select,
 * a text box otherwise. A yes or no and a choice save at once; text saves with its button, and
 * says so while it is unsaved so that the page can keep Approve waiting for it.
 *
 * `remember` offers the box "Remember this answer for next forms", ticked ("on") or not ("off"),
 * placed before the click that saves; `onSave` receives whether it is ticked.
 */
export function AnswerEditor({
  answer,
  saving,
  onSave,
  onDirtyChange,
  remember = null,
}: {
  answer: Answer;
  saving: boolean;
  onSave: (value: string | boolean, remember: boolean) => Promise<boolean>;
  onDirtyChange: (dirty: boolean) => void;
  remember?: "on" | "off" | null;
}) {
  const kind = editorKind(answer);
  const boxId = useId();
  const [keep, setKeep] = useState(remember === "on");
  const box =
    remember === null ? null : (
      <div className="flex items-start gap-2">
        <Checkbox
          id={boxId}
          className="mt-0.5"
          checked={keep}
          disabled={saving}
          onCheckedChange={(checked) => {
            setKeep(checked === true);
          }}
        />
        <Label htmlFor={boxId} className="leading-snug font-normal">
          Remember this answer for next forms
        </Label>
      </div>
    );
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
      <div className="flex flex-col gap-2">
        {box}
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
                void onSave(choice, keep);
              }}
            >
              {choice ? "Yes" : "No"}
            </Button>
          ))}
        </div>
      </div>
    );
  }
  if (kind === "select") {
    return (
      <div className="flex flex-col gap-2">
        {box}
        <NativeSelect
          aria-label={name}
          value={saved}
          disabled={saving}
          onChange={(event) => {
            if (event.target.value !== "")
              void onSave(event.target.value, keep);
          }}
        >
          <option value="">Choose one</option>
          {(answer.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </NativeSelect>
      </div>
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
      {box}
      <Button
        type="button"
        size="sm"
        className="w-full sm:w-fit"
        disabled={
          saving || !dirty || draft.trim() === "" || hasControl || tooLong
        }
        onClick={() => {
          void onSave(documentLetter ? draft : draft.trim(), keep);
        }}
      >
        {saving ? <Spinner aria-hidden="true" role={undefined} /> : null}
        Save answer
      </Button>
    </div>
  );
}
