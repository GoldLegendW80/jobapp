import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, CircleAlert, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  applySettingsKey,
  getApplySettings,
  type ApplySettings,
} from "@/features/apply/settings-api";
import { useMe } from "@/features/auth/auth-context";
import {
  conflictOf,
  getProfile,
  profileKey,
  saveSection,
  type Conflict,
  type Profile,
} from "@/features/profile/api";
import { ConflictDialog } from "@/features/profile/section-ui";
import { PageLoading, PageUnavailable } from "@/features/shell/page-status";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";
import { WORDING } from "@/lib/wording";
import { cn } from "@/lib/utils";

import {
  completeOnboarding,
  dismissOnboarding,
  getOnboarding,
  onboardingKey,
  passStep,
  saveLikeMode,
  type OnboardingState,
} from "./onboarding-api";
import { starterKey } from "./starter-api";
import { StarterBoards } from "./starter-boards";
import {
  StepApply,
  StepLooking,
  StepPay,
  StepProfile,
  StepSummary,
  StepWhere,
  StepWork,
  type PanelProps,
} from "./step-panels";
import {
  draftOf,
  ESSENTIAL,
  nextPage,
  openStep,
  passedOf,
  preferencesWith,
  savedDraft,
  searchWith,
  STEP_IDS,
  STEP_NAMES,
  stepProblem,
  type Draft,
  type StepId,
} from "./steps";

/** Where "Start" leads: the deck. */
export const DISCOVER = "/discover";

const SWEEP_SENTENCE = {
  started:
    "Your first search has started. New postings appear as it finds them.",
  running: "A search was already running: it is your first one.",
  no_boards:
    "Add the company boards to search in Search options, then find new postings.",
  search_invalid:
    "Open Search options and save them again, then find new postings.",
  queue_down:
    "The search could not start. Find new postings again in a minute.",
} as const;

/**
 * /welcome, the first login wizard (opportunities O5): seven steps with a progress bar, Back,
 * Next and "Skip this step" (not on the roles nor the summary). Next saves the step through the
 * profile's own route with the revision this page read (a second tab's save shows the profile's
 * conflict dialog) and apply's settings route for the mode after a Like. What the person typed is
 * kept in one draft for the whole wizard, so Back shows it as left. The step open is in `?step=`
 * and the steps passed are kept in the profile (POST /api/opportunities/onboarding/steps), so a
 * reload, a closed tab or another device resumes at the right step. The summary shows what was
 * saved, never what was typed and then skipped.
 */
export function WelcomePage() {
  useTitle(WORDING.welcome);
  const me = useMe();
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
  });
  const state = useQuery({
    queryKey: onboardingKey,
    queryFn: ({ signal }) => getOnboarding(signal),
  });
  const settings = useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
  });
  // A failed refetch keeps the wizard and its draft: only a first load that fails stops here.
  if ((profile.isError && !profile.data) || (state.isError && !state.data))
    return (
      <PageUnavailable
        error={profile.error ?? state.error}
        onRetry={() => {
          void profile.refetch();
          void state.refetch();
        }}
        retrying={profile.isFetching || state.isFetching}
      />
    );
  if (!profile.data || !state.data || !me.data) return <PageLoading />;
  return (
    <Wizard
      profile={profile.data}
      state={state.data}
      settings={settings.data}
    />
  );
}

