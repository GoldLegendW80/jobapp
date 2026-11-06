import { TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { errorDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

import { AnswerEditor } from "./answer-editor";
import { type Answer } from "@/features/apply/api/apply-api";
import { useRememberAnswer } from "@/features/apply/api/use-answer-bank";
import {
  isEditable,
  isOpenField,
  leftFields,
  REMEMBERED,
  sourceLabel,
  valueText,
} from "@/features/apply/helpers/apply-state";

/**
 * The fields of the filled form: label, value and where the value came from. A field the app left
 * is the person's to answer: it sits in an amber frame with the words "You answer" and its input
 * open, and a banner above the list names how many there are, so none can be missed. Any other
 * answer can be corrected with Edit. A field the api takes no value for (a file, a list of
 * choices, a skipped widget) has no input: it keeps the frame and the banner line, and is
 * answered on the site. A value is always shown whole. One column on a phone.
 *
 * With `applicationId`, a field left for the person that the api offers to remember shows the
 * box "Remember this answer for next forms" (ticked unless the question names the company):
 * once the answer is saved on this form, a ticked box saves it to Saved answers too, and the
 * row says what happened.
 */
export function FieldsList({
  answers,
  editable,
  saving,
  onSave,
  onDirtyChange,
  applicationId,
}: {
  answers: readonly Answer[];
  editable: boolean;
  saving: boolean;
  onSave: (answer: Answer, value: string | boolean) => Promise<boolean>;
  onDirtyChange: (count: number) => void;
  applicationId?: string;
}) {
  const left = leftFields(answers);
  const [dirty, setDirty] = useState<ReadonlySet<string>>(new Set());
  const mark = useCallback((key: string, isDirty: boolean) => {
    setDirty((current) => {
      if (current.has(key) === isDirty) return current;
      const next = new Set(current);
      if (isDirty) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  useEffect(() => {
    onDirtyChange(dirty.size);
  }, [dirty, onDirtyChange]);
  return (
    <div className="flex flex-col gap-3">
      {left.length > 0 ? (
        <Alert variant="warning">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle className="font-semibold">
            {left.length === 1
              ? "1 field is left for you"
              : `${left.length} fields are left for you`}
          </AlertTitle>
          <AlertDescription className="text-foreground">
            <p>
              The assistant did not answer {left.length === 1 ? "it" : "them"}.
              Type the answers below. A required one must be answered before you
              can approve; an optional one is sent empty. One marked "answer on
              the site" cannot be typed here.
            </p>
            <ul className="list-disc pl-5">
              {left.map((answer) => (
                <li key={answer.field_key}>
                  {answer.label}
                  {answer.required ? " (required)" : " (optional)"}
                  {isEditable(answer) ? "" : ", answer on the site"}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
      <ul aria-label="Fields filled" className="flex flex-col gap-2">
        {answers.map((answer) => (
          <FieldRow
            key={answer.field_key}
            answer={answer}
            editable={editable}
            saving={saving}
            onSave={(value) => onSave(answer, value)}
            mark={mark}
            applicationId={applicationId}
          />
        ))}
      </ul>
    </div>
  );
}

function FieldRow({
  answer,
  editable,
  saving,
  onSave,
  mark,
  applicationId,
}: {
  answer: Answer;
  editable: boolean;
  saving: boolean;
  onSave: (value: string | boolean) => Promise<boolean>;
  mark: (key: string, dirty: boolean) => void;
  applicationId?: string;
}) {
  const [remembered, setRemembered] = useState<string | null>(null);
  const rememberAnswer = useRememberAnswer();
  const key = answer.field_key;
  const onDirtyChange = useCallback(
    (isDirty: boolean) => {
      mark(key, isDirty);
    },
    [mark, key],
  );
  const open = isOpenField(answer);
  const typed = isEditable(answer);
  const [editing, setEditing] = useState(false);
  const text = valueText(answer.value);
  const typing = editable && typed && (open || editing);
  const reason = open
    ? {
        letter_too_long:
          "The letter has more than 10000 characters, so the assistant left this box empty. Shorten it here or answer on the site.",
        letter_control_character:
          "The letter contains a tab, carriage return or another control character the browser cannot type, so the assistant left this box empty. Correct it here or answer on the site.",
      }[answer.left_reason ?? ""]
    : undefined;
  return (
    <li
      data-left={open ? "true" : undefined}
      className={cn(
        "flex min-w-0 flex-col gap-1 rounded-md border p-3 text-sm sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] sm:items-start sm:gap-4",
        open && "border-2 border-warning bg-warning-muted",
      )}
    >
      <span className="min-w-0 font-medium [overflow-wrap:anywhere]">
        {answer.label}
        {answer.required ? (
          <span className="text-muted-foreground"> (required)</span>
        ) : null}
      </span>
      {reason ? (
        <p className="text-foreground sm:col-span-3">{reason}</p>
      ) : null}
      {typing ? (
        <AnswerEditor
          key={`${answer.source}:${text}`}
          answer={answer}
          saving={saving}
          remember={
            applicationId !== undefined && open
              ? (answer.remember ?? null)
              : null
          }
          onSave={async (value, remember) => {
            setRemembered(null);
            const saved = await onSave(value);
            if (saved) setEditing(false);
            if (saved && remember && applicationId !== undefined) {
              try {
                const done = await rememberAnswer(
                  applicationId,
                  answer.field_key,
                  value,
                );
                setRemembered(REMEMBERED[done.outcome]);
              } catch (error) {
                setRemembered(
                  `Saved on this form only. ${errorDetail(error) ?? "It could not be saved for next forms."}`,
                );
              }
            }
            return saved;
          }}
          onDirtyChange={onDirtyChange}
        />
      ) : open && !typed ? (
        <span className="text-muted-foreground">
          Not filled by the assistant.{" "}
          {answer.required
            ? "It cannot be answered here: answer it on the site, or reject this form."
            : "Answer it on the site, or leave it empty."}
        </span>
      ) : (
        <span
          className={cn(
            "min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]",
            text === "" && "text-muted-foreground",
          )}
        >
          {text === "" ? "Left empty" : text}
        </span>
      )}
      {remembered ? (
        <p
          role="status"
          className="text-xs text-muted-foreground sm:col-span-3"
        >
          {remembered}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        {open ? (
          <Badge className="w-fit bg-warning text-warning-foreground hover:bg-warning">
            You answer
          </Badge>
        ) : (
          <Badge variant="secondary" className="w-fit">
            {sourceLabel(answer.source === "left" ? "person" : answer.source)}
          </Badge>
        )}
        {editable && typed && !open ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`${editing ? "Stop editing" : "Edit"}: ${answer.label}`}
            onClick={() => {
              setEditing(!editing);
            }}
          >
            {editing ? "Done" : "Edit"}
          </Button>
        ) : null}
      </div>
    </li>
  );
}
