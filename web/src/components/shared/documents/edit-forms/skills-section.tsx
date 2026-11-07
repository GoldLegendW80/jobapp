import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FieldLegend, FieldSet } from "@/components/ui/field";

import { useListMoves } from "@/features/documents/hooks/use-list-moves";
import { MAX, listAt } from "@/features/documents/helpers/edit-content";
import { inputId, type Form } from "@/features/documents/helpers/edit-form";
import { EntryButtons, TextField } from "../edit-fields";
import { TermsField } from "./terms-field";

/** The CV's skills: groups of terms, each with its label. Drawn where the page shows them. */
export function SkillsSection({ form }: { form: Form }) {
  const path = ["skills"] as const;
  const groups = listAt(form.draft, path);
  const moves = useListMoves(form, path, "Group");
  const base = inputId(form, path);
  return (
    <>
      {groups.map((_, index) => (
        <FieldSet key={index} className="min-w-0 gap-2 border-t pt-3">
          <FieldLegend variant="label" className="mb-0">
            Group {index + 1}
          </FieldLegend>
          <div className="flex justify-end">
            <EntryButtons
              id={`${base}-${String(index)}`}
              what={`group ${String(index + 1)}`}
              index={index}
              count={groups.length}
              onMove={(to, button) => {
                moves.move(index, to, button);
              }}
              onRemove={() => {
                moves.remove(index);
              }}
            />
          </div>
          <TextField
            form={form}
            path={[...path, index, "label"]}
            label="Label"
            max={MAX.term}
          />
          <TermsField form={form} path={[...path, index, "items"]} />
        </FieldSet>
      ))}
      <div>
        <Button
          id={`${base}-add`}
          type="button"
          variant="outline"
          size="sm"
          disabled={groups.length >= MAX.skills}
          onClick={() => {
            form.set(path, [...groups, { label: "", items: [] }]);
            moves.focus(`${base}-${String(groups.length)}-label`);
          }}
        >
          <Plus aria-hidden="true" />
          Add a group
        </Button>
      </div>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </>
  );
}
