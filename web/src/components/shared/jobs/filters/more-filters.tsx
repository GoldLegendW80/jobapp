/**
 * The filters of the Filters panel beyond status, contract, fit, found and company (jobs J29):
 * place, work mode, website, published, pay, application, and the two presets.
 */
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";

import type { Facet } from "@/features/jobs/api/jobs-api";
import {
  cleanPlace,
  foundToday,
  hasPublishedBound,
  hideSkippedAndClosed,
  localIsoDate,
  VALUES_MAX,
  type FilterProps,
  type PayChoice,
} from "@/features/jobs/helpers/filters";
import {
  APPLICATION_CHOICE_LABELS,
  APPLICATION_CHOICES,
  PAY_PERIODS,
  WORK_MODE_CHOICE_LABELS,
  WORK_MODE_CHOICES,
  websiteName,
  type PayPeriodChoice,
} from "@/features/jobs/helpers/labels";
import { PAY_PERIOD_WORDS } from "@/lib/wording";
import { FacetMenu } from "./facet-menu";
import { MultiSelectMenu } from "./multi-select-menu";

/** Found today, and Hide skipped and closed: one click each, the other filters kept. */
export function FilterPresets({ filters, onChange }: FilterProps) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          onChange(foundToday(filters, localIsoDate(new Date())));
        }}
      >
        Found today
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          onChange(hideSkippedAndClosed(filters));
        }}
      >
        Hide skipped and closed
      </Button>
    </div>
  );
}

