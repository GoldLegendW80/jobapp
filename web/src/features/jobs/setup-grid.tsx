/**
 * The grid of Find jobs (search-setup.tsx, sources-editor.tsx): one left edge for every label,
 * help text, input, list and button, and one vertical rhythm. A column starts after a fixed
 * gutter of 2.25rem; whatever leads a line (a step number, an icon, a radio button, the chevron
 * of "More options") hangs in that gutter, centred in a 1.75rem box, so the text after it starts
 * at the same x as the text that has nothing before it. A box with a background or a border (a
 * callout, a line of the list) reaches past the gutter so its glyph keeps the same axis.
 *
 *   gutter   column
 *   [ 1 ]    Where to search
 *   [ ◎ ]    A job website, like LinkedIn or Indeed
 *            Website address
 *            [ https://...                 ] [ Add ]
 */
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A column: its content starts after the gutter. The two properties hang the chevron of an
 * AdvancedSection inside it (components/advanced-section.tsx) in the gutter, on the glyph axis:
 * a 1rem chevron, 0.375rem into the gutter, then 0.875rem to the text. */
export const COLUMN =
  "pl-9 [--advanced-gap:0.875rem] [--advanced-hang:1.875rem]";

/** A line inside a column whose first cell is the gutter: a glyph, then the text at the edge. */
export const HANG = "-ml-9 grid grid-cols-[2.25rem_minmax(0,1fr)]";

/** A box inside a column that reaches 0.5rem past the gutter (its border or background stays
 * clear of the glyph), with its glyph in the gutter and its text at the edge. */
export const BOX =
  "-ml-11 grid grid-cols-[2.25rem_minmax(0,1fr)] pl-2 pr-3 rounded-lg";

/** The vertical rhythm: between the blocks of a section, and inside one block. */
export const BLOCKS = "flex flex-col gap-4";
export const BLOCK = "flex flex-col gap-2";

/** What leads a line, centred in the gutter's 1.75rem box. `line` is the height of the line's
 * first row of text, so the glyph sits on it when the text wraps. */
export function Glyph({
  children,
  line = "h-7",
  className,
}: {
  children?: ReactNode;
  line?: "h-7" | "h-5" | "h-4";
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn("flex w-7 items-center justify-center", line, className)}
    >
      {children}
    </span>
  );
}

/** A section's number, as a filled circle in the gutter. */
export function StepNumber({ step }: { step: number }) {
  return (
    <Glyph>
      <span className="flex size-7 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
        {step}
      </span>
    </Glyph>
  );
}
