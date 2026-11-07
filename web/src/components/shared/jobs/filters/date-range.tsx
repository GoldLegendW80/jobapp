import { format, parseISO } from "date-fns";
import { CalendarIcon } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Field,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { formatDate } from "@/lib/format";

import {
  parseTypedDate,
  type FilterProps,
} from "@/features/jobs/helpers/filters";

const toIso = (day: Date) => format(day, "yyyy-MM-dd");
const toDay = (iso: string | null) => (iso ? parseISO(iso) : undefined);

/**
 * Found between two dates: each typed ("6 Nov 2026" or "2026-11-06") and committed on Enter or when
 * focus leaves, or both picked on a calendar beside them (the first day picked, then the other end,
 * either way round). Committed only as a valid pair; a first date after the second says so.
 */
export function DateRange({ filters, onChange }: FilterProps) {
  const ids = useId();
  const [from, setFrom] = useState(formatDate(filters.foundFrom));
  const [to, setTo] = useState(formatDate(filters.foundTo));
  const [error, setError] = useState<string>();
  const [open, setOpen] = useState(false);
  // The day picked first on the calendar since it opened, until the other end is picked.
  const [anchor, setAnchor] = useState<string | null>(null);
  // A range changed elsewhere (the calendar, a chip, back) refills the fields without remounting
  // them, so focus stays where it is.
  const [shown, setShown] = useState([filters.foundFrom, filters.foundTo]);
  if (shown[0] !== filters.foundFrom || shown[1] !== filters.foundTo) {
    setShown([filters.foundFrom, filters.foundTo]);
    setFrom(formatDate(filters.foundFrom));
    setTo(formatDate(filters.foundTo));
    setError(undefined);
  }

  const commit = (low: string | null, high: string | null) => {
    if (low !== null && high !== null && low > high) {
      setError("The first date is after the second.");
      return;
    }
    setError(undefined);
    if (low !== filters.foundFrom || high !== filters.foundTo) {
      onChange({ ...filters, foundFrom: low, foundTo: high });
    }
  };
  const commitTyped = () => {
    const low = parseTypedDate(from);
    const high = parseTypedDate(to);
    if (low === false || high === false) {
      setError("Type a date as 6 Nov 2026.");
      return;
    }
    commit(low, high);
  };
  const pick = (day: Date) => {
    const iso = toIso(day);
    if (anchor === null) {
      setAnchor(iso);
      commit(iso, null);
      return;
    }
    setAnchor(null);
    setOpen(false);
    commit(anchor <= iso ? anchor : iso, anchor <= iso ? iso : anchor);
  };

  const today = new Date();
  const selected = toDay(filters.foundFrom);
  const errorId = error ? `${ids}-error` : undefined;
  return (
    <FieldSet className="gap-1">
      <FieldLegend className="sr-only">Found between</FieldLegend>
      <div className="flex items-end gap-2">
        <DateInput
          id={`${ids}-from`}
          label="Found from"
          value={from}
          errorId={errorId}
          onChange={setFrom}
          onCommit={commitTyped}
        />
        <DateInput
          id={`${ids}-to`}
          label="Found to"
          value={to}
          errorId={errorId}
          onChange={setTo}
          onCommit={commitTyped}
        />
        <Popover
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            setAnchor(null);
          }}
        >
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              aria-label="Pick the dates on a calendar"
            >
              <CalendarIcon aria-hidden="true" />
            </Button>
          </PopoverTrigger>
          <PopoverContent
            className="w-auto p-0"
            align="end"
            // The focus goes to the day the calendar marks (the range's first day, or today), once
            // the popover's focus scope is in place: inside the Filters sheet, sooner is undone.
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              if (event.currentTarget instanceof HTMLElement)
                event.currentTarget
                  .querySelector<HTMLElement>("[data-focused] button")
                  ?.focus();
            }}
          >
            <Calendar
              autoFocus
              mode="range"
              selected={{ from: selected, to: toDay(filters.foundTo) }}
              onSelect={(_range, day) => {
                pick(day);
              }}
              defaultMonth={selected ?? today}
              disabled={{ after: today }}
              weekStartsOn={1}
            />
          </PopoverContent>
        </Popover>
      </div>
      {error ? (
        <FieldError id={errorId} className="text-xs">
          {error}
        </FieldError>
      ) : null}
    </FieldSet>
  );
}

/** One end of the range; `errorId` names the range's problem while there is one. */
function DateInput({
  id,
  label,
  value,
  errorId,
  onChange,
  onCommit,
}: {
  id: string;
  label: string;
  value: string;
  errorId: string | undefined;
  onChange: (value: string) => void;
  onCommit: () => void;
}) {
  return (
    <Field className="w-36 gap-1">
      <FieldLabel htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </FieldLabel>
      <Input
        id={id}
        className="w-36"
        placeholder="Any date"
        autoComplete="off"
        value={value}
        aria-invalid={errorId ? true : undefined}
        aria-describedby={errorId}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        onBlur={onCommit}
        onKeyDown={(event) => {
          if (event.key === "Enter") onCommit();
        }}
      />
    </Field>
  );
}
