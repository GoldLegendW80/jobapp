import { ChevronDown, Search, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { cn } from "@/lib/utils";

import {
  EMPTY_FILTERS,
  hasFilters,
  isIsoDate,
  TEXT_MAX,
  type JobFilters,
} from "./filters";
import { formatDate } from "./format";
import {
  CONTRACT_TYPES,
  isSort,
  SORT_LABELS,
  SORTS,
  STATUS_LABELS,
  STATUSES,
} from "./labels";
import { useDebouncedField } from "./use-job-filters";

export const SEARCH_DEBOUNCE_MS = 300;

/**
 * Search, status, type, company, fit range, found dates and sort, then one removable chip per
 * active filter and "Clear filters". Text fields commit after a pause in typing; the others at
 * once. Every value comes from and goes to the URL through `filters` and `onChange`.
 */
export function FilterBar({
  filters,
  onChange,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
}) {
  const search = useDebouncedField(
    filters.q,
    (q) => {
      onChange({ ...filters, q });
    },
    SEARCH_DEBOUNCE_MS,
  );
  const company = useDebouncedField(
    filters.company,
    (value) => {
      onChange({ ...filters, company: value });
    },
    SEARCH_DEBOUNCE_MS,
  );
  const ids = useId();

  return (
    <section
      aria-label="Filters"
      className="flex flex-col gap-3 rounded-lg border bg-card p-3"
    >
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <div className="relative sm:col-span-full">
          <Label htmlFor={`${ids}-q`} className="sr-only">
            Search postings
          </Label>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            id={`${ids}-q`}
            type="search"
            placeholder="Search company, title, location, notes"
            maxLength={TEXT_MAX}
            value={search.draft}
            onChange={(event) => {
              search.setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") search.flush();
            }}
            className="pl-9"
          />
        </div>
        <MultiSelect
          label="Status"
          options={STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
          selected={filters.statuses}
          onChange={(statuses) => {
            onChange({
              ...filters,
              statuses: STATUSES.filter((s) => statuses.includes(s)),
            });
          }}
        />
        <MultiSelect
          label="Type"
          options={CONTRACT_TYPES.map((t) => ({ value: t, label: t }))}
          selected={filters.types}
          onChange={(types) => {
            onChange({
              ...filters,
              types: CONTRACT_TYPES.filter((t) => types.includes(t)),
            });
          }}
        />
        <div>
          <Label htmlFor={`${ids}-company`} className="sr-only">
            Company
          </Label>
          <Input
            id={`${ids}-company`}
            placeholder="Company"
            maxLength={TEXT_MAX}
            value={company.draft}
            onChange={(event) => {
              company.setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") company.flush();
            }}
          />
        </div>
        <div className="flex items-center gap-2">
          <Label
            htmlFor={`${ids}-sort`}
            className="shrink-0 text-sm text-muted-foreground"
          >
            Sort
          </Label>
          <NativeSelect
            id={`${ids}-sort`}
            value={filters.sort}
            onChange={(event) => {
              const value = event.target.value;
              if (isSort(value)) onChange({ ...filters, sort: value });
            }}
          >
            {SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {SORT_LABELS[sort]}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <FitRange filters={filters} onChange={onChange} />
        <DateRange filters={filters} onChange={onChange} />
      </div>
      <Chips filters={filters} onChange={onChange} />
    </section>
  );
}

function MultiSelect<T extends string>({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: Array<{ value: T; label: string }>;
  selected: readonly T[];
  onChange: (next: T[]) => void;
}) {
  const summary =
    selected.length === 0
      ? "All"
      : selected.length === 1
        ? (options.find((o) => o.value === selected[0])?.label ?? "1")
        : `${selected.length} selected`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="justify-between"
          aria-label={`${label}: ${summary}`}
        >
          <span>
            <span className="text-muted-foreground">{label}: </span>
            {summary}
          </span>
          <ChevronDown aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
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
            {option.label}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function parseFitInput(raw: string): number | null | "invalid" {
  const value = raw.trim();
  if (value === "") return null;
  if (!/^\d{1,3}$/.test(value)) return "invalid";
  const number = Number(value);
  return number <= 100 ? number : "invalid";
}

/** Two numbers committed on Enter or when focus leaves, only as a valid range. */
function FitRange({
  filters,
  onChange,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
}) {
  const ids = useId();
  const [min, setMin] = useState(filters.fitMin?.toString() ?? "");
  const [max, setMax] = useState(filters.fitMax?.toString() ?? "");
  const [error, setError] = useState<string>();
  // A range changed elsewhere (the histogram, a chip, back) refills the fields, without
  // remounting them, so focus stays where it is.
  const [shown, setShown] = useState([filters.fitMin, filters.fitMax]);
  if (shown[0] !== filters.fitMin || shown[1] !== filters.fitMax) {
    setShown([filters.fitMin, filters.fitMax]);
    setMin(filters.fitMin?.toString() ?? "");
    setMax(filters.fitMax?.toString() ?? "");
    setError(undefined);
  }

  const commit = () => {
    const low = parseFitInput(min);
    const high = parseFitInput(max);
    if (low === "invalid" || high === "invalid") {
      setError("Fit is a whole number from 0 to 100.");
      return;
    }
    if (low !== null && high !== null && low > high) {
      setError("The lowest fit is above the highest.");
      return;
    }
    setError(undefined);
    if (low !== filters.fitMin || high !== filters.fitMax) {
      onChange({ ...filters, fitMin: low, fitMax: high });
    }
  };

  const field = (
    id: string,
    label: string,
    value: string,
    set: (value: string) => void,
  ) => (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      <Input
        id={id}
        inputMode="numeric"
        className="w-20"
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${ids}-error` : undefined}
        onChange={(event) => {
          set(event.target.value);
        }}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
        }}
      />
    </div>
  );

  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="sr-only">Fit range</legend>
      <div className="flex items-end gap-2">
        {field(`${ids}-min`, "Fit from", min, setMin)}
        {field(`${ids}-max`, "Fit to", max, setMax)}
      </div>
      {error ? (
        <p
          id={`${ids}-error`}
          role="alert"
          className="text-xs text-destructive"
        >
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** Found between two dates, committed as soon as the pair is valid. */
function DateRange({
  filters,
  onChange,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
}) {
  const ids = useId();
  const [error, setError] = useState<string>();

  const set = (key: "foundFrom" | "foundTo", raw: string) => {
    const value = raw === "" ? null : raw;
    if (value !== null && !isIsoDate(value)) return;
    const next = { ...filters, [key]: value };
    if (next.foundFrom && next.foundTo && next.foundFrom > next.foundTo) {
      setError("The first date is after the second.");
      return;
    }
    setError(undefined);
    onChange(next);
  };

  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="sr-only">Found between</legend>
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label
            htmlFor={`${ids}-from`}
            className="text-xs text-muted-foreground"
          >
            Found from
          </Label>
          <Input
            id={`${ids}-from`}
            type="date"
            className="w-40"
            value={filters.foundFrom ?? ""}
            onChange={(event) => {
              set("foundFrom", event.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label
            htmlFor={`${ids}-to`}
            className="text-xs text-muted-foreground"
          >
            Found to
          </Label>
          <Input
            id={`${ids}-to`}
            type="date"
            className="w-40"
            value={filters.foundTo ?? ""}
            onChange={(event) => {
              set("foundTo", event.target.value);
            }}
          />
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

function Chip({
  children,
  onRemove,
  label,
}: {
  children: ReactNode;
  onRemove: () => void;
  label: string;
}) {
  return (
    <li className="inline-flex max-w-full items-center gap-1 rounded-full border bg-secondary py-0.5 pr-1 pl-3 text-xs text-secondary-foreground">
      <span className="truncate">{children}</span>
      <button
        type="button"
        aria-label={`Remove filter ${label}`}
        onClick={onRemove}
        className="grid size-5 shrink-0 place-items-center rounded-full hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <X aria-hidden="true" className="size-3" />
      </button>
    </li>
  );
}

/** One chip per active filter, so the view is readable at a glance and each part removable. */
function Chips({
  filters,
  onChange,
}: {
  filters: JobFilters;
  onChange: (next: JobFilters) => void;
}) {
  if (!hasFilters(filters)) return null;
  const chips: Array<{ key: string; text: string; remove: () => void }> = [];
  if (filters.q)
    chips.push({
      key: "q",
      text: `Search: ${filters.q}`,
      remove: () => {
        onChange({ ...filters, q: "" });
      },
    });
  for (const status of filters.statuses)
    chips.push({
      key: `status-${status}`,
      text: `Status: ${STATUS_LABELS[status]}`,
      remove: () => {
        onChange({
          ...filters,
          statuses: filters.statuses.filter((s) => s !== status),
        });
      },
    });
  for (const type of filters.types)
    chips.push({
      key: `type-${type}`,
      text: `Type: ${type}`,
      remove: () => {
        onChange({
          ...filters,
          types: filters.types.filter((t) => t !== type),
        });
      },
    });
  if (filters.company)
    chips.push({
      key: "company",
      text: `Company: ${filters.company}`,
      remove: () => {
        onChange({ ...filters, company: "" });
      },
    });
  if (filters.fitMin !== null || filters.fitMax !== null)
    chips.push({
      key: "fit",
      text: `Fit: ${filters.fitMin ?? 0} to ${filters.fitMax ?? 100}`,
      remove: () => {
        onChange({ ...filters, fitMin: null, fitMax: null });
      },
    });
  if (filters.foundFrom || filters.foundTo)
    chips.push({
      key: "found",
      text:
        filters.foundFrom && filters.foundTo
          ? `Found: ${formatDate(filters.foundFrom)} to ${formatDate(filters.foundTo)}`
          : filters.foundFrom
            ? `Found since ${formatDate(filters.foundFrom)}`
            : `Found until ${formatDate(filters.foundTo)}`,
      remove: () => {
        onChange({ ...filters, foundFrom: null, foundTo: null });
      },
    });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ul aria-label="Active filters" className="flex min-w-0 flex-wrap gap-2">
        {chips.map((chip) => (
          <Chip key={chip.key} label={chip.text} onRemove={chip.remove}>
            {chip.text}
          </Chip>
        ))}
      </ul>
      <Button
        variant="ghost"
        size="sm"
        className={cn("h-7")}
        onClick={() => {
          onChange({ ...EMPTY_FILTERS, sort: filters.sort });
        }}
      >
        Clear filters
      </Button>
    </div>
  );
}
