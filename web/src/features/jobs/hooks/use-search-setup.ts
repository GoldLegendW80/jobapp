/**
 * The form of one search in Find jobs, with Save and "Save and start search": the list of
 * addresses (use-sources.ts) and what to look for. The main search is saved in the profile on the
 * revision the person reviewed, then started on the revision the save made; a saved search (jobs
 * J33) holds the keys of one search only (CRITERIA), written through /api/jobs/searches. A change
 * from another tab is a conflict to review, never a retry.
 */
import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useForm } from "react-hook-form";

import { conflictOf, type Profile } from "@/features/profile/api/profile-api";
import {
  fromSearchForm,
  searchSchema,
  toSearchForm,
  type SearchForm,
} from "@/features/profile/helpers/editor-forms";
import { toast } from "sonner";

import type { Search } from "@/features/profile/api/profile-api";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import { nameProblem, runWords } from "../helpers/saved-searches";
import { READERS, type Protected } from "../helpers/site-permissions";
import {
  CRITERIA,
  MAIN_SEARCH,
  bodyOf,
  type Criteria,
  type SavedSearch,
} from "../api/searches-api";
import { runningSweepOf } from "../api/sweeps-api";
import { useRunSearch } from "../api/use-saved-searches";
import {
  SourcesFailure,
  useSources,
  type SourcesSnapshot,
} from "../api/use-sources";
import type { SweepRunner } from "../api/use-sweep-runner";

/** The search a form edits: the main one, a saved one, or a new one. */
export type SetupTarget =
  { kind: "main" } | { kind: "saved"; search: SavedSearch } | { kind: "new" };

/** What a form holds, kept when the person goes back to Your searches (#798, per search). */
export interface SetupSnapshot {
  values: SearchForm;
  sources: SourcesSnapshot;
  instructions: string;
  name: string;
  more: boolean;
  revision: number;
  reviewed: Search;
  savedRevision: number;
}

/** A new search starts blank but for the age window and the web. */
function blank(person: Search): Criteria {
  const empty = { advanced_regex: false, terms: [] };
  return {
    title_include: empty,
    title_exclude: empty,
    location: empty,
    location_exclude: empty,
    remote_stance: null,
    contract_types: [],
    days: person.days,
    interactive_sites: [],
    company_boards: [],
    job_feeds: [],
    web_search: true,
    interactive_instructions: "",
  };
}

/** The search the form shows: the person's settings with the search's own keys over them. */
function searchOf(person: Search, target: SetupTarget): Search {
  if (target.kind === "main") return person;
  const own = target.kind === "saved" ? target.search.criteria : {};
  return { ...person, ...blank(person), ...own };
}

function criteriaOf(search: Search): Criteria {
  return Object.fromEntries(CRITERIA.map((key) => [key, search[key]]));
}

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

/** Said beside Save while the list is empty: the api refuses a search with no website and no board (a
 * save still goes through, it is how the last website is taken out). */
export const EMPTY_LIST_REASON =
  "Add a website or a company job board to your list to start a search.";

export type SearchSetupState = ReturnType<typeof useSearchSetup>;

