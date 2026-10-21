import { useId } from "react";

import { Label } from "@/components/ui/label";
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
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">
          {WORDING.contractTypes}
        </legend>
        <div className="flex flex-wrap gap-2">
          {Object.entries(CONTRACT_WORDS).map(([code, word]) => (
            <label
              key={code}
              className="flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm sm:min-h-9"
            >
              <input
                type="checkbox"
                checked={filters.types.includes(code)}
                onChange={() => {
                  toggle(code);
                }}
              />
              {word}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor={remoteId}>{WORDING.remoteOrOnSite}</Label>
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
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={sourceId}>{WORDING.source}</Label>
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
        </div>
      </div>
    </div>
  );
}

/** "Greenhouse" for "greenhouse" (the api matches every company page of that board). */
function sourceLabel(source: string): string {
  return sourceLine(source)?.replace(/^via /, "") ?? WORDING.addedByYou;
}
