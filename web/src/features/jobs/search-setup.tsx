import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Search as SearchIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";

import { AdvancedSection } from "@/components/advanced-section";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Form,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  CONTRACT_TYPES,
  REMOTE_STANCES,
  conflictOf,
  getProfile,
  profileKey,
  type Profile,
} from "@/features/profile/api";
import {
  fromSearchForm,
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "@/features/profile/editor-forms";
import { SearchLists } from "@/features/profile/search-lists";
import { TargetFields } from "@/features/profile/target-fields";
import { describeError, isApiError } from "@/lib/api";
import { contractWord } from "@/lib/wording";

import {
  PROTECTED,
  READERS,
  permissionKey,
  type Protected,
} from "./site-permissions";
import { SourcesEditor } from "./sources-editor";
import { SourcesFailure, useSources } from "./use-sources";
import type { SweepRunner } from "./use-sweep-runner";

/** The fields of "What you want" folded under More options: a mistake there opens it. */
const MORE_FIELDS = new Set([
  "title_exclude",
  "location_exclude",
  "remote_stance",
  "contract_types",
  "days",
  "seniority",
  "industries",
  "company_types",
  "compensation",
]);

/**
 * Find jobs: where to search (one list of addresses, sources-editor.tsx) and what to look for,
 * then Save and start search. Saving records the permission of each known job website in the
 * list (use-sources.ts), then the search starts on the saved revision.
 */
export function SearchSetup({
  runner,
  onClose,
  onReturnFocus,
}: {
  runner: SweepRunner;
  onClose: () => void;
  onReturnFocus: () => void;
}) {
  const client = useQueryClient();
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
    retry: false,
  });
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !runner.starting && !busy) onClose();
      }}
    >
      <DialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onReturnFocus();
        }}
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl [&>*]:min-w-0"
      >
        <DialogHeader>
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
            >
              <SearchIcon className="size-5" />
            </span>
            <div className="min-w-0 space-y-1 text-left">
              <DialogTitle>Find jobs</DialogTitle>
              <DialogDescription>
                Add the places to search, say what you want, and start. The app
                keeps the postings that match.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>
        {profile.isPending ? (
          <p role="status">Loading your search settings…</p>
        ) : profile.isError ? (
          <div role="alert" className="space-y-3 text-sm">
            <p>
              Your search settings could not be loaded.{" "}
              {describeError(profile.error)}
            </p>
            <Button variant="outline" onClick={() => void profile.refetch()}>
              Try again
            </Button>
          </div>
        ) : (
          <SetupForm
            key={generation}
            profile={profile.data}
            runner={runner}
            onClose={onClose}
            onBusyChange={setBusy}
            onReload={async () => {
              for (const platform of PROTECTED)
                void client.invalidateQueries({
                  queryKey: permissionKey(platform),
                });
              const current = await profile.refetch();
              if (current.isSuccess) setGeneration((value) => value + 1);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function SetupForm({
  profile,
  runner,
  onClose,
  onReload,
  onBusyChange,
}: {
  profile: Profile;
  runner: SweepRunner;
  onClose: () => void;
  onReload: () => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const id = useId();
  const form = useForm<SearchForm>({
    resolver: zodResolver(searchSchema),
    defaultValues: toSearchForm(profile.sections.search),
  });
  const sources = useSources(profile.sections.search);
  const [instructions, setInstructions] = useState(
    profile.sections.search.interactive_instructions ?? "",
  );
  const [revision, setRevision] = useState(profile.revision);
  const [reviewed, setReviewed] = useState(() =>
    structuredClone(profile.sections.search),
  );
  const [more, setMore] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    onBusyChange(saving);
  }, [saving, onBusyChange]);
  // The current profile becomes the reviewed one: its LinkedIn and Free-Work rows, which a
  // consent write may have added, join the draft's other sources.
  const adopt = (current: Profile) => {
    const ordinary = form
      .getValues("browser_sources")
      .filter((source) => !READERS.has(source.kind as Protected));
    const readers = toSearchForm(
      current.sections.search,
    ).browser_sources.filter((source) => READERS.has(source.kind as Protected));
    form.setValue("browser_sources", [...ordinary, ...readers]);
    setRevision(current.revision);
    setReviewed(structuredClone(current.sections.search));
  };
  const submit = async (values: SearchForm) => {
    if (busy.current || conflict) return;
    busy.current = true;
    setSaving(true);
    setError(null);
    try {
      const draft = fromSearchForm(values);
      const current = await sources.save({
        base: {
          ...profile,
          revision,
          sections: { ...profile.sections, search: reviewed },
        },
        body: draft,
        titles: draft.title_include.terms,
        instructions,
        onAdopt: adopt,
      });
      if (!current || !mounted.current) return;
      await runner.start(current.revision);
      onClose();
    } catch (failure) {
      if (!mounted.current) return;
      if (failure instanceof SourcesFailure) {
        if (failure.conflict) setConflict(true);
        setError(failure.message);
      } else if (
        conflictOf(failure) ||
        (isApiError(failure) && failure.status === 409)
      ) {
        setConflict(true);
        setError(
          "Your profile changed in another tab. Your choices are still here. Load current settings and review them before starting.",
        );
      } else setError(`The search did not start. ${describeError(failure)}`);
    } finally {
      busy.current = false;
      if (mounted.current) setSaving(false);
    }
  };
  const locked = saving || sources.saving;
  return (
    <Form {...form}>
      <form
        noValidate
        onSubmit={(event) =>
          void form.handleSubmit(submit, (errors) => {
            if (Object.keys(errors).some((name) => MORE_FIELDS.has(name)))
              setMore(true);
          })(event)
        }
        className="space-y-8"
      >
        {error ? (
          <div
            role="alert"
            className="space-y-2 rounded-md border border-destructive p-3 text-sm"
          >
            <p>{error}</p>
            {conflict ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => void onReload()}
              >
                Load current settings
              </Button>
            ) : null}
          </div>
        ) : null}
        <fieldset
          disabled={locked}
          aria-busy={locked}
          className="min-w-0 space-y-8"
        >
          <SourcesEditor
            sources={sources}
            reviewed={reviewed}
            disabled={locked}
            step={1}
          />
          <section
            aria-labelledby={`${id}-want`}
            className="space-y-4 border-t pt-6"
          >
            <h3
              id={`${id}-want`}
              className="flex items-center gap-2 text-base font-semibold tracking-tight"
            >
              <span
                aria-hidden="true"
                className="flex size-7 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground"
              >
                2
              </span>
              What you want
            </h3>
            <SearchLists form={form} only={["title_include", "location"]} />
            <AdvancedSection
              title="More options"
              keepMounted
              open={more}
              onOpenChange={setMore}
            >
              <SearchLists
                form={form}
                only={["title_exclude", "location_exclude"]}
              />
              <CriteriaFields form={form} />
              <TargetFields form={form} />
              <div className="space-y-2">
                <Label htmlFor={`${id}-instructions`}>
                  Instructions for the assistant
                </Label>
                <Textarea
                  id={`${id}-instructions`}
                  maxLength={2000}
                  rows={3}
                  value={instructions}
                  placeholder="For example: only roles open to juniors, skip agencies."
                  onChange={(event) => {
                    setInstructions(event.target.value);
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Optional, up to 2,000 characters, kept for your next searches.
                </p>
              </div>
            </AdvancedSection>
          </section>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={conflict || locked}>
              {locked ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : (
                <SearchIcon aria-hidden="true" />
              )}
              {locked ? "Saving and starting…" : "Save and start search"}
            </Button>
          </DialogFooter>
        </fieldset>
      </form>
    </Form>
  );
}

function CriteriaFields({ form }: { form: UseFormReturn<SearchForm> }) {
  return (
    <>
      <FormField
        control={form.control}
        name="remote_stance"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Remote preference</FormLabel>
            <FormControl>
              <NativeSelect {...field}>
                <option value="">Not set</option>
                {REMOTE_STANCES.map((stance) => (
                  <option key={stance} value={stance}>
                    {stance === "remote"
                      ? "Remote only"
                      : stance === "hybrid"
                        ? "Hybrid"
                        : stance === "office"
                          ? "On site"
                          : "Any"}
                  </option>
                ))}
              </NativeSelect>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Contract types</legend>
        <div className="flex flex-wrap gap-3">
          {CONTRACT_TYPES.map((contract) => (
            <label key={contract} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                value={contract}
                {...form.register("contract_types")}
              />
              {contractWord(contract)}
            </label>
          ))}
        </div>
      </fieldset>
      <FormField
        control={form.control}
        name="days"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Posted within the last days</FormLabel>
            <FormControl>
              <Input
                {...field}
                type="number"
                min={1}
                max={365}
                className="max-w-32"
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </>
  );
}
