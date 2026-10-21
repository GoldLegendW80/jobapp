/**
 * Search: what the sweep looks for. Title include and exclude terms and location terms as chips
 * (each list with its advanced switch), contract types as toggles, the remote stance, the minimum
 * fit to shortlist, the posting age window, the company boards (boards-table.tsx), the job sites
 * read in the browser (browser-sources-card.tsx), the freelance and project sources
 * (opportunities/sources, O8) and the never-apply rules. Then what the person
 * aims for (target-fields.tsx, O4) and, as a card with its own Save, the preferences section
 * (preferences-card.tsx). On top, the starter list of boards and sites (starter-card.tsx, O5c).
 */
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";
import { DocumentsSearchOptions } from "@/features/documents/generation-settings-card";
import { FreelanceSourcesCard } from "@/features/opportunities/sources/freelance-sources-card";
import { starterKey } from "@/features/onboarding/starter-api";
import { cn } from "@/lib/utils";

import {
  CONTRACT_TYPES,
  REMOTE_STANCES,
  type Profile,
  profileKey,
  type SweepSchedule,
} from "./api";
import { BoardsTable } from "./boards-table";
import { BrowserSourcesCard } from "./browser-sources-card";
import { PreferencesCard } from "./preferences-card";
import { StarterCard } from "./starter-card";
import { TargetFields } from "./target-fields";
import { describeSchedule } from "./schedule";
import {
  fromSearchForm,
  NEVER_APPLY_MAX,
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "./editor-forms";
import {
  ConflictDialog,
  FormErrorAlert,
  Group,
  ItemControls,
  SaveBar,
  TextField,
} from "./section-ui";
import { TermChips } from "./term-chips";
import { useSectionForm } from "./use-section-form";

const CONTRACT_LABELS: Record<(typeof CONTRACT_TYPES)[number], string> = {
  CDI: "CDI (permanent)",
  CDD: "CDD (fixed term)",
  VIE: "VIE",
  Stage: "Stage (internship)",
  Alternance: "Alternance (apprenticeship)",
  Freelance: "Freelance",
};

const STANCE_LABELS: Record<(typeof REMOTE_STANCES)[number], string> = {
  remote: "Remote only",
  hybrid: "Hybrid",
  office: "Office",
  any: "Any",
};

export function SearchTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const client = useQueryClient();
  const [dirty, setDirty] = useState(false);
  // Bumped once the profile is read again after a starter choice: the forms below remount on
  // it, so their values and the revision their next save is based on include what was added.
  const [generation, setGeneration] = useState(0);
  const reloadForms = useRef(false);
  const onFormsDirty = useCallback(
    (now: boolean) => {
      setDirty(now);
      onDirtyChange(now);
    },
    [onDirtyChange],
  );
  const onStarterChanged = useCallback(() => {
    reloadForms.current = true;
    void client.invalidateQueries({ queryKey: profileKey });
  }, [client]);
  // A new revision: after a starter choice the forms reload; after a save of the search the
  // targets the list is built from may have changed, so the list is read again.
  const seen = useRef(profile.revision);
  useEffect(() => {
    if (seen.current === profile.revision) return;
    seen.current = profile.revision;
    if (reloadForms.current) {
      reloadForms.current = false;
      setGeneration((n) => n + 1);
    }
    void client.invalidateQueries({ queryKey: starterKey });
  }, [client, profile.revision]);
  return (
    <div className="flex flex-col gap-6">
      <StarterCard
        profile={profile}
        dirty={dirty}
        onChanged={onStarterChanged}
      />
      <SearchForms
        key={generation}
        profile={profile}
        onDirtyChange={onFormsDirty}
      />
    </div>
  );
}

