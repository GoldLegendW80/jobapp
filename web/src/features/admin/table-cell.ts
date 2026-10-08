/**
 * Mobile first tables: under 640px each row is a card and each cell a labelled line (the label
 * comes from `data-label`); from sm up, a regular table cell.
 */
export const cell = (label: string) => ({
  "data-label": label,
  className:
    "px-3 py-2 align-middle max-sm:grid max-sm:grid-cols-[6.5rem_1fr] max-sm:items-center max-sm:gap-2 max-sm:px-0 max-sm:py-1 max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]",
});
