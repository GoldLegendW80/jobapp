import { useId } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";
import { CONTRACT_WORDS, WORDING } from "@/lib/wording";

import type { DeckFilters, Remote } from "../opportunities-api";
import { sourceLine } from "./card-text";

const ANY = "";

/**
 * The deck's filter row (inside "Advanced"): contract types, remote or on site, source. Each
 * change reads the deck again from its first card.
 */
export function DeckFilterRow({
  filters,
  sources,
  onChange,
}: {
  filters: DeckFilters;
  sources: readonly string[];
  onChange: (filters: DeckFilters) => void;
}) {
  const remoteId = useId();
  const sourceId = useId();
  const toggle = (code: string) => {
    const types = filters.types.includes(code)
      ? filters.types.filter((one) => one !== code)
      : [...filters.types, code];
    onChange({ ...filters, types });
  };
  return (
    <div className="flex flex-col gap-4">
      <FieldSet className="gap-2">
        <FieldLegend variant="label" className="mb-2">
          {WORDING.contractTypes}
        </FieldLegend>
        <div className="flex flex-wrap gap-2">
          {Object.entries(CONTRACT_WORDS).map(([code, word]) => (
            // The whole chip is the label, so a tap anywhere on it ticks the box.
            <FieldLabel
              key={code}
              className="min-h-11 w-auto items-center rounded-md border px-3 font-normal sm:min-h-9"
            >
              <Checkbox
                checked={filters.types.includes(code)}
                onCheckedChange={() => {
                  toggle(code);
                }}
              />
              {word}
            </FieldLabel>
          ))}
        </div>
      </FieldSet>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field className="gap-2">
          <FieldLabel htmlFor={remoteId}>{WORDING.remoteOrOnSite}</FieldLabel>
          <NativeSelect
            id={remoteId}
            value={filters.remote ?? ANY}
            onChange={(event) => {
              onChange({
                ...filters,
                remote: (event.target.value || null) as Remote | null,
              });
            }}
          >
            <option value={ANY}>{WORDING.either}</option>
            <option value="remote">{WORDING.remote}</option>
            <option value="on_site">{WORDING.onSite}</option>
          </NativeSelect>
        </Field>
        <Field className="gap-2">
          <FieldLabel htmlFor={sourceId}>{WORDING.source}</FieldLabel>
          <NativeSelect
            id={sourceId}
            value={filters.source ?? ANY}
            onChange={(event) => {
              onChange({ ...filters, source: event.target.value || null });
            }}
          >
            <option value={ANY}>{WORDING.anySource}</option>
            {sources.map((source) => (
              <option key={source} value={source}>
                {sourceLabel(source)}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>
    </div>
  );
}

/** "Greenhouse" for "greenhouse" (the api matches every company page of that board). */
function sourceLabel(source: string): string {
  return sourceLine(source)?.replace(/^via /, "") ?? WORDING.addedByYou;
}
