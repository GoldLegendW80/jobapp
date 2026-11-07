import { useState } from "react";

import {
  MAX,
  listAt,
  type Path,
} from "@/features/documents/helpers/edit-content";
import { textOf, type Form } from "@/features/documents/helpers/edit-form";
import { TextField } from "../edit-fields";

const split = (line: string) => line.split(",").map((one) => one.trim());
const kept = (list: string[]) =>
  JSON.stringify(list.map((one) => one.trim()).filter((one) => one !== ""));

/** A list of terms typed as one line; the line keeps what is typed until the list changes elsewhere. */
export function TermsField({ form, path }: { form: Form; path: Path }) {
  const items = listAt(form.draft, path).map(textOf);
  const key = JSON.stringify(items);
  const [typed, setTyped] = useState({ line: items.join(", "), key });
  // The list changed outside this line (a restore, another tab): adjust the line during render.
  if (typed.key !== key) {
    const same = kept(split(typed.line)) === kept(items);
    setTyped({ line: same ? typed.line : items.join(", "), key });
  }
  return (
    <TextField
      form={form}
      path={path}
      label="Terms, separated by commas"
      max={MAX.skillItems * (MAX.term + 2)}
      value={typed.line}
      onText={(line) => {
        const next = split(line);
        setTyped({ line, key: JSON.stringify(next) });
        form.set(path, next);
      }}
    />
  );
}
