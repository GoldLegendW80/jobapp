import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";

import type { IngestionDocument } from "@/features/ingestion/api/documents-api";
import {
  kindLabel,
  sizeLabel,
  statusView,
} from "@/features/ingestion/helpers/labels";
import { canBeChosen } from "@/features/ingestion/helpers/run-selection";
import { TWO_LINE_LABEL } from "@/features/ingestion/helpers/two-line-label";

/** The documents a run may read, a box each, with "Select all" and how many are selected. */
export function DocumentChoice({
  items,
  selected,
  onToggle,
  onChooseAll,
}: {
  items: IngestionDocument[];
  selected: readonly string[];
  onToggle: (id: string, on: boolean) => void;
  /** Selects every document that can be read, or none: the order is the list's. */
  onChooseAll: (ids: string[]) => void;
}) {
  const boxes = useId();
  const selectable = items.filter(canBeChosen);
  const everything = selected.length === selectable.length;
  return (
    <>
      <FieldSet className="gap-2">
        <FieldLegend className="sr-only">Documents to read</FieldLegend>
        <ul className="flex flex-col divide-y rounded-lg border">
          {items.map((one) => (
            <li key={one.id} className="min-w-0 px-3 py-2">
              <Field
                orientation="horizontal"
                className="min-w-0 items-start"
                data-disabled={!canBeChosen(one) || undefined}
              >
                <Checkbox
                  id={`${boxes}-${one.id}`}
                  className="mt-0.5"
                  checked={selected.includes(one.id)}
                  disabled={!canBeChosen(one)}
                  onCheckedChange={(state) => {
                    onToggle(one.id, state === true);
                  }}
                />
                <FieldLabel
                  htmlFor={`${boxes}-${one.id}`}
                  className={TWO_LINE_LABEL}
                >
                  <span className="font-medium break-words">{one.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {kindLabel(one)}, {sizeLabel(one.size)}
                    {canBeChosen(one)
                      ? null
                      : `. ${statusView(one).label}: it cannot be read yet`}
                  </span>
                </FieldLabel>
              </Field>
            </li>
          ))}
        </ul>
      </FieldSet>
      <div className="flex flex-wrap items-center gap-2">
        {selectable.length > 1 ? (
          <Button
            variant="outline"
            onClick={() => {
              onChooseAll(everything ? [] : selectable.map((one) => one.id));
            }}
          >
            {everything ? "Clear the selection" : "Select all"}
          </Button>
        ) : null}
        <span
          className="text-xs text-muted-foreground tabular-nums"
          role="status"
        >
          {selected.length} of {items.length} selected
        </span>
      </div>
    </>
  );
}
