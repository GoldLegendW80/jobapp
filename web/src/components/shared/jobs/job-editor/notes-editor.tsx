import { CircleAlert } from "lucide-react";
import { useId } from "react";

import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import { NOTES_MAX, type Save } from "@/features/jobs/api/use-job-editor";
import {
  useNotesAutosave,
  type NotesState,
} from "@/features/jobs/hooks/use-notes-autosave";

const STATE_WORDS: Record<NotesState, string> = {
  idle: "",
  dirty: "Unsaved changes",
  saving: "Saving",
  saved: "Saved",
  error: "",
};

/** The posting's notes, saved as the person types (use-notes-autosave.ts). */
export function NotesEditor({ job, save }: { job: JobDetail; save: Save }) {
  const id = useId();
  const notes = useNotesAutosave(job, save);

  return (
    <Section
      title="Notes"
      actions={
        <p
          role="status"
          aria-live="polite"
          className="flex items-center gap-1 text-xs text-muted-foreground"
          data-testid="notes-status"
        >
          {notes.state === "saving" ? (
            <Spinner aria-hidden="true" role={undefined} className="size-3" />
          ) : null}
          {STATE_WORDS[notes.state]}
        </p>
      }
    >
      <Card>
        <CardContent className="flex flex-col gap-3">
          <Field className="gap-2">
            <FieldLabel htmlFor={id} className="sr-only">
              Notes
            </FieldLabel>
            <Textarea
              id={id}
              value={notes.draft}
              rows={5}
              placeholder="What you learnt, who you talked to, what comes next."
              aria-invalid={notes.tooLong || notes.state === "error"}
              aria-describedby={`${id}-help`}
              onChange={(event) => {
                notes.edit(event.target.value);
              }}
              onBlur={notes.flush}
              className="min-h-28 text-sm"
            />
            <FieldDescription id={`${id}-help`} className="text-xs leading-4">
              {notes.tooLong
                ? `Too long: ${notes.draft.length.toLocaleString("en-GB")} characters, at most ${NOTES_MAX.toLocaleString("en-GB")}.`
                : "Saved automatically as you type."}
            </FieldDescription>
          </Field>
          {notes.state === "error" ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription className="flex flex-wrap items-center gap-3">
                <span>{notes.error}</span>
                <Button size="sm" variant="outline" onClick={notes.retry}>
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
