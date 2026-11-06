import { Plus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";

import { useListMoves } from "@/features/documents/hooks/use-list-moves";
import {
  MAX,
  listAt,
  span,
  type EmployerFact,
} from "@/features/documents/helpers/edit-content";
import {
  inputId,
  textOf,
  type Form,
} from "@/features/documents/helpers/edit-form";
import { EntryButtons, ListField } from "../edit-fields";
import { FormSection } from "./form-section";

/** The CV's experience: one entry per employer of the profile, whose bullets the person writes. */
export function ExperienceSection({
  form,
  employers,
  known,
}: {
  form: Form;
  employers: Map<string, EmployerFact>;
  /** The profile (and the base CV) are read: an employer missing from them is said. */
  known: boolean;
}) {
  const path = ["experience"] as const;
  const entries = listAt(form.draft, path);
  const keyOf = (index: number) =>
    textOf(
      (entries[index] as { employer_key?: unknown } | undefined)?.employer_key,
    );
  const used = new Set(entries.map((_, index) => keyOf(index)));
  const free = [...employers.values()].filter((one) => !used.has(one.key));
  const [adding, setAdding] = useState("");
  const chosen = free.some((one) => one.key === adding)
    ? adding
    : (free[0]?.key ?? "");
  const moves = useListMoves(form, path, "Employer");
  const base = inputId(form, path);
  const selectId = `${base}-choice`;
  return (
    <FormSection title="Experience">
      <p className="text-sm text-muted-foreground">
        Each employer&apos;s name, title and dates come from your profile; you
        write the bullets.
      </p>
      {entries.map((_, index) => {
        const key = keyOf(index);
        const fact = employers.get(key);
        const name = fact?.organisation ?? key;
        return (
          <FieldSet key={index} className="min-w-0 gap-2 border-t pt-3">
            <FieldLegend variant="label" className="mb-0">
              {name}
            </FieldLegend>
            <div className="flex items-start justify-between gap-2">
              {fact === undefined && known ? (
                <p role="note" className="text-sm text-warning">
                  This employer is no longer in your profile, nor in the base
                  CV, so the page has no name, title or dates to print above
                  these bullets. Add it back in your profile, or remove it here:
                  nothing is changed for you.
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {[
                    fact?.title ?? "",
                    span(fact?.start ?? null, fact?.end ?? null),
                  ]
                    .filter((part) => part !== "")
                    .join(", ")}
                </p>
              )}
              <EntryButtons
                id={`${base}-${String(index)}`}
                what={`employer ${name}`}
                index={index}
                count={entries.length}
                onMove={(to, button) => {
                  moves.move(index, to, button);
                }}
                onRemove={() => {
                  moves.remove(index);
                }}
              />
            </div>
            <ListField
              form={form}
              path={[...path, index, "bullets"]}
              legend="Bullets"
              noun="Bullet"
              context={name}
              max={MAX.bullets}
              itemMax={MAX.bullet}
              multiline
            />
          </FieldSet>
        );
      })}
      <Field className="gap-1.5 border-t pt-3">
        <FieldLabel htmlFor={selectId}>
          Add an employer of your profile
        </FieldLabel>
        <div className="flex flex-wrap gap-2">
          <div className="min-w-0 flex-1">
            <NativeSelect
              id={selectId}
              value={chosen}
              disabled={free.length === 0}
              onChange={(event) => {
                setAdding(event.target.value);
              }}
            >
              {free.length === 0 ? (
                <option value="">Every employer is in this CV</option>
              ) : (
                free.map((one) => (
                  <option key={one.key} value={one.key}>
                    {one.organisation}
                  </option>
                ))
              )}
            </NativeSelect>
          </div>
          <Button
            id={`${base}-add`}
            type="button"
            variant="outline"
            disabled={chosen === "" || entries.length >= MAX.employers}
            onClick={() => {
              form.set(path, [
                ...entries,
                { employer_key: chosen, bullets: [] },
              ]);
            }}
          >
            <Plus aria-hidden="true" />
            Add
          </Button>
        </div>
      </Field>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </FormSection>
  );
}
