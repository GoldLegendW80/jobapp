/**
 * Search: what the search looks for, one settings section per group. The four plain lists, job
 * titles, unwanted titles, places and unwanted places, as chips (search-lists.tsx), the remote
 * stance and contract types, what the person aims for (target-fields.tsx, O4), the minimum fit to
 * shortlist and the posting age window, where the search runs with the sign in to job websites
 * (B17), the never-apply rules and the writing rules. Then, with its own Save, the preferences
 * section (preferences-card.tsx). Searches run the job websites
 * ticked in Find jobs alone (owner, 2026-10-28): the company boards, the starter list, the fixed
 * browser readers and the freelance feeds are deleted, and the api drops any it still holds.
 */
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { FieldTitle } from "@/components/ui/field";
import { NativeSelect } from "@/components/ui/native-select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { JobWebsitesSignIn } from "@/components/shared/browser/site-signin";
import { REMOTE_WORDS, contractWord } from "@/lib/wording";

import {
  CONTRACT_TYPES,
  REMOTE_STANCES,
  type Profile,
  type SweepSchedule,
} from "../api/profile-api";
import { PreferencesCard } from "./preferences-card";
import { WritingRulesCard } from "./writing-rules-card";
import { SearchLists } from "@/components/shared/profile/search-lists";
import { SettingsSection } from "@/components/shared/profile/settings-frame";
import {
  BONUS_NOTE,
  TargetFields,
} from "@/components/shared/profile/target-fields";
import { describeSchedule } from "../helpers/schedule";
import {
  fromSearchForm,
  NEVER_APPLY_MAX,
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "../helpers/editor-forms";
import {
  ConflictDialog,
  FormErrorAlert,
  ItemControls,
  SaveBar,
  TextField,
} from "@/components/shared/profile/section-ui";
import { useSectionForm } from "../api/use-section-form";

// The same words as the wizard and the postings (lib/wording.ts): one name for one thing.
const STANCE_LABELS: Record<(typeof REMOTE_STANCES)[number], string> =
  REMOTE_WORDS;

export function SearchTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  return <SearchForms profile={profile} onDirtyChange={onDirtyChange} />;
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
      rules: { wrapped: ["never_apply"] },
      onDirtyChange: onSearchDirty,
    });
  const { control } = form;

  return (
    <>
      <Form {...form}>
        <form
          noValidate
          aria-label="Search"
          className="flex flex-col gap-8"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <SettingsSection
            title="Titles and places"
            description="Each word or phrase matches as a whole word, in any case, with an optional plural."
          >
            <SearchLists form={form} />
          </SettingsSection>
          <SettingsSection
            title="Where and how"
            description="How far from home you work, and on which contracts."
          >
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
          </SettingsSection>
          <SettingsSection title="What you aim for" description={BONUS_NOTE}>
            <TargetFields form={form} bare />
          </SettingsSection>
          <SettingsSection
            title="Shortlist"
            description="Which of the postings found are proposed for your shortlist."
          >
            <FormField
              control={control}
              name="min_fit"
              render={({ field }) => (
                <FormItem className="max-w-md">
                  <div className="flex items-baseline justify-between gap-2">
                    <FormLabel>Minimum fit</FormLabel>
                    <output className="text-sm tabular-nums text-muted-foreground">
                      {field.value} / 100
                    </output>
                  </div>
                  <FormControl>
                    <Slider
                      min={0}
                      max={100}
                      step={1}
                      name={field.name}
                      aria-label="Minimum fit"
                      value={[field.value]}
                      onBlur={field.onBlur}
                      onValueChange={([value]) => {
                        if (value !== undefined) field.onChange(value);
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
          </SettingsSection>
          {/* Owner, 2026-10-28: searches run the job websites ticked in Find jobs alone. The
              company boards, fixed readers and feeds are deleted; the api drops any it holds. */}
          <SettingsSection
            title="Where the search runs"
            description="Searches run the job websites ticked in Find jobs."
          >
            <AutomaticSearch schedule={form.getValues("sweep_schedule")} />
            {/* B17: sign in to a job website of Find jobs, then searches read it signed in. */}
            <JobWebsitesSignIn
              urls={(profile.sections.search.interactive_sites ?? []).map(
                (site) => site.url,
              )}
            />
          </SettingsSection>
          <NeverApply form={form} />
          <WritingRulesCard />
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
    </>
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
            <FieldTitle id="contract-types-label">Contract types</FieldTitle>
            <ToggleGroup
              type="multiple"
              variant="outline"
              spacing={2}
              size="sm"
              aria-labelledby="contract-types-label"
              value={selected}
              onValueChange={(next) => {
                // Kept in the order of the list, whatever the order of the clicks.
                field.onChange(
                  CONTRACT_TYPES.filter((type) => next.includes(type)),
                );
              }}
              className="flex-wrap"
            >
              {CONTRACT_TYPES.map((type) => (
                <ToggleGroupItem key={type} value={type}>
                  {contractWord(type)}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
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
    <SettingsSection
      title="Never apply"
      description="Rules agents must respect whatever the fit score, in your own words."
      actions={
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
                placeholder="No gambling: casino"
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
    </SettingsSection>
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
      <Link to="/jobs" className="font-medium underline underline-offset-4">
        Change in Jobs
      </Link>
    </p>
  );
}
