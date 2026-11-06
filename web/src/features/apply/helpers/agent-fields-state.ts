import type { FilledField } from "../api/apply-api";

/** The steps in the order the assistant first filled a field on each, with their fields. */
export function byStep(fields: readonly FilledField[]) {
  const steps = new Map<string, FilledField[]>();
  for (const field of fields) {
    const list = steps.get(field.step) ?? [];
    list.push(field);
    steps.set(field.step, list);
  }
  return [...steps.entries()];
}