function Wizard({
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
  const passed = passedOf(state.passed);
  const step: StepId = openStep(params.get("step"), state.done, passed);
  const fromSummary = params.get("from") === "summary";
  const after = nextPage(params.get("next"));
  const index = STEP_IDS.indexOf(step);
  const heading = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);

  useEffect(() => {
    // Each step moved to puts the keyboard on its heading, not the first time.
    if (first.current) first.current = false;
    else heading.current?.focus();
  }, [step]);

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
  const following = (current: StepId): StepId =>
    fromSummary
      ? "summary"
      : (STEP_IDS[STEP_IDS.indexOf(current) + 1] ?? "summary");
  /**
   * Keep the step passed in the profile. A step passed is no revision of the profile (a save
   * keeps the stored steps), so the next save starts from the revision this page already has,
   * and a second tab editing the profile is not sent a 409 by it.
   */
  const recordPass = async (current: StepId) => {
    const found = await passStep(current);
    client.setQueryData(onboardingKey, found);
  };
  const change = (patch: Partial<Draft>) => {
    setProblem(null);
    setDraft((old) => ({ ...old, ...patch }));
  };

  const save = useMutation({
    mutationFn: async (current: StepId): Promise<Profile | null> => {
      const cached = client.getQueryData<Profile>(profileKey) ?? profile;
      if (current === "apply") {
        if (draft.mode !== null && draft.mode !== settings?.mode_after_like) {
          await saveLikeMode(draft.mode);
          void client.invalidateQueries({ queryKey: applySettingsKey });
        }
        return saveSection(
          "preferences",
          cached.revision,
          preferencesWith(cached.sections.preferences, draft),
        );
      }
      if (
        current === "looking" ||
        current === "where" ||
        current === "work" ||
        current === "pay"
      )
        return saveSection(
          "search",
          cached.revision,
          searchWith(current, cached.sections.search, draft),
        );
      return null;
    },
    onSuccess: async (saved, current) => {
      if (saved) client.setQueryData(profileKey, saved);
      // The starter list is built from the saved answers: never show a stale one on step 7.
      void client.invalidateQueries({ queryKey: starterKey });
      try {
        await recordPass(current);
      } catch (error) {
        setProblem(
          `This step was saved, but not marked as done. ${describeError(error)}`,
        );
        return;
      }
      goTo(following(current));
    },
    onError: (error) => {
      const stale = conflictOf(error);
      if (stale) setConflict(stale);
      else setProblem(`This step was not saved. ${describeError(error)}`);
    },
  });

  const skip = useMutation({
    mutationFn: recordPass,
    onSuccess: (_, current) => {
      goTo(following(current));
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
      void navigate(after ?? DISCOVER);
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

  const onNext = () => {
    const blocked = stepProblem(step, draft);
    if (blocked) {
      setProblem(blocked);
      return;
    }
    save.mutate(step);
  };

  const applyConflict = (current: Conflict) => {
    client.setQueryData<Profile>(profileKey, (old) =>
      old
        ? { ...old, revision: current.revision, sections: current.sections }
        : old,
    );
  };
  const busy =
    save.isPending || skip.isPending || finish.isPending || later.isPending;
  const stored = (client.getQueryData<Profile>(profileKey) ?? profile).sections;
  const panel: PanelProps = {
    draft,
    change,
    problem,
    patterns: {
      roles: stored.search.title_include.advanced_regex,
      places: stored.search.location.advanced_regex,
    },
  };

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {WORDING.welcome}
          </h1>
          {state.completed_at ? null : (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                later.mutate();
              }}
            >
              {WORDING.skipForNow}
            </Button>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          A few short answers, and the app looks for postings that fit. You can
          change every answer later in your profile.
        </p>
        <Progress
          current={index}
          onOpen={(open) => {
            goTo(open);
          }}
          reachable={passed}
        />
      </header>

      <section
        aria-labelledby="welcome-step"
        className="flex flex-col gap-5"
        data-step={step}
      >
        <h2
          id="welcome-step"
          ref={heading}
          tabIndex={-1}
          className="text-lg font-semibold tracking-tight outline-none"
        >
          <span className="text-muted-foreground">
            Step {index + 1} of {STEP_IDS.length}:
          </span>{" "}
          {STEP_NAMES[step]}
        </h2>
        {problem ? (
          <Alert id="welcome-problem" variant="destructive" role="alert">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        ) : null}
        {step === "profile" ? <StepProfile /> : null}
        {step === "looking" ? <StepLooking {...panel} /> : null}
        {step === "where" ? <StepWhere {...panel} /> : null}
        {step === "work" ? <StepWork {...panel} /> : null}
        {step === "pay" ? <StepPay {...panel} problem={problem} /> : null}
        {step === "apply" ? <StepApply {...panel} settings={settings} /> : null}
        {step === "summary" ? (
          <StepSummary
            draft={savedDraft(stored, draft)}
            settings={settings}
            search={stored.search}
            onChange={(changed) => {
              goTo(changed, true);
            }}
            footer={<StarterBoards />}
          />
        ) : null}
      </section>

      <nav
        aria-label={WORDING.wizard}
        className="sticky bottom-0 flex flex-wrap items-center justify-between gap-2 border-t bg-background py-3"
      >
        <Button
          variant="outline"
          disabled={index === 0 || busy}
          onClick={() => {
            goTo(STEP_IDS[index - 1] ?? "profile");
          }}
        >
          <ArrowLeft aria-hidden="true" />
          {WORDING.back}
        </Button>
        <div className="flex flex-wrap gap-2">
          {ESSENTIAL.has(step) ? null : (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                skip.mutate(step);
              }}
            >
              {WORDING.skipThisStep}
            </Button>
          )}
          {step === "summary" ? (
            <Button
              disabled={busy}
              onClick={() => {
                finish.mutate();
              }}
            >
              {finish.isPending ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : null}
              {WORDING.start}
            </Button>
          ) : (
            <Button disabled={busy} onClick={onNext}>
              {save.isPending ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : null}
              {WORDING.next}
              <ArrowRight aria-hidden="true" />
            </Button>
          )}
        </div>
      </nav>

      <ConflictDialog
        open={conflict !== undefined}
        label={WORDING.yourProfile}
        onKeepEditing={() => {
          if (conflict) applyConflict(conflict);
          setConflict(undefined);
        }}
        onReload={() => {
          if (conflict) {
            applyConflict(conflict);
            setDraft(draftOf(conflict.sections));
          }
          setConflict(undefined);
        }}
      />
    </div>
  );
}

/** The progress bar: the seven names, the current one marked; a step already passed opens. */
function Progress({
  current,
  reachable,
  onOpen,
}: {
  current: number;
  reachable: readonly StepId[];
  onOpen: (step: StepId) => void;
}) {
  return (
    <nav aria-label={WORDING.progress}>
      <div
        role="progressbar"
        aria-label={WORDING.wizardProgress}
        aria-valuemin={1}
        aria-valuemax={STEP_IDS.length}
        aria-valuenow={current + 1}
        aria-valuetext={`Step ${current + 1} of ${STEP_IDS.length}`}
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width]"
          style={{ width: `${((current + 1) / STEP_IDS.length) * 100}%` }}
        />
      </div>
      <ol className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {STEP_IDS.map((step, at) => {
          const open = at === current;
          const canOpen = !open && (at < current || reachable.includes(step));
          return (
            <li key={step}>
              {canOpen ? (
                <button
                  type="button"
                  className="min-h-6 rounded text-muted-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  onClick={() => {
                    onOpen(step);
                  }}
                >
                  {STEP_NAMES[step]}
                </button>
              ) : (
                <span
                  aria-current={open ? "step" : undefined}
                  className={cn(
                    open ? "font-semibold" : "text-muted-foreground",
                  )}
                >
                  {STEP_NAMES[step]}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
