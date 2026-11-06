import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FieldLegend, FieldSet } from "@/components/ui/field";

import { useListMoves } from "@/features/documents/hooks/use-list-moves";
import { MAX, listAt } from "@/features/documents/helpers/edit-content";
import {
  inputId,
  textOf,
  type Form,
} from "@/features/documents/helpers/edit-form";
import {
  employerKey,
  type FixedList as List,
} from "@/features/documents/helpers/fixed-facts";
import { EntryButtons, TextField } from "../edit-fields";

/** A list of the base CV's `fixed` block: one small form per entry. */
export function FixedList({ form, list }: { form: Form; list: List }) {
  const path = ["fixed", list.name] as const;
  const rows = listAt(form.draft, path);
  const moves = useListMoves(form, path, list.noun);
  const base = inputId(form, path);
  const lower = list.noun.toLowerCase();
  const setOrganisation = (index: number, value: string) => {
    const used = new Set(
      listAt(form.draft, ["experience"]).map((one) =>
        textOf((one as { employer_key?: unknown }).employer_key),
      ),
    );
    const row = rows[index] as Record<string, unknown>;
    form.set([...path, index], {
      ...row,
      organisation: value,
      key: employerKey(rows, index, value, used),
    });
  };
  return (
    <FieldSet className="min-w-0 gap-3">
      <FieldLegend variant="label" className="mb-0">
        {list.title}
      </FieldLegend>
      {rows.map((_, index) => (
        <div key={index} className="flex flex-col gap-2 border-t pt-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">
              {list.noun} {index + 1}
            </span>
            <EntryButtons
              id={`${base}-${String(index)}`}
              what={`${lower} ${String(index + 1)}`}
              index={index}
              count={rows.length}
              onMove={(to, button) => {
                moves.move(index, to, button);
              }}
              onRemove={() => {
                moves.remove(index);
              }}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {list.columns.map((column) => (
              <TextField
                key={column.name}
                form={form}
                path={[...path, index, column.name]}
                label={`${column.label}, ${lower} ${String(index + 1)}`}
                max={column.period ? 7 : MAX.line}
                inputMode={column.period ? "numeric" : undefined}
                hint={
                  column.period
                    ? "A year or a month: 2021 or 2021-03."
                    : undefined
                }
                onText={
                  column.name === "organisation"
                    ? (typed) => {
                        setOrganisation(index, typed);
                      }
                    : undefined
                }
              />
            ))}
          </div>
        </div>
      ))}
      <div>
        <Button
          id={`${base}-add`}
          type="button"
          variant="outline"
          size="sm"
          disabled={rows.length >= list.max}
          onClick={() => {
            const empty = Object.fromEntries(
              list.columns.map((column) => [
                column.name,
                column.period ? null : "",
              ]),
            );
            form.set(path, [
              ...rows,
              list.name === "employers" ? { key: "", ...empty } : empty,
            ]);
            moves.focus(
              `${base}-${String(rows.length)}-${list.columns[0]?.name ?? ""}`,
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add {/^[aeiou]/.test(lower) ? "an" : "a"} {lower}
        </Button>
      </div>
      <p aria-live="polite" className="sr-only">
        {moves.said}
      </p>
    </FieldSet>
  );
}
