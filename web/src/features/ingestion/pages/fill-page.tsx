import { useCallback, useEffect, type ComponentType } from "react";
import { useSearchParams } from "react-router";

import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTitle } from "@/hooks/use-title";
import { cn } from "@/lib/utils";

import { useDocuments } from "../api/use-documents";
import { StepDocuments } from "@/components/shared/ingestion/step-documents";
import { PROMPT_READY, StepPrompt } from "../components/step-prompt";
import { REVIEW_READY, StepReview } from "../components/step-review";
import { isStepId, type StepId, type StepProps } from "../helpers/steps";

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

/** The documents step on its own page: the ways to add, and the documents beside them. */
function DocumentsPanel() {
  return <StepDocuments />;
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
    heading: "Analyze your computer with a coding assistant",
    ready: PROMPT_READY,
    Panel: StepPrompt,
  },
  {
    id: "documents",
    title: "Documents",
    summary: "Add what you already have",
    heading: "Add your documents",
    ready: true,
    Panel: DocumentsPanel,
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

const DOCUMENTS = STEPS[1] as Step;

/**
 * The step the page opens on without `?step=`: Documents for a user who already has a document
 * (issue #114, owner 2026-10-21: the optional prompt stays one click away in the list of
 * steps), else the first one that is ready.
 */
function defaultStep(hasDocuments: boolean): Step {
  if (hasDocuments) return DOCUMENTS;
  return STEPS.find((step) => step.ready) ?? DOCUMENTS;
}

/**
 * /profile/fill, "Fill my profile with an agent": three steps, one open at a time, the open one
 * in `?step=`. An address that names a step that does not exist, or is not ready, opens the
 * default one (`defaultStep`), once the list of documents says which. The profile itself is never changed from this page without the user's decision.
 */
export function FillPage() {
  useTitle("Fill my profile");
  const [params, setParams] = useSearchParams();
  const requested = params.get("step");
  const wanted = isStepId(requested)
    ? STEPS.find((step) => step.id === requested && step.ready)
    : undefined;
  // Asked only to choose the landing step; the Documents step shares this query.
  const listed = useDocuments({ enabled: wanted === undefined });
  const deciding = wanted === undefined && listed.isPending;
  const hasDocuments = (listed.data?.items.length ?? 0) > 0;
  const current = wanted ?? defaultStep(hasDocuments);

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

  // Landed on Documents: the address names it, so deleting the last document keeps the page
  // where it is.
  useEffect(() => {
    if (wanted === undefined && !deciding && current.id === "documents")
      goTo("documents");
  }, [wanted, deciding, current.id, goTo]);

  const number = STEPS.indexOf(current) + 1;
  const Panel = current.Panel;
  return (
    <Page>
      <PageHeader
        trail={[{ to: "/profile", label: "Profile" }]}
        title="Fill my profile with an agent"
        description="An agent reads your CV, letters and notes, and proposes what to put in your profile. Nothing is written to your profile until you accept it."
      />

      <nav aria-label="Steps">
        <ol className="flex items-start gap-1 sm:gap-2">
          {STEPS.map((step, index) => (
            <li key={step.id} className="flex min-w-0 flex-1 items-start gap-2">
              <StepLink
                step={step}
                number={index + 1}
                current={!deciding && step.id === current.id}
                onOpen={goTo}
              />
              {index < STEPS.length - 1 ? (
                <span
                  aria-hidden="true"
                  className="mt-5 hidden h-px min-w-6 flex-1 bg-border sm:block"
                />
              ) : null}
            </li>
          ))}
        </ol>
      </nav>

      {deciding ? (
        <div role="status" aria-live="polite" className="flex flex-col gap-4">
          <span className="sr-only">Loading your documents</span>
          <Skeleton aria-hidden="true" className="h-7 w-64" />
          <Skeleton aria-hidden="true" className="h-56" />
        </div>
      ) : (
        <section
          aria-labelledby="step-heading"
          className="flex flex-col gap-6"
          data-step={current.id}
        >
          {/* The steps above show which one is open: "Step N" is said, not shown again. */}
          <h2 id="step-heading" className="text-lg font-medium tracking-tight">
            <span className="sr-only">Step {number}:</span> {current.heading}
          </h2>
          <Panel goTo={goTo} />
        </section>
      )}
    </Page>
  );
}

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
          "grid size-6 shrink-0 place-items-center rounded-full border text-xs font-medium tabular-nums",
          current
            ? "border-primary bg-primary text-primary-foreground"
            : "bg-card text-muted-foreground",
        )}
      >
        {number}
      </span>
      <span className="flex min-w-0 flex-col items-start gap-0.5 text-left">
        <span
          className={cn(
            "text-sm",
            current ? "font-medium text-foreground" : "text-muted-foreground",
          )}
        >
          <span className="sr-only">Step {number}:</span> {step.title}{" "}
          {step.ready ? null : <Badge variant="outline">Soon</Badge>}
        </span>
        <span className="text-xs font-normal text-muted-foreground max-sm:sr-only">
          {step.summary}
        </span>
      </span>
    </>
  );
  if (!step.ready) {
    // Not a control: there is nothing to open yet, so nothing takes the keyboard focus.
    return (
      <span className="flex items-start gap-3 px-2 py-2 opacity-70">
        {body}
      </span>
    );
  }
  return (
    <Button
      type="button"
      variant="ghost"
      aria-current={current ? "step" : undefined}
      className="h-auto min-w-0 shrink items-start justify-start gap-3 px-2 py-2 whitespace-normal"
      onClick={() => {
        onOpen(step.id);
      }}
    >
      {body}
    </Button>
  );
}
