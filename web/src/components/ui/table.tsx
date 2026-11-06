import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Every table of the app (agents A8): the shadcn table with the look of the Users table of
 * Admin, light and dark from the theme tokens. A list table is `stacked` (the default): under
 * 640px each row becomes a card and each cell a line led by its `label`. A comparison table
 * passes `stacked={false}`: it keeps its columns and scrolls sideways inside its border when it
 * is wider than the screen. table.test.tsx fails when web/src draws a table without it.
 */
type Layout = {
  stacked: boolean;
  section: "head" | "body" | null;
};

const LayoutContext = React.createContext<Layout>({
  stacked: true,
  section: null,
});

function Table({
  className,
  stacked = true,
  busy,
  ...props
}: React.ComponentProps<"table"> & {
  /** Under 640px, each row a card (a list). False for a comparison table. */
  stacked?: boolean;
  /** A change is being saved: the table is dimmed and announced busy. */
  busy?: boolean;
}) {
  const layout = React.useMemo(() => ({ stacked, section: null }), [stacked]);
  return (
    <LayoutContext.Provider value={layout}>
      <div
        data-slot="table-container"
        aria-busy={busy}
        className={cn(
          "w-full",
          stacked
            ? "sm:overflow-x-auto sm:rounded-lg sm:border sm:bg-card"
            : "overflow-x-auto rounded-lg border bg-card",
          busy && "[&_tbody]:opacity-60",
        )}
      >
        <table
          data-slot="table"
          className={cn("w-full text-sm", stacked && "max-sm:block", className)}
          {...props}
        />
      </div>
    </LayoutContext.Provider>
  );
}

/** Visible by default; a list table whose heading already names it passes `sr-only`. */
function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  const { stacked } = React.useContext(LayoutContext);
  return (
    <caption
      data-slot="table-caption"
      className={cn(
        "px-3 py-2 text-left text-sm text-muted-foreground",
        // A block spans the cards' width by itself; a width here would beat sr-only's 1px and scroll the page sideways.
        stacked && "max-sm:block max-sm:px-0",
        className,
      )}
      {...props}
    />
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  const { stacked } = React.useContext(LayoutContext);
  const layout = React.useMemo(
    () => ({ stacked, section: "head" as const }),
    [stacked],
  );
  return (
    <LayoutContext.Provider value={layout}>
      <thead
        data-slot="table-header"
        className={cn(
          "border-b bg-muted text-left text-xs text-muted-foreground",
          stacked && "max-sm:sr-only",
          className,
        )}
        {...props}
      />
    </LayoutContext.Provider>
  );
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  const { stacked } = React.useContext(LayoutContext);
  const layout = React.useMemo(
    () => ({ stacked, section: "body" as const }),
    [stacked],
  );
  return (
    <LayoutContext.Provider value={layout}>
      <tbody
        data-slot="table-body"
        className={cn(
          stacked && "max-sm:flex max-sm:flex-col max-sm:gap-3",
          className,
        )}
        {...props}
      />
    </LayoutContext.Provider>
  );
}

/** `data-disabled` greys a row out (a disabled account). */
function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  const { stacked, section } = React.useContext(LayoutContext);
  const body = section !== "head";
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors last:border-b-0 data-disabled:text-muted-foreground",
        body && (stacked ? "sm:hover:bg-muted/50" : "hover:bg-muted/50"),
        body &&
          stacked &&
          "max-sm:flex max-sm:flex-col max-sm:rounded-lg max-sm:border max-sm:bg-card max-sm:p-3 max-sm:last:border-b",
        className,
      )}
      {...props}
    />
  );
}

/**
 * A header cell: `scope="col"` in the header, `scope="row"` in the body (the row's header, its
 * card's title under 640px), unless `scope` says otherwise.
 */
function TableHead({ className, scope, ...props }: React.ComponentProps<"th">) {
  const { stacked, section } = React.useContext(LayoutContext);
  const own = scope ?? (section === "body" ? "row" : "col");
  return (
    <th
      data-slot="table-head"
      scope={own}
      className={cn(
        "px-3 py-2 text-left font-medium",
        own === "row" && stacked && "max-sm:px-0 max-sm:pb-2 max-sm:text-base",
        className,
      )}
      {...props}
    />
  );
}

/** `label` names the cell on its card line under 640px (its column's header). */
function TableCell({
  className,
  label,
  ...props
}: React.ComponentProps<"td"> & { label?: string }) {
  const { stacked } = React.useContext(LayoutContext);
  return (
    <td
      data-slot="table-cell"
      data-label={label}
      className={cn(
        "px-3 py-2",
        stacked &&
          (label
            ? "max-sm:grid max-sm:grid-cols-[6.5rem_minmax(0,1fr)] max-sm:items-center max-sm:gap-2 max-sm:px-0 max-sm:py-1 max-sm:before:text-xs max-sm:before:font-medium max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]"
            : "max-sm:px-0"),
        className,
      )}
      {...props}
    />
  );
}

export {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
};
