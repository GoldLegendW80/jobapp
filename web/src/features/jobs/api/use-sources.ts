/**
 * The list of addresses a search reads (sources.ts) and its save, shared by Find jobs
 * (search-setup/search-setup.tsx) and the first login wizard's "Websites to search" step.
 *
 * Saving keeps the rule of jobs J17 (owner, 2026-10-27): a known job website in the list is the
 * person's permission to search it, recorded through its own consent route at the version of the
 * risk sentence shown beside it and on the reviewed profile revision; taking it out of the list
 * and saving takes the permission back. The profile save never carries a permission. With several
 * searches (jobs J33) a permission is the person's: ticking a website in any search gives it, and
 * unticking it takes it back only while no other search of the person ticks it, read afresh.
 */
import {
  useQueries,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import { linkedinKey } from "@/features/browser/api/linkedin-api";
import {
  getProfile,
  interactiveSettingsSchema,
  profileKey,
  saveSection,
  type LinkedInSearch,
  type Profile,
  type Search,
} from "@/features/profile/api/profile-api";
import {
  fromSearchForm,
  toSearchForm,
} from "@/features/profile/helpers/editor-forms";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import { formatDateTime } from "../helpers/format";
import { keptElsewhere } from "../helpers/saved-searches";
import {
  PROTECTED,
  permissionPlan,
  readerWords,
  tickedOf,
  withdraws,
  type PermissionChange,
  type Protected,
} from "../helpers/site-permissions";
import {
  PLATFORM_NAMES,
  draftOfSearch,
  type SourcesDraft,
} from "../helpers/sources";
import {
  getPermission,
  permissionKey,
  putPermission,
  type Permission,
} from "./permissions-api";
import {
  MAIN_SEARCH,
  changeSearch,
  createSearch,
  type Criteria,
  type SavedSearch,
  type SavedSchedule,
} from "./searches-api";
import { useReadSearches, useSavedSearches } from "./use-saved-searches";

/** A save that stopped; `conflict` when current settings must be loaded first. */
export class SourcesFailure extends Error {
  readonly conflict: boolean;
  constructor(message: string, conflict: boolean) {
    super(message);
    this.conflict = conflict;
  }
}

/** The search as reviewed, without what a consent write of `except` changes. */
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
    linkedin_search: search.linkedin_search ?? "api",
  };
}

