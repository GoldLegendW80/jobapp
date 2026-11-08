/**
 * The state of /welcome: the step open (`?step=`), the draft kept for the whole wizard, and the
 * saves behind Next, "Skip this step", Start and "Skip for now" (welcome-page.tsx says the rules).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import {
  applySettingsKey,
  saveApplySettings,
  type ApplySettings,
} from "@/features/apply/api/settings-api";
import { useModelKey } from "@/features/agents/api/use-model-key";
import { SourcesFailure, useSources } from "@/features/jobs/api/use-sources";
import { draftOfSearch, sourceCount } from "@/features/jobs/helpers/sources";
import {
  conflictOf,
  profileKey,
  saveSection,
  type Conflict,
  type Profile,
} from "@/features/profile/api/profile-api";
import { describeError } from "@/lib/api";

import {
  completeOnboarding,
  dismissOnboarding,
  onboardingKey,
  passStep,
  type OnboardingState,
} from "./onboarding-api";
import {
  answersWith,
  DISCOVER,
  draftOf,
  FIND_JOBS,
  nextPage,
  openStep,
  passedOf,
  preferencesWith,
  searchWith,
  STEP_IDS,
  stepProblem,
  UNSTORED,
  type Draft,
  type PanelProps,
  type StepId,
} from "../helpers/steps";
import { SWEEP_SENTENCE } from "../helpers/words";

export function useWizard({
  profile,
  state,
  settings,
}: {
  profile: Profile;
  state: OnboardingState;
  settings: ApplySettings | undefined;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState<Draft>(() => draftOf(profile.sections));
  const [problem, setProblem] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict>();
  // "Websites to search" is answered by the list itself (steps.ts UNSTORED).
  const sources = useSources(profile.sections.search);
  const current = client.getQueryData<Profile>(profileKey) ?? profile;
  const listed = sourceCount(draftOfSearch(current.sections.search)) > 0;
  // "Model access" is done when a credential was saved before the wizard opened (steps.ts
  // UNSTORED): one saved on that step leaves the person there to see it, until Next.
  const key = useModelKey();
  const [access] = useState(() => Boolean(key.data));
  const done = [
    ...state.done,
    ...(listed ? ["sites"] : []),
    ...(access ? ["access"] : []),
  ];
  const passed = passedOf(state.passed);
  const step: StepId = openStep(params.get("step"), done, passed);
  const fromSummary = params.get("from") === "summary";
  const after = nextPage(params.get("next"));
  const index = STEP_IDS.indexOf(step);

  /** Open a step; `back` says the summary sent the person there ("Change"). */
  const goTo = (next: StepId, back = false) => {
    setProblem(null);
    setParams(
      {
        step: next,
        ...(back ? { from: "summary" } : {}),
        ...(after ? { next: after } : {}),
      },
      { replace: false },
    );
  };
  /** Where Next and "Skip this step" lead: the summary when it sent the person here. */
  const following = (from: StepId): StepId =>
    fromSummary
      ? "summary"
      : (STEP_IDS[STEP_IDS.indexOf(from) + 1] ?? "summary");
  /**
   * Keep the step passed in the profile. A step passed is no revision of the profile (a save
   * keeps the stored steps), so the next save starts from the revision this page already has,
   * and a second tab editing the profile is not sent a 409 by it.
   */
  const recordPass = async (passing: StepId) => {
    if (UNSTORED.has(passing)) return;
    const found = await passStep(passing);
    client.setQueryData(onboardingKey, found);
  };
  const change = (patch: Partial<Draft>) => {
    setProblem(null);
    setDraft((old) => ({ ...old, ...patch }));
  };

  const save = useMutation({
    mutationFn: async (saving: StepId): Promise<Profile | null> => {
      const cached = client.getQueryData<Profile>(profileKey) ?? profile;
      if (saving === "apply") {
        // Apply's own route, with only what the person changed here (nothing when nothing).
        const changed: Parameters<typeof saveApplySettings>[0] = {};
        if (draft.mode !== null && draft.mode !== settings?.mode_after_like)
          changed.mode_after_like = draft.mode;
        if (
          draft.review !== null &&
          draft.review !== settings?.review_before_send
        )
          changed.review_before_send = draft.review;
        if (Object.keys(changed).length) {
          const kept = await saveApplySettings(changed);
          client.setQueryData(applySettingsKey, kept);
        }
        return saveSection(
          "preferences",
          cached.revision,
          preferencesWith(cached.sections.preferences, draft),
        );
      }
      if (saving === "sites")
        return sources.save({
          base: cached,
          body: cached.sections.search,
          titles: cached.sections.search.title_include.terms,
        });
      if (
        saving === "looking" ||
        saving === "where" ||
        saving === "work" ||
        saving === "pay"
      ) {
        const searched = await saveSection(
          "search",
          cached.revision,
          searchWith(saving, cached.sections.search, draft),
        );
        client.setQueryData(profileKey, searched);
        // The step's answers for forms, merged into the stored answers, only when they change.
        const stored = searched.sections.answers;
        const answers = answersWith(
          saving,
          stored,
          draft,
          cached.sections.search.compensation,
        );
        if (JSON.stringify(answers) === JSON.stringify(stored)) return searched;
        return saveSection("answers", searched.revision, answers);
      }
      return null;
    },
    onSuccess: async (saved, saving) => {
      if (saved) client.setQueryData(profileKey, saved);
      try {
        await recordPass(saving);
      } catch (error) {
        setProblem(
          `This step was saved, but not marked as done. ${describeError(error)}`,
        );
        return;
      }
      goTo(following(saving));
    },
    onError: (error) => {
      const stale = conflictOf(error);
      if (stale) setConflict(stale);
      else if (error instanceof SourcesFailure) {
        setProblem(error.message);
        if (error.conflict)
          void client.invalidateQueries({ queryKey: profileKey });
      } else setProblem(`This step was not saved. ${describeError(error)}`);
    },
  });

  const skip = useMutation({
    mutationFn: recordPass,
    onSuccess: (_, skipped) => {
      goTo(following(skipped));
    },
    onError: (error) => {
      setProblem(`That did not work. ${describeError(error)}`);
    },
  });

  const finish = useMutation({
    mutationFn: completeOnboarding,
    onSuccess: (answer) => {
      if ("state" in answer) client.setQueryData(onboardingKey, answer.state);
      else void client.invalidateQueries({ queryKey: onboardingKey });
      // Recording the end is one revision of the profile.
      void client.invalidateQueries({ queryKey: profileKey });
      toast.success(SWEEP_SENTENCE[answer.sweep]);
      // Nothing to search: Find jobs opens on the list instead of an empty deck.
      void navigate(
        after ?? (answer.sweep === "no_websites" ? FIND_JOBS : DISCOVER),
      );
    },
    onError: (error) => {
      setProblem(`The wizard could not finish. ${describeError(error)}`);
    },
  });

  const later = useMutation({
    mutationFn: dismissOnboarding,
    onSuccess: (saved) => {
      client.setQueryData(onboardingKey, saved);
      // Putting it off is one revision of the profile: no later save starts from a stale one.
      void client.invalidateQueries({ queryKey: profileKey });
      void navigate(after ?? "/jobs");
    },
    onError: (error) => {
      setProblem(`That did not work. ${describeError(error)}`);
    },
  });

  const next = () => {
    const blocked = stepProblem(step, draft);
    if (blocked) {
      setProblem(blocked);
      return;
    }
    save.mutate(step);
  };

  const applyConflict = (stale: Conflict) => {
    client.setQueryData<Profile>(profileKey, (old) =>
      old
        ? { ...old, revision: stale.revision, sections: stale.sections }
        : old,
    );
  };
  /** The conflict dialog's two ways out: keep the draft on the newer revision, or reload it. */
  const keepEditing = () => {
    if (conflict) applyConflict(conflict);
    setConflict(undefined);
  };
  /** The profile as a step wrote it (Building your profile): the next steps start from it. */
  const adopt = (written: Profile) => {
    client.setQueryData(profileKey, written);
    setDraft(draftOf(written.sections));
  };
  const reload = () => {
    if (conflict) {
      applyConflict(conflict);
      setDraft(draftOf(conflict.sections));
    }
    setConflict(undefined);
  };

  const busy =
    save.isPending ||
    skip.isPending ||
    finish.isPending ||
    later.isPending ||
    sources.saving;
  const stored = current.sections;
  const panel: PanelProps = {
    draft,
    change,
    problem,
    patterns: {
      roles: stored.search.title_include.advanced_regex,
      places: stored.search.location.advanced_regex,
    },
  };

  return {
    step,
    index,
    passed,
    draft,
    problem,
    panel,
    sources,
    stored,
    busy,
    conflict,
    goTo,
    next,
    skip: () => {
      skip.mutate(step);
    },
    start: () => {
      finish.mutate();
    },
    putOff: () => {
      later.mutate();
    },
    saving: save.isPending,
    starting: finish.isPending,
    keepEditing,
    reload,
    adopt,
  };
}

export type Wizard = ReturnType<typeof useWizard>;
