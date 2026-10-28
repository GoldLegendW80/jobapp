/**
 * A term list as chips: the terms of `<name>.terms`, an input that adds one (Enter or the Add
 * button), a remove button per chip, and the "advanced (regular expressions)" switch of
 * `<name>.advanced_regex`. A term is checked as it is added (matching.ts `termProblem`, the
 * server's rules) and refused with a message under the input; a duplicate (ignoring case) is
 * refused too. A chip that breaks a rule after the switch changes, or that the server refused
 * on save (a regex error with its position, errors.ts), shows its message under the chip.
 *
 * A list with no switch (`regexSwitch={false}`: the search lists, search-lists.tsx, where the
 * person types words only, owner 2026-10-28) keeps the mode it was saved in and says under its
 * chips when it holds regular expressions an earlier version saved.
 */
import { CircleAlert, Plus, X } from "lucide-react";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  get,
  useController,
  type FieldValues,
  type Path,
  type UseFormReturn,
} from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import { TERMS_MAX } from "./editor-forms";
import { listProblems, termProblem } from "./matching";

interface TermChipsProps<F extends FieldValues> {
  form: UseFormReturn<F>;
  /** path of the term list object (`{advanced_regex, terms}`) */
  name: string;
  /** what the list is, for headings and screen readers ("Job titles") */
  label: string;
  /** shown as the heading; defaults to `label` */
  title?: ReactNode;
  description?: ReactNode;
  placeholder?: string;
  /** smaller heading, for lists nested in a card */
  compact?: boolean;
  /** false: no regular expression switch; the list keeps the mode it was saved in */
  regexSwitch?: boolean;
}

export function TermChips<F extends FieldValues>({
  form,
  name,
  label,
  title,
  description,
  placeholder,
  compact = false,
  regexSwitch = true,
}: TermChipsProps<F>) {
  const id = useId();
  const terms = useController({
    control: form.control,
    name: `${name}.terms` as Path<F>,
  });
  const advanced = useController({
    control: form.control,
    name: `${name}.advanced_regex` as Path<F>,
  });
  const values = (terms.field.value ?? []) as string[];
  const isAdvanced = advanced.field.value === true;
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string>();
  // A switch elsewhere changes the mode too: what was said about the draft no longer holds.
  const [modeSeen, setModeSeen] = useState(isAdvanced);
  if (modeSeen !== isAdvanced) {
    setModeSeen(isAdvanced);
    setProblem(undefined);
  }
  const errors = get(form.formState.errors, `${name}.terms`) as
    | (Record<number, { message?: string } | undefined> & {
        message?: string;
        root?: { message?: string };
      })
    | undefined;
  const listError = errors?.root?.message ?? errors?.message;
  const revalidate = () => {
    if (form.formState.isSubmitted)
      void form.trigger(`${name}.terms` as Path<F>);
  };

  const add = () => {
    const term = draft.normalize("NFC").trim();
    const found =
      termProblem(term, isAdvanced) ??
      listProblems([...values, term], isAdvanced).get(values.length);
    if (found) {
      setProblem(found);
      return;
    }
    const key = term.toLocaleLowerCase("en");
    if (values.some((t) => t.toLocaleLowerCase("en") === key)) {
      setProblem("This term is already in the list.");
      return;
    }
    if (values.length >= TERMS_MAX) {
      setProblem(`Keep at most ${TERMS_MAX} terms.`);
      return;
    }
    terms.field.onChange([...values, term]);
    setDraft("");
    setProblem(undefined);
    revalidate();
  };

  const remove = (index: number) => {
    terms.field.onChange(values.filter((_, i) => i !== index));
    // Errors are kept by index: clear them so none lands on the next chip.
    form.clearErrors(`${name}.terms` as Path<F>);
    revalidate();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  };

  const Heading = compact ? "h5" : "h4";
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <Heading
          id={`${id}-title`}
          className={cn("font-medium", compact ? "text-xs" : "text-sm")}
        >
          {title ?? label}
        </Heading>
        {regexSwitch ? (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              role="switch"
              aria-checked={isAdvanced}
              aria-label={`Advanced (regular expressions) for ${label}`}
              className="size-4 accent-primary"
              checked={isAdvanced}
              onChange={(event) => {
                advanced.field.onChange(event.target.checked);
                setProblem(undefined);
                revalidate();
              }}
            />
            Advanced (regular expressions)
          </label>
        ) : null}
      </div>
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}
      {!regexSwitch && isAdvanced ? (
        <p className="text-xs text-muted-foreground">
          {WORDING.listUsesRegularExpressions}
        </p>
      ) : null}
      {values.length === 0 ? (
        <p className="text-sm text-muted-foreground">No term yet.</p>
      ) : (
        <ul aria-label={`Terms of ${label}`} className="flex flex-wrap gap-1.5">
          {values.map((term, index) => {
            const message =
              errors?.[index]?.message ?? termProblem(term, isAdvanced);
            const messageId = `${id}-term-${index}`;
            return (
              <li key={`${term}-${index}`} className="flex max-w-full flex-col">
                <span
                  aria-invalid={message ? true : undefined}
                  aria-describedby={message ? messageId : undefined}
                  className={cn(
                    "inline-flex max-w-full items-center gap-1 rounded-full border bg-secondary py-0.5 pr-0.5 pl-2.5 text-xs text-secondary-foreground",
                    isAdvanced && "font-mono",
                    message &&
                      "border-destructive bg-destructive/10 text-destructive",
                  )}
                >
                  <span className="min-w-0 truncate" title={term}>
                    {term}
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${term}`}
                    className="rounded-full p-0.5 hover:bg-foreground/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    onClick={() => {
                      remove(index);
                    }}
                  >
                    <X aria-hidden="true" className="size-3" />
                  </button>
                </span>
                {message ? (
                  <span
                    id={messageId}
                    role="alert"
                    className="mt-0.5 flex max-w-80 items-start gap-1 text-xs text-destructive"
                  >
                    <CircleAlert
                      aria-hidden="true"
                      className="mt-0.5 size-3 shrink-0"
                    />
                    {message}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex gap-2">
        <Input
          aria-label={`New term for ${label}`}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-problem` : undefined}
          value={draft}
          placeholder={
            placeholder ??
            (isAdvanced ? "A regular expression" : "A word or phrase")
          }
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          className={cn("h-8", isAdvanced && "font-mono")}
          onChange={(event) => {
            setDraft(event.target.value);
            if (problem) setProblem(undefined);
          }}
          onKeyDown={onKeyDown}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={`Add term to ${label}`}
          onClick={add}
        >
          <Plus aria-hidden="true" />
          Add
        </Button>
      </div>
      {problem ? (
        <p
          id={`${id}-problem`}
          role="alert"
          className="text-xs text-destructive"
        >
          {problem}
        </p>
      ) : null}
      {listError ? (
        <p role="alert" className="text-xs text-destructive">
          {listError}
        </p>
      ) : null}
    </div>
  );
}