function SearchForms({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  // Two forms on one tab (search and preferences): the page is dirty while either is.
  const [searchDirty, setSearchDirty] = useState(false);
  const [preferencesDirty, setPreferencesDirty] = useState(false);
  useEffect(() => {
    onDirtyChange(searchDirty || preferencesDirty);
  }, [onDirtyChange, searchDirty, preferencesDirty]);
  const onSearchDirty = useCallback((dirty: boolean) => {
    setSearchDirty(dirty);
  }, []);
  const onPreferencesDirty = useCallback((dirty: boolean) => {
    setPreferencesDirty(dirty);
  }, []);
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "search",
      profile,
      schema: searchSchema,
      toForm: toSearchForm,
      fromForm: fromSearchForm,
      rules: { unique: { boards: "org" }, wrapped: ["never_apply"] },
      onDirtyChange: onSearchDirty,
    });
  const { control } = form;

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          noValidate
          aria-label="Search"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Job titles"
            description="Plain terms match whole words in any case, with an optional plural. Switch a list to advanced to write regular expressions instead."
          >
            <TermChips
              form={form}
              name="title_include"
              label="Titles to include"
              description="A posting is kept when its title matches one of these."
            />
            <TermChips
              form={form}
              name="title_exclude"
              label="Titles to exclude"
              description="A posting whose title matches one of these is dropped."
            />
          </Group>
          <Group title="Where and how">
            <TermChips
              form={form}
              name="location"
              label="Locations"
              description="Cities, countries or Remote, matched against the posting location."
            />
            <FormField
              control={control}
              name="remote_stance"
              render={({ field }) => (
                <FormItem className="max-w-64">
                  <FormLabel>Remote stance</FormLabel>
                  <FormControl>
                    <NativeSelect {...field}>
                      <option value="">Not set</option>
                      {REMOTE_STANCES.map((stance) => (
                        <option key={stance} value={stance}>
                          {STANCE_LABELS[stance]}
                        </option>
                      ))}
                    </NativeSelect>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <ContractTypes form={form} />
          </Group>
          <TargetFields form={form} />
          <Group title="Shortlist">
            <FormField
              control={control}
              name="min_fit"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-baseline justify-between gap-2">
                    <FormLabel>Minimum fit</FormLabel>
                    <output className="text-sm tabular-nums text-muted-foreground">
                      {field.value} / 100
                    </output>
                  </div>
                  <FormControl>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={1}
                      className="w-full accent-primary"
                      name={field.name}
                      ref={field.ref}
                      value={field.value}
                      onBlur={field.onBlur}
                      onChange={(event) => {
                        field.onChange(Number(event.target.value));
                      }}
                    />
                  </FormControl>
                  <FormDescription>
                    Postings scoring at least this are proposed for the
                    shortlist.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <TextField
              control={control}
              name="days"
              label="Posting age window (days)"
              inputMode="numeric"
              autoComplete="off"
              className="max-w-48"
              description="Only postings published within this many days."
            />
          </Group>
          <AutomaticSearch schedule={form.getValues("sweep_schedule")} />
          <BoardsTable form={form} />
          <BrowserSourcesCard form={form} />
          <FreelanceSourcesCard form={form} />
          <NeverApply form={form} />
          <DocumentsSearchOptions form={form} />
          <SaveBar
            label="Search"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Search"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
      <PreferencesCard profile={profile} onDirtyChange={onPreferencesDirty} />
    </div>
  );
}

function ContractTypes({ form }: { form: UseFormReturn<SearchForm> }) {
  return (
    <FormField
      control={form.control}
      name="contract_types"
      render={({ field }) => {
        const selected = field.value;
        return (
          <FormItem>
            <p id="contract-types-label" className="text-sm font-medium">
              Contract types
            </p>
            <div
              role="group"
              aria-labelledby="contract-types-label"
              className="flex flex-wrap gap-2"
            >
              {CONTRACT_TYPES.map((type) => {
                const on = selected.includes(type);
                return (
                  <Button
                    key={type}
                    type="button"
                    size="sm"
                    variant={on ? "default" : "outline"}
                    aria-pressed={on}
                    className={cn(!on && "text-muted-foreground")}
                    onClick={() => {
                      field.onChange(
                        on
                          ? selected.filter((t) => t !== type)
                          : CONTRACT_TYPES.filter(
                              (t) => t === type || selected.includes(t),
                            ),
                      );
                    }}
                  >
                    {CONTRACT_LABELS[type]}
                  </Button>
                );
              })}
            </div>
            <FormMessage />
          </FormItem>
        );
      }}
    />
  );
}

function NeverApply({ form }: { form: UseFormReturn<SearchForm> }) {
  const rules = useFieldArray({ control: form.control, name: "never_apply" });
  return (
    <Group
      title="Never apply"
      description="Rules agents must respect whatever the fit score, in your own words."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={rules.fields.length >= NEVER_APPLY_MAX}
          onClick={() => {
            rules.append({ value: "" }, { shouldFocus: true });
          }}
        >
          <Plus aria-hidden="true" />
          Add rule
        </Button>
      }
    >
      {rules.fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No rule yet.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {rules.fields.map((item, index) => (
            <li key={item.id} className="flex items-end gap-2">
              <TextField
                control={form.control}
                name={`never_apply.${index}.value`}
                label={`Rule ${index + 1}`}
                placeholder="No cybersecurity roles."
                className="min-w-0 flex-1"
              />
              <ItemControls
                index={index}
                count={rules.fields.length}
                name={`rule ${index + 1}`}
                onMove={rules.move}
                onRemove={rules.remove}
              />
            </li>
          ))}
        </ol>
      )}
    </Group>
  );
}

/**
 * The automatic search, read only: Jobs (next to Find jobs) edits it. The Search tab carries the
 * value through every save unchanged.
 */
function AutomaticSearch({ schedule }: { schedule: SweepSchedule }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
      <span>
        <span className="font-medium">Automatic search:</span>{" "}
        {describeSchedule(schedule)}
      </span>
      <Link
        to="/jobs"
        className="text-primary underline-offset-4 hover:underline"
      >
        Change in Jobs
      </Link>
    </p>
  );
}
