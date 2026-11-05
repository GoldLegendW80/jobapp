import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search as SearchIcon } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";

import { AdvancedSection } from "@/components/advanced-section";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Form,
} from "@/components/ui/form";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { Spinner } from "@/components/ui/spinner";
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
import { useIsPhone } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";
import { contractWord } from "@/lib/wording";

import { BLOCK, BLOCKS, COLUMN, HANG, StepNumber } from "./setup-grid";
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
 * list (use-sources.ts), then the search starts on the saved revision. A bottom sheet on a
 * phone, a dialog above; its words and fields keep one edge (setup-grid.tsx).
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
    <SetupSurface
      onClose={() => {
        if (!runner.starting && !busy) onClose();
      }}
      onReturnFocus={onReturnFocus}
    >
      {profile.isPending ? (
        <div className={cn(BODY, COLUMN)}>
          <p role="status" className="text-sm">
            Loading your search settings…
          </p>
        </div>
      ) : profile.isError ? (
        <div className={cn(BODY, COLUMN)}>
          <div role="alert" className="flex flex-col items-start gap-3 text-sm">
            <p>
              Your search settings could not be loaded.{" "}
              {describeError(profile.error)}
            </p>
            <Button variant="outline" onClick={() => void profile.refetch()}>
              Try again
            </Button>
          </div>
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
    </SetupSurface>
  );
}

/** The scrolling middle of the setup, between its header and its footer, which stay in view. */
const BODY =
  "min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 sm:px-6";
/** The header's and the footer's sides, as the body's. */
const EDGES = "shrink-0 px-5 sm:px-6";

const INTRO =
  "Add the places to search, say what you want, and start. The app keeps the postings that match.";

/**
 * Where the setup opens: a sheet from the bottom of the screen on a phone (below `sm`, rounded
 * top corners, a grab handle, at most 92% of the screen's height), a dialog in the middle above
 * it. Either way the header and the footer stay in view and the middle scrolls.
 */
function SetupSurface({
  onClose,
  onReturnFocus,
  children,
}: {
  /** Asked to close (Escape, the close button, a click outside). */
  onClose: () => void;
  onReturnFocus: () => void;
  children: ReactNode;
}) {
  const phone = useIsPhone();
  const onOpenChange = (open: boolean) => {
    if (!open) onClose();
  };
  const onCloseAutoFocus = (event: Event) => {
    event.preventDefault();
    onReturnFocus();
  };
  // The title's glyph in the gutter of the grid below, so the title starts at the text's edge.
  const glyph = (
    <span
      aria-hidden="true"
      className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-primary"
    >
      <SearchIcon className="size-4" />
    </span>
  );
  const header =
    "grid grid-cols-[2.25rem_minmax(0,1fr)] items-center gap-x-0 gap-y-1";
  if (phone)
    return (
      <Sheet open onOpenChange={onOpenChange}>
        <SheetContent
          side="bottom"
          onCloseAutoFocus={onCloseAutoFocus}
          className="max-h-[92dvh] gap-0 overflow-hidden rounded-t-2xl p-0 motion-reduce:animate-none!"
        >
          <div
            aria-hidden="true"
            className="mx-auto mt-2 h-1.5 w-10 shrink-0 rounded-full bg-muted-foreground/30"
          />
          <SheetHeader
            className={cn(EDGES, header, "border-b pt-2 pr-12 pb-3")}
          >
            {glyph}
            <SheetTitle className="text-lg leading-tight">Find jobs</SheetTitle>
            <SheetDescription className="col-start-2">{INTRO}</SheetDescription>
          </SheetHeader>
          {children}
        </SheetContent>
      </Sheet>
    );
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent
        onCloseAutoFocus={onCloseAutoFocus}
        className="flex max-h-[calc(100dvh-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl"
      >
        <DialogHeader
          className={cn(EDGES, header, "border-b pt-6 pr-12 pb-4 text-left")}
        >
          {glyph}
          <DialogTitle className="leading-tight">Find jobs</DialogTitle>
          <DialogDescription className="col-start-2">{INTRO}</DialogDescription>
        </DialogHeader>
        {children}
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
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className={BODY}>
          <fieldset
            disabled={locked}
            aria-busy={locked}
            className="flex min-w-0 flex-col gap-6"
          >
            <SourcesEditor
              sources={sources}
              reviewed={reviewed}
              disabled={locked}
              step={1}
            />
            <section
              aria-labelledby={`${id}-want`}
              className={cn(COLUMN, BLOCKS, "border-t pt-6")}
            >
              <h3
                id={`${id}-want`}
                className={cn(
                  HANG,
                  "items-center text-base font-semibold tracking-tight",
                )}
              >
                <StepNumber step={2} />
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
                {/* The profile's "What you aim for" box, without its border and padding here, so
                  its words keep the setup's one edge. */}
                <div className="[&>section]:rounded-none [&>section]:border-0 [&>section]:p-0 sm:[&>section]:p-0">
                  <TargetFields form={form} />
                </div>
                <Field className={BLOCK}>
                  <FieldLabel
                    htmlFor={`${id}-instructions`}
                    className="leading-none"
                  >
                    Instructions for the assistant
                  </FieldLabel>
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
                  <FieldDescription className="text-xs leading-4">
                    Optional, up to 2,000 characters, kept for your next
                    searches.
                  </FieldDescription>
                </Field>
              </AdvancedSection>
            </section>
          </fieldset>
        </div>
        {/* Always in view, under the scrolling middle: so is what went wrong after a click. */}
        <div
          className={cn(
            EDGES,
            "flex flex-col gap-3 border-t pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:py-4",
          )}
        >
          {error ? (
            <div
              role="alert"
              className="flex max-h-40 flex-col items-start gap-2 overflow-y-auto rounded-md border border-destructive p-3 text-sm"
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
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={locked}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              className="flex-1 sm:flex-none"
              disabled={conflict || locked}
            >
              {locked ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : (
                <SearchIcon aria-hidden="true" />
              )}
              {locked ? "Saving and starting…" : "Save and start search"}
            </Button>
          </div>
        </div>
      </form>
    </Form>
  );
}

function CriteriaFields({ form }: { form: UseFormReturn<SearchForm> }) {
  const id = useId();
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
      <FormField
        control={form.control}
        name="contract_types"
        render={({ field }) => (
          <FieldSet className="gap-2">
            <FieldLegend variant="label" className="mb-2">
              Contract types
            </FieldLegend>
            <FieldGroup
              data-slot="checkbox-group"
              className="flex-row flex-wrap gap-3"
            >
              {CONTRACT_TYPES.map((contract) => (
                <Field
                  key={contract}
                  orientation="horizontal"
                  className="w-auto gap-2"
                >
                  <Checkbox
                    id={`${id}-contract-${contract}`}
                    checked={field.value.includes(contract)}
                    onBlur={field.onBlur}
                    onCheckedChange={(checked) => {
                      // As the boxes of one name did: the ticked ones, in the order shown.
                      field.onChange(
                        CONTRACT_TYPES.filter((one) =>
                          one === contract
                            ? checked === true
                            : field.value.includes(one),
                        ),
                      );
                    }}
                  />
                  <FieldLabel
                    htmlFor={`${id}-contract-${contract}`}
                    className="font-normal"
                  >
                    {contractWord(contract)}
                  </FieldLabel>
                </Field>
              ))}
            </FieldGroup>
          </FieldSet>
        )}
      />
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
