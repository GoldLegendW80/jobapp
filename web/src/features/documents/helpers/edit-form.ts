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
}

export const inputId = (form: Form, path: Path): string =>
  `${form.id}-${pathKey(path).replace(/\./g, "-")}`;

/** A text value of the draft; anything else (absent, null, a list) reads as empty. */
export const textOf = (value: unknown): string =>
  typeof value === "string" ? value : "";
