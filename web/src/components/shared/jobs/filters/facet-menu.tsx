import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import type { Facet } from "@/features/jobs/api/jobs-api";
import { formatCount } from "@/lib/format";

/**
 * A menu of the values found in the person's postings, each with its count, to tick several:
 * the places and the websites of the Filters panel. A picked value the postings no longer hold
 * stays listed, so it can be unticked.
 */
export function FacetMenu({
  label,
  facets,
  selected,
  onChange,
  nameOf = (value) => value,
  loading = false,
}: {
  label: string;
  /** Undefined while the first answer loads or when it could not be read. */
  facets: Facet[] | undefined;
  selected: readonly string[];
  onChange: (next: string[]) => void;
  nameOf?: (value: string) => string;
  loading?: boolean;
}) {
  const listed = facets ?? [];
  const values = new Set(listed.map((facet) => facet.value));
  const options = [
    ...selected
      .filter((value) => !values.has(value))
      .map((value) => ({ value, count: null as number | null })),
    ...listed.map((facet) => ({ value: facet.value, count: facet.count })),
  ];
  const summary =
    selected.length === 0
      ? "All"
      : selected.length === 1
        ? nameOf(selected[0] ?? "")
        : `${selected.length} selected`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="justify-between"
          aria-label={`${label}: ${summary}`}
        >
          <span className="truncate">
            <span className="text-muted-foreground">{label}: </span>
            {summary}
          </span>
          <ChevronDown aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-64">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {options.length === 0 ? (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">
            {loading ? "Loading..." : "None in your postings."}
          </p>
        ) : null}
        {options.map((option) => (
          <DropdownMenuCheckboxItem
            key={option.value}
            checked={selected.includes(option.value)}
            onSelect={(event) => {
              // Keep the menu open to pick several.
              event.preventDefault();
            }}
            onCheckedChange={(checked) => {
              onChange(
                checked
                  ? [...selected, option.value]
                  : selected.filter((value) => value !== option.value),
              );
            }}
          >
            <span className="min-w-0 flex-1 truncate">
              {nameOf(option.value)}
            </span>
            {option.count !== null ? (
              <span className="text-xs text-muted-foreground tabular-nums">
                {formatCount(option.count)}
              </span>
            ) : null}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
