/**
 * The form of Find jobs and its "Save and start search": the list of addresses (use-sources.ts)
 * and what to look for, saved on the revision the person reviewed, then the search started on
 * the revision the save made. A change from another tab is a conflict to review, never a retry.
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
import { describeError, isApiError } from "@/lib/api";

import { READERS, type Protected } from "../helpers/site-permissions";
import { SourcesFailure, useSources } from "../api/use-sources";
import type { SweepRunner } from "../api/use-sweep-runner";

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

export function useSearchSetup({
  profile,
  runner,
  onClose,
  onBusyChange,
}: {
  profile: Profile;
  runner: SweepRunner;
  onClose: () => void;
  /** Told when saving starts and ends, so the surface around stays open meanwhile. */
  onBusyChange: (busy: boolean) => void;
}) {
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

  const submit = async (values: SearchForm) => {
    if (busy.current || conflict) return;
    busy.current = true;
    markSaving(true);
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
      if (mounted.current) markSaving(false);
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) =>
    void form.handleSubmit(submit, (errors) => {
      if (Object.keys(errors).some((name) => MORE_FIELDS.has(name)))
        setMore(true);
    })(event);

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
    locked: saving || sources.saving,
    onSubmit,
  };
}