export function useSearchSetup({
  profile,
  runner,
  target = { kind: "main" },
  names = [],
  initial,
  onClose,
  onSaved = onClose,
  onWritten,
  onBusyChange,
}: {
  profile: Profile;
  runner: SweepRunner;
  target?: SetupTarget;
  /** The names of the person's other saved searches. */
  names?: readonly string[];
  initial?: SetupSnapshot;
  onClose: () => void;
  /** After Save, or a start that waits in the line: back to Your searches. */
  onSaved?: () => void;
  /** A saved search was written, its take backs not done yet: a new one now has its id. */
  onWritten?: (saved: SavedSearch) => void;
  /** Told when saving starts and ends, so the surface around stays open meanwhile. */
  onBusyChange: (busy: boolean) => void;
}) {
  const [search] = useState(() => searchOf(profile.sections.search, target));
  // The search as last written from this form: a retry after a failure halfway goes on from it.
  const [written, setWritten] = useState<SavedSearch | null>(null);
  const saved = written ?? (target.kind === "saved" ? target.search : null);
  const form = useForm<SearchForm>({
    resolver: zodResolver(searchSchema),
    defaultValues: initial?.values ?? toSearchForm(search),
  });
  const sources = useSources(search, {
    searchId: saved?.id ?? (target.kind === "new" ? "" : MAIN_SEARCH),
    initial: initial?.sources,
  });
  const [instructions, setInstructions] = useState(
    initial?.instructions ?? search.interactive_instructions ?? "",
  );
  const [name, setName] = useState(initial?.name ?? saved?.name ?? "");
  const [revision, setRevision] = useState(
    initial?.revision ?? profile.revision,
  );
  const [reviewed, setReviewed] = useState(
    () => initial?.reviewed ?? structuredClone(profile.sections.search),
  );
  const [savedRevision, setSavedRevision] = useState(
    initial?.savedRevision ?? saved?.revision ?? 0,
  );
  const [more, setMore] = useState(initial?.more ?? false);
  const run = useRunSearch({});
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

  const markSaving = (value: boolean) => {
    setSaving(value);
    onBusyChange(value);
  };

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

  const { sites, boards, feeds } = sources.draft;
  const emptyReason =
    sites.length === 0 && (boards?.length ?? 0) === 0 && feeds.length === 0
      ? EMPTY_LIST_REASON
      : null;

  // A search started, or put in the line behind the running one, as its Run now does.
  const startOrWait = async (id: string, label: string) => {
    const ran = await run.mutateAsync(id);
    if (ran.sweep) {
      runner.follow(ran.sweep);
      onClose();
      return;
    }
    if (ran.outcome === "queued" || ran.outcome === "started")
      toast.info(runWords(label, ran));
    else toast.error(runWords(label, ran));
    onSaved();
  };

  // A saved search: its criteria written, then started or back to Your searches.
  const submitSaved = async (values: SearchForm, start: boolean) => {
    const draft = {
      ...fromSearchForm(values),
      interactive_instructions: instructions,
    };
    const result = await sources.saveSaved({
      base: {
        ...profile,
        revision,
        sections: { ...profile.sections, search: reviewed },
      },
      id: saved?.id ?? null,
      revision: savedRevision,
      name,
      criteria: criteriaOf(draft),
      schedule: saved
        ? bodyOf(saved).schedule
        : {
            enabled: false,
            days: [...search.sweep_schedule.days],
            times: ["06:17"],
          },
      titles: draft.title_include.terms,
      instructions,
      onAdopt: adopt,
      onWritten: (stored) => {
        if (!mounted.current) return;
        setWritten(stored);
        setSavedRevision(stored.revision);
        onWritten?.(stored);
      },
    });
    if (!result || !mounted.current) return;
    if (!start) {
      toast.success(`${result.name} saved.`);
      onSaved();
      return;
    }
    await startOrWait(result.id, result.name);
  };

  const submit = async (values: SearchForm, start: boolean) => {
    if (busy.current || conflict) return;
    if (target.kind !== "main") {
      const wrong = nameProblem(name, names);
      if (wrong) {
        setError(wrong);
        return;
      }
    }
    busy.current = true;
    markSaving(true);
    setError(null);
    try {
      if (target.kind !== "main") {
        await submitSaved(values, start);
        return;
      }
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
      if (!start) {
        toast.success("Main search saved.");
        onSaved();
        return;
      }
      try {
        await runner.start(current.revision);
      } catch (failure) {
        if (!runningSweepOf(failure)) throw failure;
        await startOrWait(MAIN_SEARCH, "Main search");
        return;
      }
      onClose();
    } catch (failure) {
      if (!mounted.current) return;
      if (failure instanceof SourcesFailure) {
        if (failure.conflict) setConflict(true);
        setError(failure.message);
      } else if (
        target.kind !== "main" &&
        isApiError(failure) &&
        (failure.status === 422 ||
          (failure.status === 409 &&
            (failure.body as { revision?: unknown } | null)?.revision ===
              undefined))
      ) {
        // A name taken, a website without its permission, a search the checks refuse.
        setError(
          `Not saved. ${errorDetail(failure) ?? describeError(failure)}`,
        );
      } else if (
        conflictOf(failure) ||
        (isApiError(failure) && failure.status === 409)
      ) {
        setConflict(true);
        setError(
          target.kind === "main"
            ? "Your profile changed in another tab. Your choices are still here. Load current settings and review them before starting."
            : "This search or your settings changed in another tab. Your choices are still here. Load current settings and review them before saving.",
        );
      } else
        setError(
          `${start ? "The search did not start" : "Not saved"}. ${describeError(failure)}`,
        );
    } finally {
      busy.current = false;
      if (mounted.current) markSaving(false);
    }
  };

  const invalid = (errors: object) => {
    if (Object.keys(errors).some((field) => MORE_FIELDS.has(field)))
      setMore(true);
  };
  const onSubmit = (event: FormEvent<HTMLFormElement>) =>
    void form.handleSubmit((values) => submit(values, true), invalid)(event);
  const onSave = () =>
    void form.handleSubmit((values) => submit(values, false), invalid)();

  const snapshot = (): SetupSnapshot => ({
    values: form.getValues(),
    sources: sources.snapshot(),
    instructions,
    name,
    more,
    revision,
    reviewed,
    savedRevision,
  });

  return {
    form,
    sources,
    reviewed,
    instructions,
    setInstructions,
    more,
    setMore,
    conflict,
    error,
    emptyReason,
    locked: saving || sources.saving,
    onSubmit,
    onSave,
    target: target.kind,
    name,
    setName,
    snapshot,
  };
}
