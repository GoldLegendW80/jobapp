import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";
import { Link } from "react-router";

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
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
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
  interactiveSettingsSchema,
  profileKey,
  saveSection,
  type InteractiveSite,
  type BrowserSource,
  type Profile,
} from "@/features/profile/api";
import {
  fromSearchForm,
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "@/features/profile/editor-forms";
import { TargetFields } from "@/features/profile/target-fields";
import { TermChips } from "@/features/profile/term-chips";
import { describeError, isApiError } from "@/lib/api";
import type { SweepRunner } from "./use-sweep-runner";

const PRESETS = [
  { platform: "linkedin", label: "LinkedIn", url: "https://www.linkedin.com/" },
  {
    platform: "welcome_to_the_jungle",
    label: "Welcome to the Jungle",
    url: "https://www.welcometothejungle.com/",
  },
  { platform: "indeed", label: "Indeed", url: "https://www.indeed.com/" },
  {
    platform: "freework",
    label: "Free-Work",
    url: "https://www.free-work.com/",
  },
] as const;

export function SearchSetup({
  runner,
  onClose,
  onReturnFocus,
}: {
  runner: SweepRunner;
  onClose: () => void;
  onReturnFocus: () => void;
}) {
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
          <DialogTitle>Find jobs setup</DialogTitle>
          <DialogDescription>
            Choose where to search and what to look for. Save your settings
            before starting.
          </DialogDescription>
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
  const client = useQueryClient();
  const id = useId();
  const form = useForm<SearchForm>({
    resolver: zodResolver(searchSchema),
    defaultValues: toSearchForm(profile.sections.search),
  });
  const [sites, setSites] = useState<InteractiveSite[]>(() =>
    structuredClone(profile.sections.search.interactive_sites ?? []),
  );
  const [instructions, setInstructions] = useState(
    profile.sections.search.interactive_instructions ?? "",
  );
  const [custom, setCustom] = useState("");
  const [revision, setRevision] = useState(profile.revision);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  useEffect(() => {
    onBusyChange(saving);
  }, [saving, onBusyChange]);
  const submit = async (values: SearchForm) => {
    if (busy.current || conflict) return;
    const settings = interactiveSettingsSchema.safeParse({
      interactive_sites: sites,
      interactive_instructions: instructions,
    });
    if (!settings.success) {
      setError(
        settings.error.issues[0]?.message ??
          "Check your websites and instructions.",
      );
      return;
    }
    if (custom.trim()) {
      setError("Add the custom website or clear its address before starting.");
      return;
    }
    busy.current = true;
    setSaving(true);
    setError(null);
    try {
      const saved = await saveSection("search", revision, {
        ...fromSearchForm(values),
        ...settings.data,
      });
      setRevision(saved.revision);
      client.setQueryData(profileKey, saved);
      await runner.start(saved.revision);
      onClose();
    } catch (failure) {
      if (
        conflictOf(failure) ||
        (isApiError(failure) && failure.status === 409)
      ) {
        setConflict(true);
        setError(
          "Your profile changed in another tab. Your draft is still here. Load current settings and review them before starting.",
        );
      } else setError(`The search did not start. ${describeError(failure)}`);
    } finally {
      busy.current = false;
      setSaving(false);
    }
  };
  const addCustom = () => {
    const next: InteractiveSite[] = [
      ...sites,
      {
        platform: "custom",
        url: custom.trim(),
        mode: "llm_browser",
        enabled: true,
      },
    ];
    const parsed = interactiveSettingsSchema.safeParse({
      interactive_sites: next,
      interactive_instructions: instructions,
    });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check this address.");
      return;
    }
    setSites(parsed.data.interactive_sites);
    setCustom("");
    setError(null);
  };
  return (
    <Form {...form}>
      <form
        noValidate
        onSubmit={(event) => void form.handleSubmit(submit)(event)}
        className="space-y-6"
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
          disabled={saving}
          aria-busy={saving}
          className="min-w-0 space-y-6"
        >
          <section className="space-y-3" aria-labelledby={`${id}-sites`}>
            <h3 id={`${id}-sites`} className="font-medium">
              Websites
            </h3>
            <p className="text-sm text-muted-foreground">
              LLM with Playwright: the model browses your selected websites. Up
              to 10 websites. Selecting a website does not grant browser
              permission or accept its account risks.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {PRESETS.map((preset) => (
                <label
                  key={preset.platform}
                  className="flex items-center gap-2 rounded-md border p-3 text-sm"
                >
                  <input
                    type="checkbox"
                    className="size-4 accent-primary"
                    checked={sites.some(
                      (site) =>
                        site.platform === preset.platform && site.enabled,
                    )}
                    onChange={(event) => {
                      if (!event.target.checked) {
                        setSites(
                          sites.filter(
                            (site) => site.platform !== preset.platform,
                          ),
                        );
                        return;
                      }
                      if (
                        sites.some((site) => site.platform === preset.platform)
                      )
                        setSites(
                          sites.map((site) =>
                            site.platform === preset.platform
                              ? { ...site, enabled: true }
                              : site,
                          ),
                        );
                      else if (sites.length < 10)
                        setSites([
                          ...sites,
                          {
                            platform: preset.platform,
                            url: preset.url,
                            enabled: true,
                            mode: "llm_browser",
                          },
                        ]);
                      else
                        setError(
                          "Choose at most 10 websites. Remove a website to add another.",
                        );
                    }}
                  />
                  {preset.label}
                </label>
              ))}
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${id}-custom`}>Custom website URL</Label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  id={`${id}-custom`}
                  type="url"
                  maxLength={2000}
                  value={custom}
                  onChange={(event) => {
                    setCustom(event.target.value);
                  }}
                  placeholder="https://careers.example.org/"
                />
                <Button type="button" variant="outline" onClick={addCustom}>
                  Add website
                </Button>
              </div>
            </div>
            {sites.length ? (
              <ul className="divide-y rounded-md border">
                {sites.map((site, index) => (
                  <li
                    key={`${site.platform}:${index}`}
                    className="flex min-w-0 items-center gap-2 p-2 text-sm"
                  >
                    <span className="min-w-0 flex-1 break-all">
                      {site.url}
                      {site.enabled ? "" : " (off)"}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove ${site.url}`}
                      onClick={() => {
                        setSites(sites.filter((_, at) => at !== index));
                      }}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
          <section aria-label="Search criteria" className="space-y-4">
            <h3 className="font-medium">Search criteria</h3>
            <TermChips
              form={form}
              name="title_include"
              label="Titles to include"
            />
            <TermChips
              form={form}
              name="title_exclude"
              label="Titles to exclude"
            />
            <TermChips form={form} name="location" label="Locations" />
            <CriteriaFields form={form} />
            <TargetFields form={form} />
          </section>
          <div className="space-y-2">
            <Label htmlFor={`${id}-instructions`}>
              Instructions for this search
            </Label>
            <Textarea
              id={`${id}-instructions`}
              maxLength={2000}
              rows={3}
              value={instructions}
              onChange={(event) => {
                setInstructions(event.target.value);
              }}
            />
            <p className="text-xs text-muted-foreground">
              Optional, up to 2,000 characters. These instructions are saved for
              your next search.
            </p>
          </div>
          <OtherSources
            form={form}
            consentOnly={(profile.sections.search.browser_sources ?? []).filter(
              (source) => source.interactive_only === true,
            )}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={conflict || saving}>
              {saving ? "Saving and starting…" : "Save and Start"}
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
                          ? "Office"
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
              {contract}
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

function OtherSources({
  form,
  consentOnly,
}: {
  form: UseFormReturn<SearchForm>;
  consentOnly: readonly BrowserSource[];
}) {
  const values = form.getValues();
  return (
    <section
      aria-label="Other search sources"
      className="space-y-3 rounded-md border p-3"
    >
      <h3 className="text-sm font-medium">Other search sources</h3>
      <p className="text-xs text-muted-foreground">
        These use their own API, feed or existing reader. They stay separate
        from LLM with Playwright.
      </p>
      {values.browser_sources.some((source) =>
        ["linkedin", "freework"].includes(source.kind),
      ) ? (
        <p className="text-xs text-muted-foreground">
          Protected readers use your existing browser permissions. Manage
          browser access from Account.
        </p>
      ) : null}
      {values.boards.map((board, index) => (
        <label
          key={`${board.ats}:${board.org}`}
          className="flex min-w-0 items-center gap-2 break-all text-sm"
        >
          <input
            type="checkbox"
            className="size-4 shrink-0 accent-primary"
            {...form.register(`boards.${index}.enabled`)}
          />
          {board.company} (API board)
        </label>
      ))}
      {values.feeds.map((feed, index) => (
        <label
          key={feed.kind}
          className="flex min-w-0 items-center gap-2 break-all text-sm"
        >
          <input
            type="checkbox"
            className="size-4 shrink-0 accent-primary"
            {...form.register(`feeds.${index}.enabled`)}
          />
          {
            {
              remoteok: "Remote OK",
              weworkremotely: "We Work Remotely",
              hackernews_freelance: "Hacker News freelance",
              hackernews_hiring: "Hacker News hiring",
            }[feed.kind]
          }{" "}
          (feed)
        </label>
      ))}
      {values.browser_sources.map((source, index) =>
        consentOnly.some(
          (row) => row.kind === source.kind && row.query === source.query,
        ) ? null : (
          <label
            key={`${source.kind}:${index}`}
            className="flex min-w-0 items-center gap-2 break-all text-sm"
          >
            <input
              type="checkbox"
              className="size-4 shrink-0 accent-primary"
              disabled={["linkedin", "freework"].includes(source.kind)}
              {...form.register(`browser_sources.${index}.enabled`)}
            />
            {source.kind === "google_careers"
              ? "Google Careers (fixed reader)"
              : source.kind === "vie"
                ? "VIE (fixed reader)"
                : source.kind === "freework"
                  ? "Free-Work (existing reader)"
                  : source.kind === "linkedin"
                    ? "LinkedIn (existing reader)"
                    : `${source.query} (legacy finder)`}
          </label>
        ),
      )}
      <Link to="/profile?tab=search" className="text-sm underline">
        Manage API boards, feeds and search options
      </Link>
    </section>
  );
}