/** The places of the person's postings to tick, and any other place typed in. */
export function PlaceFilter({
  filters,
  onChange,
  facets,
  loading,
}: FilterProps & { facets: Facet[] | undefined; loading: boolean }) {
  const id = useId();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string>();
  const add = () => {
    const place = cleanPlace(typed);
    if (place === null) {
      setError("A place needs a letter or a digit, 100 characters at most.");
      return;
    }
    setError(undefined);
    setTyped("");
    if (!filters.places.includes(place) && filters.places.length < VALUES_MAX)
      onChange({ ...filters, places: [...filters.places, place] });
  };
  return (
    <div className="flex flex-col gap-2">
      <FacetMenu
        label="Place"
        facets={facets}
        loading={loading}
        selected={filters.places}
        onChange={(places) => {
          onChange({ ...filters, places });
        }}
      />
      <Field className="gap-1">
        <Label htmlFor={id} className="sr-only">
          Another place
        </Label>
        <div className="flex gap-2">
          <Input
            id={id}
            placeholder="Another place, as written in postings"
            value={typed}
            maxLength={100}
            aria-invalid={error ? true : undefined}
            onChange={(event) => {
              setTyped(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") add();
            }}
          />
          <Button variant="outline" onClick={add} disabled={!typed.trim()}>
            Add
          </Button>
        </div>
        {error ? <FieldError>{error}</FieldError> : null}
      </Field>
    </div>
  );
}

export function WorkModeMenu({ filters, onChange }: FilterProps) {
  return (
    <MultiSelectMenu
      label="Work mode"
      options={WORK_MODE_CHOICES.map((value) => ({
        value,
        label: WORK_MODE_CHOICE_LABELS[value],
      }))}
      selected={filters.workModes}
      onChange={(modes) => {
        onChange({
          ...filters,
          workModes: WORK_MODE_CHOICES.filter((m) => modes.includes(m)),
        });
      }}
    />
  );
}

/** The websites the person's postings came from; a posting found on two counts on each. */
export function WebsiteMenu({
  filters,
  onChange,
  facets,
  loading,
}: FilterProps & { facets: Facet[] | undefined; loading: boolean }) {
  return (
    <FacetMenu
      label="Website"
      facets={facets}
      loading={loading}
      selected={filters.sources}
      nameOf={websiteName}
      onChange={(sources) => {
        onChange({ ...filters, sources });
      }}
    />
  );
}

const WITHIN = [0, 3, 7, 14, 30];
const withinWords = (days: number) =>
  days === 0 ? "Today" : `In the last ${days === 1 ? "day" : `${days} days`}`;

/** How recently the posting was published, and whether postings without a date stay. */
export function PublishedFilter({ filters, onChange }: FilterProps) {
  const ids = useId();
  const within = filters.publishedWithin;
  const choices =
    within === null || WITHIN.includes(within) ? WITHIN : [...WITHIN, within];
  return (
    <FieldSet className="gap-2">
      <FieldLegend variant="label">Published</FieldLegend>
      <NativeSelect
        aria-label="Published"
        value={within === null ? "" : String(within)}
        onChange={(event) => {
          const value = event.target.value;
          onChange({
            ...filters,
            publishedWithin: value === "" ? null : Number(value),
          });
        }}
      >
        <option value="">Any time</option>
        {choices.map((days) => (
          <option key={days} value={days}>
            {withinWords(days)}
          </option>
        ))}
      </NativeSelect>
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${ids}-undated`}
          checked={filters.publishedUndated}
          disabled={!hasPublishedBound(filters)}
          onCheckedChange={(checked) => {
            onChange({ ...filters, publishedUndated: checked === true });
          }}
        />
        <Label htmlFor={`${ids}-undated`} className="text-sm font-normal">
          Keep postings without a date
        </Label>
      </div>
    </FieldSet>
  );
}

/** Whether the pay is stated, and a minimum per year, month, day or hour. */
export function PayFilter({ filters, onChange }: FilterProps) {
  const ids = useId();
  const [min, setMin] = useState(filters.payMin?.toString() ?? "");
  const [period, setPeriod] = useState<PayPeriodChoice>(
    filters.payPeriod ?? "year",
  );
  const [error, setError] = useState<string>();
  // A pay changed elsewhere (a chip, back) refills the field without remounting it.
  const [shown, setShown] = useState([filters.payMin, filters.payPeriod]);
  if (shown[0] !== filters.payMin || shown[1] !== filters.payPeriod) {
    setShown([filters.payMin, filters.payPeriod]);
    setMin(filters.payMin?.toString() ?? "");
    setPeriod(filters.payPeriod ?? "year");
    setError(undefined);
  }
  const commit = (periodNow: PayPeriodChoice) => {
    const text = min.trim().replace(/[\s,]/g, "");
    if (text === "") {
      setError(undefined);
      if (filters.payMin !== null)
        onChange({ ...filters, payMin: null, payPeriod: null });
      return;
    }
    if (
      !/^\d{1,8}$/.test(text) ||
      Number(text) < 1 ||
      Number(text) > 10_000_000
    ) {
      setError("The pay is a whole number from 1 to 10,000,000.");
      return;
    }
    setError(undefined);
    onChange({
      ...filters,
      pay: filters.pay === "not_stated" ? null : filters.pay,
      payMin: Number(text),
      payPeriod: periodNow,
    });
  };
  return (
    <FieldSet className="gap-2">
      <FieldLegend variant="label">Pay</FieldLegend>
      <NativeSelect
        aria-label="Pay"
        value={filters.pay ?? ""}
        onChange={(event) => {
          const value = event.target.value as PayChoice | "";
          onChange(
            value === "not_stated"
              ? { ...filters, pay: value, payMin: null, payPeriod: null }
              : { ...filters, pay: value === "" ? null : value },
          );
        }}
      >
        <option value="">Stated or not</option>
        <option value="stated">Stated</option>
        <option value="not_stated">Not stated</option>
      </NativeSelect>
      <div className="flex items-end gap-2">
        <Field className="gap-1">
          <FieldLabel htmlFor={`${ids}-min`}>At least</FieldLabel>
          <Input
            id={`${ids}-min`}
            inputMode="numeric"
            placeholder="Any"
            value={min}
            disabled={filters.pay === "not_stated"}
            aria-invalid={error ? true : undefined}
            onChange={(event) => {
              setMin(event.target.value);
            }}
            onBlur={() => {
              commit(period);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") commit(period);
            }}
          />
        </Field>
        <NativeSelect
          aria-label="Pay period"
          value={period}
          disabled={filters.pay === "not_stated"}
          onChange={(event) => {
            const next = event.target.value as PayPeriodChoice;
            setPeriod(next);
            if (min.trim()) commit(next);
          }}
        >
          {PAY_PERIODS.map((value) => (
            <option key={value} value={value}>
              {PAY_PERIOD_WORDS[value]}
            </option>
          ))}
        </NativeSelect>
      </div>
      {error ? <FieldError>{error}</FieldError> : null}
    </FieldSet>
  );
}

export function ApplicationMenu({ filters, onChange }: FilterProps) {
  return (
    <MultiSelectMenu
      label="Application"
      options={APPLICATION_CHOICES.map((value) => ({
        value,
        label: APPLICATION_CHOICE_LABELS[value],
      }))}
      selected={filters.applications}
      onChange={(choices) => {
        onChange({
          ...filters,
          applications: APPLICATION_CHOICES.filter((c) => choices.includes(c)),
        });
      }}
    />
  );
}
