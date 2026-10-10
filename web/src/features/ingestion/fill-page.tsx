import { ArrowLeft } from "lucide-react";
import { useCallback, type ComponentType } from "react";
import { Link, useSearchParams } from "react-router";

import { useTitle } from "@/lib/use-title";
import { cn } from "@/lib/utils";

import { StepDocuments } from "./step-documents";
import { PROMPT_READY, StepPrompt } from "./step-prompt";
import { REVIEW_READY, StepReview } from "./step-review";
import { isStepId, type StepId, type StepProps } from "./steps";

interface Step {
  id: StepId;
  /** Two or three words, the name of the step in the list. */
  title: string;
  /** One line under the name: what the user does there. */
  summary: string;
  /** The heading of the step's panel. */
  heading: string;
  /** false: built by a later task. The step is listed as "Soon" and cannot be opened. */
  ready: boolean;
  Panel: ComponentType<StepProps>;
}

/**
 * The steps in the order the user takes them. Each step's file says whether it is ready
 * (PROMPT_READY, REVIEW_READY), so the task that builds a step turns it on from its own file.
 */
const STEPS: readonly Step[] = [
  {
    id: "prompt",
    title: "Prepare",
    summary: "Let a coding assistant write about you",
    heading: "Prepare a dossier with a coding assistant",
    ready: PROMPT_READY,
    Panel: StepPrompt,
  },
  {
    id: "documents",
    title: "Documents",
    summary: "Add what you already have",
    heading: "Add your documents",
    ready: true,
    Panel: StepDocuments,
  },
  {
    id: "review",
    title: "Review",
    summary: "Accept what the agent proposes",
    heading: "Review what the agent proposes",
    ready: REVIEW_READY,
    Panel: StepReview,
  },
];

/** The step the page opens on without `?step=`: the first one that is ready. */
function defaultStep(): Step {
  return STEPS.find((step) => step.ready) ?? (STEPS[1] as Step);
}

/**
 * /profile/fill, "Fill my profile with an agent": three steps, one open at a time, the open one
 * in `?step=`. An address that names a step that does not exist, or is not ready, opens the
 * default one. The profile itself is never changed from this page without the user's decision.
 */
export function FillPage() {
  useTitle("Fill my profile");
  const [params, setParams] = useSearchParams();
  const requested = params.get("step");
  const wanted = isStepId(requested)
    ? STEPS.find((step) => step.id === requested && step.ready)
    : undefined;
  const current = wanted ?? defaultStep();

  const goTo = useCallback(
    (id: StepId) => {
      if (!STEPS.some((step) => step.id === id && step.ready)) return;
      setParams(
        (old) => {
          const updated = new URLSearchParams(old);
          updated.set("step", id);
          return updated;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const number = STEPS.indexOf(current) + 1;
  const Panel = current.Panel;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <header className="flex flex-col gap-3">
        <Link
          to="/profile"
          className="inline-flex items-center gap-1.5 self-start rounded-md text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft aria-hidden="true" className="size-4" />
          Back to your profile
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          Fill my profile with an agent
        </h1>
        <p className="text-sm text-muted-foreground">
          Add what you already have: a CV, a cover letter, notes on your
          projects. An agent reads it and proposes what to put in each section
          of your profile. Nothing is written to your profile until you accept
          it.
        </p>
      </header>

      <nav aria-label="Steps">
        <ol className="grid gap-2 sm:grid-cols-3">
          {STEPS.map((step, index) => (
            <li key={step.id} className="min-w-0">
              <StepLink
                step={step}
                number={index + 1}
                current={step.id === current.id}
                onOpen={goTo}
              />
            </li>
          ))}
        </ol>
      </nav>

      <section
        aria-labelledby="step-heading"
        className="flex flex-col gap-5"
        data-step={current.id}
      >
        <h2 id="step-heading" className="text-lg font-semibold tracking-tight">
          <span className="text-muted-foreground">Step {number}: </span>
          {current.heading}
        </h2>
        <Panel goTo={goTo} />
      </section>
    </div>
  );
}

const STEP_BOX =
  "flex h-full w-full items-start gap-3 rounded-lg border px-3 py-2.5 text-left";

function StepLink({
  step,
  number,
  current,
  onOpen,
}: {
  step: Step;
  number: number;
  current: boolean;
  onOpen: (id: StepId) => void;
}) {
  const body = (
    <>
      <span
        aria-hidden="true"
        className={cn(
          "grid size-6 shrink-0 place-items-center rounded-full border text-xs font-semibold tabular-nums",
          current && "border-primary bg-primary text-primary-foreground",
        )}
      >
        {number}
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="text-sm font-medium">
          <span className="sr-only">Step {number}: </span>
          {step.title}
          {step.ready ? null : (
            <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-xs font-normal text-muted-foreground">
              Soon
            </span>
          )}
        </span>
        <span className="text-xs text-muted-foreground">{step.summary}</span>
      </span>
    </>
  );
  if (!step.ready) {
    // Not a control: there is nothing to open yet, so nothing takes the keyboard focus.
    return (
      <div className={cn(STEP_BOX, "border-dashed opacity-70")}>{body}</div>
    );
  }
  return (
    <button
      type="button"
      aria-current={current ? "step" : undefined}
      onClick={() => {
        onOpen(step.id);
      }}
      className={cn(
        STEP_BOX,
        "transition-colors outline-none hover:bg-accent focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        current && "border-primary/60 bg-accent",
      )}
    >
      {body}
    </button>
  );
}
