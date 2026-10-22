import { type FormEvent, useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import {
  type Draft,
  draftOf,
  fieldsOf,
  isRecord,
  refusedField,
  valueOf,
} from "./proposal-value";

/**
 * "Edit then accept" of one proposal (task G13): the proposed value as form controls, one per
 * field, so the user corrects what the agent read before it reaches the profile. The server is
 * the judge: the value is validated by its section's model when it is accepted, and what it
 * refuses comes back as one sentence that names the field (`edited_value.start: ...`), shown
 * under that field.
 *
 * A value is either one thing (a line, a text, a number, a list of words) or a record of such
 * things (an employer, a language, a fact). A part that is neither (a nested list of records)
 * is not shown and is sent back as it was.
 *
 * While the call runs the fields are read only (`aria-busy`), not disabled, so the focus stays
 * where it was; a refusal moves it to the field it names.
 */

const HINTS: Record<string, string> = {
  start: "A year, or a year and a month such as 2021-03.",
  end: "A year, or a year and a month such as 2023-06.",
};

export function EditForm({
  title,
  value,
  busy,
  refused,
  onSubmit,
  onCancel,
}: {
  title: string;
  value: unknown;
  busy: boolean;
  /** The sentence of the server's last refusal of this form, if any. */
  refused?: string;
  onSubmit: (value: unknown) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const fields = fieldsOf(value, title);
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() =>
    Object.fromEntries(fields.map((field) => [field.name, draftOf(field)])),
  );
  const named = refused ? refusedField(refused) : undefined;
  const at =
    named && fields.some((field) => field.name === named.name)
      ? named
      : undefined;
  const focused = at ? `${id}-${at.name || "value"}` : undefined;
  useEffect(() => {
    if (focused) document.getElementById(focused)?.focus();
  }, [focused, refused]);
  const set = (name: string, draft: Draft) => {
    if (busy) return;
    setDrafts((current) => ({ ...current, [name]: draft }));
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const edited = fields.map(
      (field) =>
        [field.name, valueOf(field, drafts[field.name] ?? "")] as const,
    );
    onSubmit(
      isRecord(value)
        ? { ...value, ...Object.fromEntries(edited) }
        : edited[0]?.[1],
    );
  };
  return (
    <form
      aria-label={`Edit ${title}`}
      aria-busy={busy || undefined}
      noValidate
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-md border bg-muted/30 p-3"
    >
      {fields.map((field, index) => {
        const fieldId = `${id}-${field.name || "value"}`;
        const problem = at?.name === field.name ? at.message : undefined;
        const hint = HINTS[field.name];
        const described =
          [hint ? `${fieldId}-hint` : "", problem ? `${fieldId}-error` : ""]
            .filter((one) => one !== "")
            .join(" ") || undefined;
        const draft = drafts[field.name] ?? "";
        const shared = {
          id: fieldId,
          readOnly: busy,
          "aria-busy": busy || undefined,
          autoFocus: index === 0,
          "aria-invalid": problem ? true : undefined,
          "aria-describedby": described,
        };
        return (
          <div key={field.name} className="flex flex-col gap-1.5">
            {field.kind === "flag" ? (
              <div className="flex items-center gap-2">
                <input
                  {...shared}
                  type="checkbox"
                  className="size-4"
                  checked={draft === true}
                  onChange={(event) => {
                    set(field.name, event.target.checked);
                  }}
                />
                <Label htmlFor={fieldId}>{field.label}</Label>
              </div>
            ) : (
              <>
                <Label htmlFor={fieldId}>
                  {field.label}
                  {field.kind === "lines" ? (
                    <span className="font-normal text-muted-foreground">
                      {" "}
                      (one per line)
                    </span>
                  ) : null}
                </Label>
                {field.kind === "text" || field.kind === "lines" ? (
                  <Textarea
                    {...shared}
                    value={String(draft)}
                    onChange={(event) => {
                      set(field.name, event.target.value);
                    }}
                  />
                ) : (
                  <Input
                    {...shared}
                    type="text"
                    inputMode={field.kind === "number" ? "decimal" : undefined}
                    value={String(draft)}
                    onChange={(event) => {
                      set(field.name, event.target.value);
                    }}
                  />
                )}
              </>
            )}
            {hint ? (
              <p
                id={`${fieldId}-hint`}
                className="text-xs text-muted-foreground"
              >
                {hint}
              </p>
            ) : null}
            {problem ? (
              <p
                id={`${fieldId}-error`}
                role="alert"
                className="text-sm text-destructive"
              >
                {problem}
              </p>
            ) : null}
          </div>
        );
      })}
      {refused && !at ? (
        <p role="alert" className="text-sm text-destructive">
          {named?.message ?? refused}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy}>
          Accept with my changes
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
