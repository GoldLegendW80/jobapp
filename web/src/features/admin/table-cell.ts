/**
 * Mobile first tables: under 640px each row is a card and each cell a labelled line (the label
 * comes from `data-label`); from sm up, a regular table cell. Spread on a TableCell: its text
 * wraps as before.
 */
export const cell = (label: string) => ({
  "data-label": label,
  className:
    "px-3 py-2 align-middle whitespace-normal max-sm:grid max-sm:grid-cols-[6.5rem_1fr] max-sm:items-center max-sm:gap-2 max-sm:px-0 max-sm:py-1 max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]",
});

/** A column header: the small muted words above the cells (the header row is hidden on a phone). */
export const HEAD = "h-auto px-3 py-2 text-muted-foreground";

/** The body: a column of cards under 640px, the last one with its border like the others. */
export const BODY =
  "max-sm:flex max-sm:flex-col max-sm:gap-3 max-sm:[&_tr:last-child]:border";

/** A row: a card under 640px. */
export const ROW =
  "max-sm:flex max-sm:flex-col max-sm:rounded-lg max-sm:border max-sm:bg-card max-sm:p-3";

/** The cell of a row's actions: at the right end, then under the card's lines on a phone. */
export const ACTIONS =
  "px-3 py-2 text-right whitespace-normal max-sm:px-0 max-sm:pt-2 max-sm:text-left";
