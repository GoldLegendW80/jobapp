/**
 * Jobs J30: what each assistant of a search said when it ended, one click away in the search's
 * summary, read as Activity reads it: the agent steps of the search's run in Activity, then each
 * run's last stored text. Those are the model's own words: shown as the assistant's, as plain
 * text (React escapes it), never as a link or markup.
 */
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

import type { FlowStep } from "@/features/agents/api/activity-api";
import { useWorkflowRun } from "@/features/agents/api/use-activity";
import { useRun } from "@/features/agents/api/use-runs";
import { stepHref } from "@/features/agents/helpers/activity";
import { lastText } from "@/features/agents/helpers/live-text";
import { formatDateTime } from "@/features/jobs/helpers/format";

/** The agents that search a website or the whole web for a sweep (api AGENT_OF_MODE). */
const SEARCH_AGENTS = new Set(["browser_discovery", "web_discovery"]);

export function SweepAssistantWords({
  sweepId,
  onLeave,
}: {
  sweepId: string;
  /** Called when the person follows a link to Activity (the summary closes). */
  onLeave?: () => void;
}) {
  const run = useWorkflowRun(sweepId);
  const steps = (run.data?.steps ?? []).filter(
    (step) =>
      step.kind === "agent" &&
      SEARCH_AGENTS.has(step.agent ?? "") &&
      step.started_at,
  );
  if (!steps.length) return null;
  return (
    <section
      aria-label="What the assistants said"
      className="flex flex-col gap-1"
    >
      <h3 className="text-sm font-medium">What the assistants said</h3>
      <ul className="flex flex-col gap-1 text-sm">
        {steps.map((step) => (
          <li key={step.id}>
            <AssistantWords sweepId={sweepId} step={step} onLeave={onLeave} />
          </li>
        ))}
      </ul>
    </section>
  );
}

interface StepProps {
  sweepId: string;
  step: FlowStep;
  onLeave?: () => void;
}

function AssistantWords({ sweepId, step, onLeave }: StepProps) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 rounded-sm text-left outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50">
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
            open && "rotate-90",
          )}
        />
        {step.name}, {formatDateTime(step.started_at ?? "")}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-1 pl-5">
        {open ? (
          <ClosingText sweepId={sweepId} step={step} onLeave={onLeave} />
        ) : null}
      </CollapsibleContent>
    </Collapsible>
  );
}

function ClosingText({ sweepId, step, onLeave }: StepProps) {
  const run = useRun(step.id);
  const activity = (
    <Link
      to={stepHref(sweepId, step.id)}
      onClick={onLeave}
      className="underline underline-offset-4"
    >
      Open it in Activity
    </Link>
  );
  if (run.isPending)
    return <p className="text-muted-foreground">Loading what it said</p>;
  if (run.isError)
    return (
      <p className="text-muted-foreground">
        What it said could not be loaded. {activity}
      </p>
    );
  // A long run's detail holds its first events only: its last text there is not its closing one.
  const text = run.data.events_truncated
    ? undefined
    : lastText(run.data.events);
  if (text === undefined)
    return (
      <p className="text-muted-foreground">
        {run.data.events_truncated
          ? "This run is too long to show its last words here."
          : "The assistant ended without a closing text."}{" "}
        {activity}
      </p>
    );
  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="text-xs text-muted-foreground">
        In the assistant's own words, which the app did not check:
      </figcaption>
      <blockquote className="max-h-72 overflow-y-auto rounded-md border bg-muted/40 p-2 text-sm break-words whitespace-pre-wrap">
        {text}
      </blockquote>
    </figure>
  );
}
