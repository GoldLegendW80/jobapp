import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, type ComponentType } from "react";
import { Link, useSearchParams } from "react-router";

import { PageHeader } from "@/components/blocks/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";
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
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-3">
        <Button asChild variant="ghost" size="sm" className="-ml-3 self-start">
          <Link to="/profile">
            <ArrowLeft aria-hidden="true" />
            Back to your profile
          </Link>
        </Button>
        <PageHeader
          title="Fill my profile with an agent"
          description="Add what you already have: a CV, a cover letter, notes on your projects. An agent reads it and proposes what to put in each section of your profile. Nothing is written to your profile until you accept it."
        />
      </div>

      <nav aria-label="Steps">
        <ol className="grid gap-2 sm:grid-cols-3">
          {STEPS.map((step, index) => (
            <li key={step.id} className="min-w-0">
              <StepLink
                step={step}
                number={index + 1}
                current={!deciding && step.id === current.id}
                onOpen={goTo}
              />
            </li>
          ))}
        </ol>
      </nav>

      {deciding ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading your documents.
        </p>
      ) : (
        <section
          aria-labelledby="step-heading"
          className="flex flex-col gap-5"
          data-step={current.id}
        >
          <h2
            id="step-heading"
            className="text-lg font-semibold tracking-tight"
          >
            <span className="text-muted-foreground">Step {number}:</span>{" "}
            {current.heading}
          </h2>
          <Panel goTo={goTo} />
        </section>
      )}
    </div>
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
      <ItemMedia>
        <span
          aria-hidden="true"
          className={cn(
            "grid size-6 place-items-center rounded-full bg-muted text-xs font-semibold tabular-nums",
            current && "bg-primary text-primary-foreground",
          )}
        >
          {number}
        </span>
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle>
          <span className="sr-only">Step {number}:</span> {step.title}{" "}
          {step.ready ? null : <Badge variant="secondary">Soon</Badge>}
        </ItemTitle>{" "}
        <ItemDescription className="text-xs">{step.summary}</ItemDescription>
      </ItemContent>
    </>
  );
  if (!step.ready) {
    // Not a control: there is nothing to open yet, so nothing takes the keyboard focus.
    return (
      <Item
        variant="outline"
        size="sm"
        className="h-full items-start rounded-xl border-dashed opacity-70"
      >
        {body}
      </Item>
    );
  }
  return (
    <Item
      asChild
      size="sm"
      className={cn(
        "h-full w-full flex-nowrap items-start justify-start rounded-xl text-left font-normal whitespace-normal hover:bg-card/60",
        current && "bg-card hover:bg-card",
      )}
    >
      <Button
        type="button"
        variant="ghost"
        aria-current={current ? "step" : undefined}
        onClick={() => {
          onOpen(step.id);
        }}
      >
        {body}
      </Button>
    </Item>
  );
}
