/**
 * The browser sources of the Search tab (browser B8): job sites without a feed that the sweep
 * reads in the person's own browser, after the company boards (api/app/browser/sources). One row
 * per source: the site, the words typed in its search box, an optional location, on or off, at
 * most BROWSER_SOURCES_MAX. Google Careers and Business France VIE run today; company sites (B9)
 * and LinkedIn (B10) are listed as coming later and cannot be picked, but a row of theirs saved
 * by another client is shown and kept as it is.
 */
import { Plus } from "lucide-react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import { BROWSER_KINDS, type BrowserSource } from "./api";
import {
  BROWSER_KINDS_RUNNABLE,
  BROWSER_SOURCES_MAX,
  type SearchForm,
} from "./editor-forms";
import { Group, ItemControls, TextField } from "./section-ui";

const BROWSER_KIND_LABELS: Record<BrowserSource["kind"], string> = {
  google_careers: "Google Careers",
  vie: "Business France VIE",
  site: "Company site (coming later)",
  linkedin: "LinkedIn (coming later)",
  freework: "Free-Work",
};

const runnable = (kind: string) =>
  (BROWSER_KINDS_RUNNABLE as readonly string[]).includes(kind);

export function BrowserSourcesCard({
  form,
}: {
  form: UseFormReturn<SearchForm>;
}) {
  const sources = useFieldArray({
    control: form.control,
    name: "browser_sources",
  });
  const full = sources.fields.length >= BROWSER_SOURCES_MAX;
  return (
    <Group
      title="Job sites in your browser"
      description="Sites without a feed, read in your own browser after the company boards, one page every few seconds. Google Careers and Business France VIE work today; company sites and LinkedIn come later."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={full}
          onClick={() => {
            sources.append(
              {
                kind: "google_careers",
                query: "",
                location: "",
                enabled: true,
              },
              { shouldFocus: false },
            );
          }}
        >
          <Plus aria-hidden="true" />
          Add site
        </Button>
      }
    >
      {sources.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No site yet.</p>
      ) : (
        <ol
          aria-label="Job sites in your browser"
          className="flex flex-col gap-4"
        >
          {sources.fields.map((item, index) => (
            <SourceRow
              key={item.id}
              form={form}
              index={index}
              count={sources.fields.length}
              onMove={sources.move}
              onRemove={sources.remove}
            />
          ))}
        </ol>
      )}
      {full ? (
        <p className="text-sm text-muted-foreground">
          {`At most ${BROWSER_SOURCES_MAX} sites.`}
        </p>
      ) : null}
    </Group>
  );
}

function SourceRow({
  form,
  index,
  count,
  onMove,
  onRemove,
}: {
  form: UseFormReturn<SearchForm>;
  index: number;
  count: number;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const { control } = form;
  const name = `site ${index + 1}`;
  const kind = form.watch(`browser_sources.${index}.kind`);
  return (
    <li className="flex flex-col gap-3 rounded-md border p-3">
      <div className="flex flex-wrap items-end gap-3">
        <FormField
          control={control}
          name={`browser_sources.${index}.kind`}
          render={({ field }) => (
            <FormItem className="min-w-44 flex-1 sm:max-w-60">
              <FormLabel>{`Site ${index + 1}`}</FormLabel>
              <FormControl>
                <NativeSelect {...field}>
                  {BROWSER_KINDS.map((k) => (
                    <option key={k} value={k} disabled={!runnable(k)}>
                      {BROWSER_KIND_LABELS[k]}
                    </option>
                  ))}
                </NativeSelect>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name={`browser_sources.${index}.enabled`}
          render={({ field }) => (
            <label className="flex h-9 items-center gap-2 text-sm">
              <input
                type="checkbox"
                role="switch"
                aria-checked={field.value}
                aria-label={`Read ${name}`}
                className="size-4 accent-primary"
                name={field.name}
                ref={field.ref}
                checked={field.value}
                onBlur={field.onBlur}
                onChange={(event) => {
                  field.onChange(event.target.checked);
                }}
              />
              <span aria-hidden="true">{field.value ? "On" : "Off"}</span>
            </label>
          )}
        />
        <div className="ml-auto">
          <ItemControls
            index={index}
            count={count}
            name={name}
            onMove={onMove}
            onRemove={onRemove}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-3">
        <TextField
          control={control}
          name={`browser_sources.${index}.query`}
          label="Search words"
          srSuffix={`of ${name}`}
          placeholder="ai engineer"
          autoComplete="off"
          className="min-w-44 flex-1"
          description="Typed in the site's search box."
        />
        <TextField
          control={control}
          name={`browser_sources.${index}.location`}
          label="Location (optional)"
          srSuffix={`of ${name}`}
          placeholder="Paris"
          autoComplete="off"
          className="min-w-44 flex-1"
        />
      </div>
      {runnable(kind) ? null : (
        <p className="text-sm text-muted-foreground">
          This site is not read yet: it is kept and will be read once it is
          supported.
        </p>
      )}
    </li>
  );
}
