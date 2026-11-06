import type { Form } from "@/features/documents/helpers/edit-form";
import { TextField } from "../edit-fields";

/** A line of the form: the field, its label, its hard limit, one line or several. */
export type FormLine = [
  field: string,
  label: string,
  max: number,
  multiline?: boolean,
];

/** Text fields at the top level of the draft, one per line given. */
export function FormLines({ form, lines }: { form: Form; lines: FormLine[] }) {
  return lines.map(([field, label, max, multiline]) => (
    <TextField
      key={field}
      form={form}
      path={[field]}
      label={label}
      max={max}
      multiline={multiline}
    />
  ));
}
