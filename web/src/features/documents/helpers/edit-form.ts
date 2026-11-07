import {
  pathKey,
  type Draft,
  type Note,
  type Path,
  type SoftLimits,
} from "./edit-content";
/** What every field of the Edit view reads and writes. */
export interface Form {
  /** The prefix of the ids of this form's inputs. */
  id: string;
  draft: Draft;
  set: (path: Path, value: unknown) => void;
  notes: Map<string, Note[]>;
  /** What the api refused at the last save, by path. */
  errors: Map<string, string>;
  limits: SoftLimits | undefined;
  /** Bumped when the draft is replaced from outside, so inputs with their own text start again. */
  round?: number;
}

export const inputId = (form: Form, path: Path): string =>
  `${form.id}-${pathKey(path).replace(/\./g, "-")}`;

/** A text value of the draft; anything else (absent, null, a list) reads as empty. */
export const textOf = (value: unknown): string =>
  typeof value === "string" ? value : "";

/** The notes and the refusals of the fields under `paths`, in words, for the text shown closed. */
export function problemsOf(form: Form | null, paths: Path[]): string[] {
  if (form === null) return [];
  const prefixes = paths.map(pathKey);
  const under = (key: string) =>
    key !== "" &&
    prefixes.some((one) => key === one || key.startsWith(`${one}.`));
  const out: string[] = [];
  for (const [key, notes] of form.notes)
    if (under(key))
      for (const note of notes)
        out.push(
          note.excerpt === ""
            ? note.sentence
            : `${note.sentence}: “${note.excerpt}”`,
        );
  for (const [key, sentence] of form.errors) if (under(key)) out.push(sentence);
  return out;
}
