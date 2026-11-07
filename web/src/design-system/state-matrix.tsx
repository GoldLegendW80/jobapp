import type { ReactNode } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const DEFAULT_STATES = ["default", "hover", "focus", "active", "disabled"];

/**
 * Rows of variants against columns of states, every state pinned at once (no pointer needed).
 * Pinned through data-preview, which index.css adds to the hover, focus-visible and active variants.
 */
export function StateMatrix<Row extends string>({
  rows,
  states = DEFAULT_STATES,
  render,
}: {
  rows: readonly Row[];
  states?: readonly string[];
  render: (row: Row, state: string) => ReactNode;
}) {
  return (
    <div className="w-fit max-w-full">
      <Table stacked={false}>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>
              <span className="sr-only">Variant</span>
            </TableHead>
            {states.map((state) => (
              <TableHead key={state} className="text-xs text-muted-foreground">
                {state}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row} className="border-0 hover:bg-transparent">
              <TableHead scope="row" className="text-xs text-muted-foreground">
                {row}
              </TableHead>
              {states.map((state) => (
                <TableCell key={state}>{render(row, state)}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Spread onto a component to pin a state: `<Button {...previewState("hover")} />`. */
export function previewState(state: string) {
  if (state === "disabled") return { disabled: true };
  if (["hover", "focus", "active"].includes(state))
    return { "data-preview": state };
  return {};
}