/** LinkedIn and Free-Work rows whose permission serves the websites' search only. */
export function websitesOnlyRows(search: Search): Set<Protected> {
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

export interface SaveArgs {
  /** The profile as the person reviewed it: its revision is the save's base. */
  base: Profile;
  /** The whole search section to save, before the list's own fields are put in. */
  body: Search;
  /** The titles to include: the words of a new LinkedIn or Free-Work reader row. */
  titles: readonly string[];
  /** Instructions for the search; the stored ones when absent. */
  instructions?: string;
  /** Called with each profile the save makes current (the save, then each consent write). */
  onAdopt?: (current: Profile) => void;
}

/** A saved search to write (jobs J33): its id and revision when it exists. */
export interface SaveSavedArgs {
  base: Profile;
  id: string | null;
  revision: number;
  name: string;
  criteria: Criteria;
  schedule: SavedSchedule;
  titles: readonly string[];
  instructions: string;
  onAdopt?: (current: Profile) => void;
}

/** What a draft of the list holds, kept when the form is left and opened again. */
export interface SourcesSnapshot {
  draft: SourcesDraft;
  /** How LinkedIn is searched. */
  mode: LinkedInSearch;
  opened: Set<Protected>;
  pending: string;
}

export interface Sources {
  draft: SourcesDraft;
  setDraft: Dispatch<SetStateAction<SourcesDraft>>;
  linkedinMode: LinkedInSearch;
  setLinkedinMode: (mode: LinkedInSearch) => void;
  /** What is typed in the address box and not added yet. */
  pending: string;
  setPending: (text: string) => void;
  permissionOf: (platform: Protected) => UseQueryResult<Permission>;
  /** What saving does to a platform's permission, against the search as reviewed. */
  noteOf: (platform: Protected, reviewed: Search) => string | null;
  /** The known job websites whose permission saving takes back, against the search as reviewed. */
  withdrawn: (reviewed: Search) => Protected[];
  save: (args: SaveArgs) => Promise<Profile | null>;
  saveSaved: (args: SaveSavedArgs) => Promise<SavedSearch | null>;
  saving: boolean;
  /** false in a saved search: the LinkedIn mode is the person's, set in the main search. */
  ownsPersonal: boolean;
  snapshot: () => SourcesSnapshot;
}

/** Reads each known job website's permission again: "Load current settings" after a conflict. */
export function useRefreshPermissions() {
  const client = useQueryClient();
  return () => {
    for (const platform of PROTECTED)
      void client.invalidateQueries({ queryKey: permissionKey(platform) });
  };
}

export function useSources(
  search: Search,
  {
    searchId = MAIN_SEARCH,
    initial,
  }: { searchId?: string; initial?: SourcesSnapshot } = {},
): Sources {
  const client = useQueryClient();
  const [draft, setDraft] = useState<SourcesDraft>(
    () => initial?.draft ?? draftOfSearch(search),
  );
  // How a LinkedIn in the list is searched (owner, 2026-10-30): its public job search by default.
  const [linkedinMode, setLinkedinMode] = useState<LinkedInSearch>(
    initial?.mode ?? search.linkedin_search ?? "api",
  );
  // The platforms in the list when it was opened (or last saved): taking one out takes back its
  // permission.
  const [opened, setOpened] = useState(
    () => initial?.opened ?? tickedOf(search.interactive_sites ?? []),
  );
  const [pending, setPending] = useState(initial?.pending ?? "");
  const others = useSavedSearches();
  const readSearches = useReadSearches();
  const kept = keptElsewhere(others.data?.items ?? [], searchId);
  const [saving, setSaving] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const isMounted = () => mounted.current;
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
  const ticked = tickedOf(draft.sites);

  const noteOf = (platform: Protected, reviewed: Search): string | null => {
    const state = permissionOf(platform).data;
    if (!state) return null;
    const on = state.enabled && state.accepted;
    if (ticked.has(platform)) {
      if (state.paused_until)
        return `Paused until ${formatDateTime(state.paused_until)}: the website showed a security check. Adding it again does not lift the pause.`;
      return on
        ? "Permission given."
        : "Saving with this website in your list gives this permission.";
    }
    if (kept.has(platform) && on)
      return "Another of your searches has this website, so its permission stays on.";
    if (
      withdraws(platform, {
        ticked,
        opened,
        kept,
        websitesOnly: websitesOnlyRows(reviewed),
        states: { [platform]: state },
      })
    )
      return "Saving removes this permission.";
    return on ? "Your permission given elsewhere stays on." : null;
  };

  const withdrawn = (reviewed: Search): Protected[] =>
    PROTECTED.filter((platform) => {
      const state = permissionOf(platform).data;
      return (
        state !== undefined &&
        withdraws(platform, {
          ticked,
          opened,
          kept,
          websitesOnly: websitesOnlyRows(reviewed),
          states: { [platform]: state },
        })
      );
    });

  const isCurrent = (current: Profile, base: Profile) =>
    current.revision >=
    Math.max(
      base.revision,
      client.getQueryData<Profile>(profileKey)?.revision ?? base.revision,
    );

  // One consent write on top of the profile `base`, then the profile it made, checked to differ
  // from `base` by that permission only (a change from another tab is a conflict).
  const record = async (
    change: PermissionChange,
    base: Profile,
    adopt: (current: Profile) => void,
  ): Promise<Profile | null> => {
    const label = PLATFORM_NAMES[change.platform];
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
        throw new SourcesFailure(
          `The ${label} permission or its risk sentence changed in another tab. Your list is still here. Load current settings and review them before saving.`,
          true,
        );
      if (
        isApiError(failure) &&
        failure.kind === "http" &&
        (failure.status ?? 500) < 500
      )
        throw new SourcesFailure(
          `The ${label} permission was not saved. ${errorDetail(failure) ?? describeError(failure)}`,
          false,
        );
      throw new SourcesFailure(
        `The ${label} permission was not confirmed. Your list is still here. Load current settings and review them before saving. ${describeError(failure)}`,
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
      throw new SourcesFailure(
        `The ${label} permission was not confirmed. Your list is still here. Load current settings and review them before saving. ${describeError(failure)}`,
        true,
      );
    }
    if (!isMounted()) return null;
    if (
      !isCurrent(current, base) ||
      stable(ordinarySearch(current.sections.search, change.platform)) !==
        stable(ordinarySearch(base.sections.search, change.platform))
    ) {
      if (isCurrent(current, base)) client.setQueryData(profileKey, current);
      throw new SourcesFailure(
        "Your search settings changed in another tab while a website permission was saved. Your list is still here. Load current settings and review them before saving.",
        true,
      );
    }
    adopt(current);
    return current;
  };

  const checked = (instructions: string) => {
    if (pending.trim())
      throw new SourcesFailure(
        "Add the address you typed, or clear it, before saving.",
        false,
      );
    const settings = interactiveSettingsSchema.safeParse({
      interactive_sites: draft.sites,
      interactive_instructions: instructions,
    });
    if (!settings.success)
      throw new SourcesFailure(
        settings.error.issues[0]?.message ??
          "Check your websites and instructions.",
        false,
      );
    return settings.data;
  };

  // The consent writes of a save: decided on each platform's state read afresh, and, when one
  // would be taken back, on the person's other searches read afresh too.
  const planned = async (
    reviewed: Search,
    draftTicked: Set<Protected>,
    titles: readonly string[],
  ): Promise<PermissionChange[] | null> => {
    // Decide on the server's current state, against the sentence shown beside each website.
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
    if (!isMounted()) return null;
    const input = {
      ticked: draftTicked,
      opened,
      websitesOnly: websitesOnlyRows(reviewed),
      states: Object.fromEntries(
        PROTECTED.map((platform, index) => [platform, read[index]]),
      ),
      shown,
      words: readerWords(titles),
    };
    let plan = permissionPlan(input);
    if (
      "changes" in plan &&
      plan.changes.some((change) => change.body.enabled === false)
    ) {
      let fresh: SavedSearch[];
      try {
        fresh = (await readSearches()).items;
      } catch (failure) {
        throw new SourcesFailure(
          `Your other searches could not be read, so no permission was changed. Try again. ${describeError(failure)}`,
          false,
        );
      }
      if (!isMounted()) return null;
      plan = permissionPlan({ ...input, kept: keptElsewhere(fresh, searchId) });
    }
    if ("refused" in plan) {
      const label = PLATFORM_NAMES[plan.refused];
      throw new SourcesFailure(
        plan.reason === "unloaded"
          ? `The ${label} risk sentence could not be loaded, so its permission cannot be given. Remove ${label} from your list or try again later.`
          : plan.reason === "unread"
            ? `The ${label} risk sentence in your list is new. Read it, then save again.`
            : `${label} needs words to search: add a job title you want, then save again.`,
        false,
      );
    }
    return plan.changes;
  };

  const save = async ({
    base,
    body,
    titles,
    instructions,
    onAdopt,
  }: SaveArgs): Promise<Profile | null> => {
    const settings = checked(
      instructions ?? base.sections.search.interactive_instructions ?? "",
    );
    setSaving(true);
    try {
      const reviewed = base.sections.search;
      const draftTicked = tickedOf(settings.interactive_sites);
      const changes = await planned(reviewed, draftTicked, titles);
      if (!changes) return null;
      const adopt = (current: Profile) => {
        client.setQueryData(profileKey, current);
        onAdopt?.(current);
      };
      // linkedin_search and company_boards are sent only when the person changed them or the
      // section already holds them: the server keeps what is left out (schemas.py
      // KEPT_WHEN_OMITTED), alert_emails included, which this list no longer shows.
      const saved = await saveSection("search", base.revision, {
        ...body,
        ...settings,
        ...(reviewed.linkedin_search !== undefined || linkedinMode !== "api"
          ? { linkedin_search: linkedinMode }
          : {}),
        job_feeds: draft.feeds,
        ...(draft.boards !== null ? { company_boards: draft.boards } : {}),
        // J21: like company_boards, sent once changed or stored (true when absent).
        ...(reviewed.web_search !== undefined || !draft.web
          ? { web_search: draft.web }
          : {}),
      });
      if (!isMounted()) return null;
      if (!isCurrent(saved, base))
        throw new Error("A newer profile was loaded. Review current settings.");
      adopt(saved);
      let current = saved;
      for (const change of changes) {
        const next = await record(change, current, adopt);
        if (!next) return null;
        current = next;
      }
      if (isMounted()) setOpened(draftTicked);
      return current;
    } finally {
      if (isMounted()) setSaving(false);
    }
  };

  // A saved search: its grants first (the api refuses a ticked website without its permission),
  // then the search, then its take backs, so a failed write never leaves a stored search with a
  // website whose permission it took back.
  const saveSaved = async (
    args: SaveSavedArgs,
  ): Promise<SavedSearch | null> => {
    const settings = checked(args.instructions);
    setSaving(true);
    try {
      const draftTicked = tickedOf(settings.interactive_sites);
      const changes = await planned(
        args.base.sections.search,
        draftTicked,
        args.titles,
      );
      if (!changes) return null;
      const adopt = (current: Profile) => {
        client.setQueryData(profileKey, current);
        args.onAdopt?.(current);
      };
      let current = args.base;
      const grants = changes.filter((change) => change.body.enabled === true);
      for (const change of grants) {
        const next = await record(change, current, adopt);
        if (!next) return null;
        current = next;
      }
      const body = {
        name: args.name.trim(),
        criteria: {
          ...args.criteria,
          ...settings,
          job_feeds: draft.feeds,
          company_boards: draft.boards ?? [],
          web_search: draft.web,
        },
        schedule: args.schedule,
      };
      const saved = args.id
        ? await changeSearch(args.id, args.revision, body)
        : await createSearch(body);
      for (const change of changes.filter((one) => !grants.includes(one))) {
        const next = await record(change, current, adopt);
        if (!next) return null;
        current = next;
      }
      if (isMounted()) setOpened(draftTicked);
      return saved;
    } finally {
      if (isMounted()) setSaving(false);
    }
  };

  return {
    draft,
    setDraft,
    linkedinMode,
    setLinkedinMode,
    pending,
    setPending,
    permissionOf,
    noteOf,
    withdrawn,
    save,
    saveSaved,
    saving,
    ownsPersonal: searchId === MAIN_SEARCH,
    snapshot: () => ({ draft, mode: linkedinMode, opened, pending }),
  };
}
