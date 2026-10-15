import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import type { KeyboardEvent, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  characters,
  listAt,
  pathKey,
  softLimit,
  textAt,
  type Path,
} from "./edit-content";
import { inputId, useListMoves, type Form } from "./edit-lists";

function counter(count: number, soft: number | null): string {
  const unit = count === 1 ? "character" : "characters";
  if (soft === null) return `${String(count)} ${unit}`;
  const line = `${String(count)} of about ${String(soft)} characters`;
  return count > soft ? `${line}, longer than this template suggests` : line;
}

export function TextField({
  form,
  path,
  label,
  max,
  multiline = false,
  hideLabel = false,
  hint,
  inputMode,
  onKeyDown,
  value: shown,
  onText,
}: {
  form: Form;
  path: Path;
  label: string;
  max: number;
  multiline?: boolean;
  hideLabel?: boolean;
  hint?: string;
  inputMode?: "numeric";
  onKeyDown?: (event: KeyboardEvent) => void;
  /** A field that keeps its own text (a list typed as one line) gives it here. */
  value?: string;
  onText?: (text: string) => void;
}) {
  const key = pathKey(path);
  const id = inputId(form, path);
  const value = shown ?? textAt(form.draft, path);
  const notes = form.notes.get(key) ?? [];
  const error = form.errors.get(key);
  const soft = softLimit(form.limits, path);
  const count = characters(value);
  const described = [
    `${id}-count`,
    hint === undefined ? "" : `${id}-hint`,
    notes.length === 0 ? "" : `${id}-notes`,
    error === undefined ? "" : `${id}-error`,
  ]
    .filter((one) => one !== "")
    .join(" ");
  const props = {
    id,
    value,
    maxLength: max,
    inputMode,
    onKeyDown,
    "aria-describedby": described,
    "aria-invalid": error === undefined ? undefined : true,
    onChange: (event: { target: { value: string } }) => {
      if (onText) onText(event.target.value);
      else form.set(path, event.target.value);
    },
  };
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <Label htmlFor={id} className={cn(hideLabel && "sr-only")}>
        {label}
      </Label>
      {multiline ? <Textarea {...props} rows={3} /> : <Input {...props} />}
      <p
        id={`${id}-count`}
        className={cn(
          "text-xs text-muted-foreground",
          soft !== null &&
            count > soft &&
            "font-medium text-amber-700 dark:text-amber-400",
        )}
      >
        {counter(count, soft)}
      </p>
      {hint === undefined ? null : (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {notes.length === 0 ? null : (
        <ul
          id={`${id}-notes`}
          aria-label={`Notes on ${label}`}
          className="flex flex-col gap-0.5 text-xs text-amber-700 dark:text-amber-400"
        >
          {notes.map((note, index) => (
            <li key={index}>
              {note.sentence}
              {note.excerpt === "" ? "" : `: “${note.excerpt}”`}
            </li>
          ))}
        </ul>
      )}
      {error === undefined ? null : (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** Up, down and remove for the entry `index` of a list; `what` names it for a screen reader. */
export function EntryButtons({
  id,
  what,
  index,
  count,
  onMove,
  onRemove,
}: {
  id: string;
  what: string;
  index: number;
  count: number;
  onMove: (to: number, button: "up" | "down") => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center">
      <Button
        id={`${id}-up`}
        type="button"
        variant="ghost"
        size="icon"
        aria-label={`Move ${what} up`}
        disabled={index === 0}
        onClick={() => {
          onMove(index - 1, "up");
        }}
      >
        <ArrowUp aria-hidden="true" />
      </Button>
      <Button
        id={`${id}-down`}
        type="button"
        variant="ghost"
        size="icon"
        aria-label={`Move ${what} down`}
        disabled={index === count - 1}
        onClick={() => {
          onMove(index + 1, "down");
        }}
      >
        <ArrowDown aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={`Remove ${what}`}
        onClick={onRemove}
      >
        <Trash2 aria-hidden="true" />
      </Button>
    </div>
  );
}

/** A list of texts (highlights, bullets, paragraphs): each its own field, reordered by keyboard. */
export function ListField({
  form,
  path,
  legend,
  noun,
  context,
  max,
  itemMax,
  multiline = false,
  children,
}: {
  form: Form;
  path: Path;
  legend: string;
  /** One entry, "Bullet". */
  noun: string;
  /** Said after the entry's number in its label: "Bullet 2, Example Corp". */
  context?: string;
  max: number;
  itemMax: number;
  multiline?: boolean;
  children?: ReactNode;
}) {
  const items = listAt(form.draft, path);
  const moves = useListMoves(form, path, noun);
  const base = inputId(form, path);
  const lower = noun.toLowerCase();
  const of = context === undefined ? "" : ` of ${context}`;
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="text-sm font-medium">{legend}</legend>
      {children}
      {items.length === 0 ? null : (
        <ol className="flex flex-col gap-3">
          {items.map((_, index) => (
            <li key={index} className="flex items-start gap-1">
              <TextField
                form={form}
                path={[...path, index]}
                label={`${noun} ${String(index + 1)}${context === undefined ? "" : `, ${context}`}`}
                hideLabel
                max={itemMax}
                multiline={multiline}
                onKeyDown={moves.keys(index)}
              />
              <EntryButtons
                id={`${base}-${String(index)}`}
                what={`${lower} ${String(index + 1)}${of}`}
                index={index}
                count={items.length}
                onMove={(to, button) => {
                  moves.move(index, to, button);
                }}
                onRemove={() => {
                  moves.remove(index);
                }}
              />
            </li>
          ))}
        </ol>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          id={`${base}-add`}
          type="button"
          variant="outline"
          size="sm"
          disabled={items.length >= max}
          onClick={() => {
            form.set(path, [...items, ""]);
            moves.focus(`${base}-${String(items.length)}`);
          }}
        >
          <Plus aria-hidden="true" />
          Add {/^[aeiou]/.test(lower) ? "an" : "a"} {lower}
          {context === undefined ? "" : <span className="sr-only">{of}</span>}
        </Button>
        {items.length >= max ? (
          <span className="text-xs text-muted-foreground">At most {max}.</span>
        ) : items.length > 1 ? (
          <span className="text-xs text-muted-foreground">
            Alt with an arrow key moves the {lower} you are in.
          </span>
        ) : null}
      </div>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </fieldset>
  );
}
