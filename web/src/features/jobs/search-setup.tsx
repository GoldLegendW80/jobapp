import { zodResolver } from "@hookform/resolvers/zod";
import {
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";

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
import { linkedinKey } from "@/features/browser/linkedin-api";
import { SiteSignIn } from "@/features/browser/site-signin";
import {
  CONTRACT_TYPES,
  REMOTE_STANCES,
  conflictOf,
  getProfile,
  interactiveSettingsSchema,
  profileKey,
  saveSection,
  type CompanyBoard,
  type InteractiveSite,
  type Profile,
  type Search,
} from "@/features/profile/api";
import {
  fromSearchForm,
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "@/features/profile/editor-forms";
import { SearchLists } from "@/features/profile/search-lists";
import { TargetFields } from "@/features/profile/target-fields";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import { getStarterBoards, savedBoard, starterKey } from "./boards-api";
import { CompanyBoards } from "./company-boards";
import { formatDateTime } from "./format";
import {
  PROTECTED,
  READERS,
  getPermission,
  permissionKey,
  permissionPlan,
  platformOf,
  putPermission,
  readerWords,
  tickedOf,
  withdraws,
  type Permission,
  type PermissionChange,
  type Protected,
} from "./site-permissions";
import type { SweepRunner } from "./use-sweep-runner";

const PRESETS = [
  { platform: "linkedin", label: "LinkedIn", url: "https://www.linkedin.com/" },
  {
    platform: "welcome_to_the_jungle",
    label: "Welcome to the Jungle",
    url: "https://www.welcometothejungle.com/en/jobs",
  },
  { platform: "indeed", label: "Indeed", url: "https://www.indeed.com/" },
  {
    platform: "freework",
    label: "Free-Work",
    url: "https://www.free-work.com/",
  },
] as const;

const LABELS: Record<Protected, string> = {
  linkedin: "LinkedIn",
  welcome_to_the_jungle: "Welcome to the Jungle",
  indeed: "Indeed",
  freework: "Free-Work",
};

/** A consent write of a Save that failed; `conflict` when current settings must be loaded. */
class PermissionFailure extends Error {
  readonly conflict: boolean;
  constructor(message: string, conflict: boolean) {
    super(message);
    this.conflict = conflict;
  }
}

/** The search as the setup reviewed it, without what a consent write of `except` changes. */
function ordinarySearch(search: Search, except?: Protected) {
  const { browser_sources: sources = [], ...settings } = fromSearchForm(
    toSearchForm(search),
  );
  const protectedKinds = ["linkedin", "freework"];
  return {
    ...settings,
    browser_sources: sources.filter(
      (source) => !protectedKinds.includes(source.kind),
    ),
    legacy_readers: sources
      .filter(
        (source) =>
          protectedKinds.includes(source.kind) &&
          source.kind !== except &&
          !search.browser_sources?.some(
            (row) => row.kind === source.kind && row.interactive_only === true,
          ),
      )
      .map(({ kind, query, location }) => ({ kind, query, location }))
      .sort((a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0)),
    interactive_sites: search.interactive_sites ?? [],
    interactive_instructions: search.interactive_instructions ?? "",
  };
}

/** LinkedIn and Free-Work rows whose permission serves the websites' search only. */
function websitesOnlyRows(search: Search): Set<Protected> {
  const found = new Set<Protected>();
  // Every row is LinkedIn or Free-Work: the api keeps no other kind (owner, 2026-10-28).
  for (const row of search.browser_sources ?? [])
    if (row.interactive_only === true) found.add(row.kind);
  return found;
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stable(record[key])}`)
      .join(",")}}`;
  }
  if (
    value === undefined ||
    typeof value === "function" ||
    typeof value === "symbol"
  )
    return "null";
  return JSON.stringify(value);
}

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
  const client = useQueryClient();
  const id = useId();
  const form = useForm<SearchForm>({
    resolver: zodResolver(searchSchema),
    defaultValues: toSearchForm(profile.sections.search),
  });
  const [sites, setSites] = useState<InteractiveSite[]>(() =>
    structuredClone(profile.sections.search.interactive_sites ?? []),
  );
  // The platforms ticked when the setup opened: unticking one takes its permission back.
  const [opened] = useState(() =>
    tickedOf(profile.sections.search.interactive_sites ?? []),
  );
  const [instructions, setInstructions] = useState(
    profile.sections.search.interactive_instructions ?? "",
  );
  const [custom, setCustom] = useState("");
  // Company job boards (owner, 2026-10-30): never saved, the starter list is shown ticked and
  // Save stores it; a list saved before is the person's own. Sent only once shown or changed.
  const savedBoards = profile.sections.search.company_boards ?? null;
  const [edited, setEdited] = useState<CompanyBoard[] | null>(() =>
    savedBoards ? structuredClone(savedBoards) : null,
  );
  const starter = useQuery({
    queryKey: starterKey,
    queryFn: ({ signal }) => getStarterBoards(signal),
    enabled: savedBoards === null,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  const seeded = starter.data ? starter.data.boards.map(savedBoard) : null;
  // null while the starter list loads; an empty list when it could not be loaded (not sent).
  const boards = edited ?? seeded ?? (starter.isError ? [] : null);
  const sendBoards = edited !== null || seeded !== null;
  const [revision, setRevision] = useState(profile.revision);
  const [reviewed, setReviewed] = useState(() =>
    structuredClone(profile.sections.search),
  );
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const mounted = useRef(true);
  const isMounted = () => mounted.current;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const permissions = useQueries({
    queries: PROTECTED.map((platform) => ({
      queryKey: permissionKey(platform),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        getPermission(platform, signal),
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    })),
  });
  const permissionOf = (platform: Protected) => {
    const state = permissions[PROTECTED.indexOf(platform)];
    if (!state) throw new Error(`No permission query for ${platform}.`);
    return state;
  };
  const ticked = tickedOf(sites);
  const isCurrent = (current: Profile) =>
    current.revision >=
    Math.max(
      revision,
      client.getQueryData<Profile>(profileKey)?.revision ?? revision,
    );
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
    client.setQueryData(profileKey, current);
    setRevision(current.revision);
    setReviewed(structuredClone(current.sections.search));
  };
  // One consent write on top of the reviewed profile `base`, then the profile it made, checked
  // to differ from `base` by that permission only (a change from another tab is a conflict).
  const record = async (
    change: PermissionChange,
    base: Profile,
  ): Promise<Profile | null> => {
    const label = LABELS[change.platform];
    const key = permissionKey(change.platform);
    let answer: Permission;
    try {
      answer = await putPermission(change.platform, {
        ...change.body,
        base_revision: base.revision,
      });
    } catch (failure) {
      void client.invalidateQueries({ queryKey: key });
      if (isApiError(failure) && failure.status === 409)
        throw new PermissionFailure(
          `The ${label} permission or its risk sentence changed in another tab. Your draft is still here. Load current settings and review them before starting.`,
          true,
        );
      if (
        isApiError(failure) &&
        failure.kind === "http" &&
        (failure.status ?? 500) < 500
      )
        throw new PermissionFailure(
          `The ${label} permission was not saved. ${errorDetail(failure) ?? describeError(failure)}`,
          false,
        );
      throw new PermissionFailure(
        `The ${label} permission was not confirmed. Your draft is still here. Load current settings and review them before starting. ${describeError(failure)}`,
        true,
      );
    }
    client.setQueryData(key, answer);
    if (change.platform === "linkedin")
      void client.invalidateQueries({ queryKey: linkedinKey });
    if (!isMounted()) return null;
    let current: Profile;
    try {
      current = await getProfile();
    } catch (failure) {
      throw new PermissionFailure(
        `The ${label} permission was not confirmed. Your draft is still here. Load current settings and review them before starting. ${describeError(failure)}`,
        true,
      );
    }
    if (!isMounted()) return null;
    if (
      !isCurrent(current) ||
      stable(ordinarySearch(current.sections.search, change.platform)) !==
        stable(ordinarySearch(base.sections.search, change.platform))
    ) {
      if (isCurrent(current)) client.setQueryData(profileKey, current);
      throw new PermissionFailure(
        "Your search settings changed in another tab while a website permission was saved. Your draft is still here. Load current settings and review them before starting.",
        true,
      );
    }
    adopt(current);
    return current;
  };
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
      const draft = fromSearchForm(values);
      const draftTicked = tickedOf(settings.data.interactive_sites);
      // Decide on the server's current state, against the sentence shown beside each box.
      const shown = Object.fromEntries(
        PROTECTED.map((platform) => [
          platform,
          permissionOf(platform).data?.text_version,
        ]),
      );
      const read = await Promise.all(
        PROTECTED.map((platform) =>
          draftTicked.has(platform) ||
          opened.has(platform) ||
          permissionOf(platform).data?.enabled
            ? client
                .query({
                  queryKey: permissionKey(platform),
                  queryFn: ({ signal }) => getPermission(platform, signal),
                  staleTime: 0,
                  retry: false,
                })
                .catch(() => undefined)
            : Promise.resolve(permissionOf(platform).data),
        ),
      );
      if (!isMounted()) return;
      const plan = permissionPlan({
        ticked: draftTicked,
        opened,
        websitesOnly: websitesOnlyRows(reviewed),
        states: Object.fromEntries(
          PROTECTED.map((platform, index) => [platform, read[index]]),
        ),
        shown,
        words: readerWords(draft.title_include.terms),
      });
      if ("refused" in plan) {
        const label = LABELS[plan.refused];
        setError(
          plan.reason === "unloaded"
            ? `The ${label} risk sentence could not be loaded, so its permission cannot be given. Untick ${label} or try again later.`
            : plan.reason === "unread"
              ? `The ${label} risk sentence beside its box is new. Read it, then Save and Start again.`
              : `${label} needs words to search: add a title to include, then Save and Start again.`,
        );
        return;
      }
      const saved = await saveSection("search", revision, {
        ...draft,
        ...settings.data,
        ...(sendBoards && boards !== null ? { company_boards: boards } : {}),
      });
      if (!isMounted()) return;
      if (!isCurrent(saved))
        throw new Error("A newer profile was loaded. Review current settings.");
      adopt(saved);
      let current = saved;
      for (const change of plan.changes) {
        const next = await record(change, current);
        if (!next) return;
        current = next;
      }
      await runner.start(current.revision);
      onClose();
    } catch (failure) {
      if (!isMounted()) return;
      if (failure instanceof PermissionFailure) {
        if (failure.conflict) setConflict(true);
        setError(failure.message);
      } else if (
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
      if (isMounted()) setSaving(false);
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
  const tickPreset = (preset: (typeof PRESETS)[number], checked: boolean) => {
    // The box shows whether any website at the platform's address is ticked, her own included,
    // so unticking it unticks them all: the platform's permission is then taken back on save.
    if (!checked) {
      setSites(
        sites
          .filter((site) => site.platform !== preset.platform)
          .map((site) =>
            platformOf(site) === preset.platform
              ? { ...site, enabled: false }
              : site,
          ),
      );
      return;
    }
    if (sites.some((site) => site.platform === preset.platform))
      setSites(
        sites.map((site) =>
          site.platform === preset.platform ? { ...site, enabled: true } : site,
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
      setError("Choose at most 10 websites. Remove a website to add another.");
  };
  // What saving does to a platform's permission, in words beside its risk sentence.
  const noteOf = (platform: Protected): string | null => {
    const state = permissionOf(platform).data;
    if (!state) return null;
    const on = state.enabled && state.accepted;
    if (ticked.has(platform)) {
      if (state.paused_until)
        return `Paused until ${formatDateTime(state.paused_until)}: the website showed a security check. Ticking it does not lift the pause.`;
      return on
        ? "Permission given."
        : "Saving with this box ticked gives this permission.";
    }
    if (
      withdraws(platform, {
        ticked,
        opened,
        websitesOnly: websitesOnlyRows(reviewed),
        states: { [platform]: state },
      })
    )
      return "Saving removes this permission.";
    return on ? "Your permission given elsewhere stays on." : null;
  };
  // Every website but the first of each job website platform, which its named row shows.
  const others = sites
    .map((site, index) => ({ site, index }))
    .filter(
      ({ site, index }) =>
        site.platform === "custom" ||
        sites.findIndex((item) => item.platform === site.platform) !== index,
    );
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
              LLM with Playwright: the model browses the websites you tick. Up
              to 10 websites. Ticking a job website and saving gives the app
              your permission to search it, with the risk written beside it.
              Unticking it and saving takes the permission back.
            </p>
            <ul
              aria-label="Job websites"
              className="divide-y rounded-md border"
            >
              {PRESETS.map((preset) => {
                const site = sites.find(
                  (item) => item.platform === preset.platform,
                );
                return (
                  <li
                    key={preset.platform}
                    className="min-w-0 space-y-1 p-3 text-sm"
                  >
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <label className="flex min-w-0 flex-1 items-center gap-2 font-medium">
                        <input
                          type="checkbox"
                          className="size-4 shrink-0 accent-primary"
                          aria-describedby={`${id}-${preset.platform}-risk`}
                          checked={ticked.has(preset.platform)}
                          onChange={(event) => {
                            tickPreset(preset, event.target.checked);
                          }}
                        />
                        {preset.label}
                      </label>
                      {/* B17: sign in to this website once, then searches read it signed in. */}
                      {site ? <SiteSignIn url={site.url} /> : null}
                    </div>
                    <RiskSentence
                      id={`${id}-${preset.platform}-risk`}
                      platform={preset.platform}
                      state={permissionOf(preset.platform)}
                      note={noteOf(preset.platform)}
                    />
                  </li>
                );
              })}
              {others.map(({ site, index }) => {
                const platform = platformOf(site);
                return (
                  <li
                    key={`${site.platform}:${index}`}
                    className="min-w-0 space-y-1 p-3 text-sm"
                  >
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <label className="flex min-w-0 flex-1 items-center gap-2 break-all">
                        <input
                          type="checkbox"
                          className="size-4 shrink-0 accent-primary"
                          aria-describedby={
                            platform ? `${id}-${index}-risk` : undefined
                          }
                          checked={site.enabled}
                          onChange={(event) => {
                            const enabled = event.target.checked;
                            setSites(
                              sites.map((item, at) =>
                                at === index ? { ...item, enabled } : item,
                              ),
                            );
                          }}
                        />
                        {site.url}
                      </label>
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
                      {/* B17: sign in to this website once, then searches read it signed in. */}
                      <SiteSignIn url={site.url} />
                    </div>
                    {platform ? (
                      <RiskSentence
                        id={`${id}-${index}-risk`}
                        platform={platform}
                        state={permissionOf(platform)}
                        note={`This address is ${LABELS[platform]}'s: its search needs ${LABELS[platform]}'s permission. ${noteOf(platform) ?? ""}`.trim()}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
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
          </section>
          <CompanyBoards boards={boards} onChange={setEdited} />
          <section aria-label="Search criteria" className="space-y-4">
            <h3 className="font-medium">Search criteria</h3>
            <SearchLists form={form} descriptions={false} />
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

// LinkedIn keeps one switch for its search, its reader and Easy Apply (owner, 2026-10-27), so
// its box says what else the tick allows. The server's sentence and its version stay as they are.
const ALSO_ALLOWS: Partial<Record<Protected, string>> = {
  linkedin:
    "Ticking LinkedIn also allows Easy Apply with your LinkedIn account. Each application still needs your approval before it is sent.",
};

/** A job website's risk sentence as the server words it now, what else its tick allows, and
 * what saving does: all inside the element the box names as its description. */
function RiskSentence({
  id,
  platform,
  state,
  note,
}: {
  id: string;
  platform: Protected;
  state: UseQueryResult<Permission>;
  note: string | null;
}) {
  const label = LABELS[platform];
  const also = ALSO_ALLOWS[platform];
  return (
    <div id={id} className="space-y-1 text-xs">
      {state.isPending ? (
        <p className="text-muted-foreground">
          Loading the {label} risk sentence…
        </p>
      ) : state.isError ? (
        <p className="text-destructive">
          The {label} risk sentence could not be loaded, so its permission
          cannot be given now.
        </p>
      ) : (
        <p className="text-muted-foreground">{state.data.text}</p>
      )}
      {also ? <p>{also}</p> : null}
      {note && state.isSuccess ? <p>{note}</p> : null}
    </div>
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
