import {
  Ban,
  Brain,
  CircleAlert,
  CircleCheck,
  CircleDot,
  CircleX,
  Gauge,
  MessageSquareText,
  ShieldX,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import {
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace";
import { cn } from "@/lib/utils";

import type {
  StepBody,
  StepIcon,
  StepView,
} from "@/features/agents/helpers/run-steps";

const ICON: Record<StepIcon, LucideIcon> = {
  failed: CircleX,
  cancelled: Ban,
  succeeded: CircleCheck,
  status: CircleDot,
  measure: Gauge,
  thinking: Brain,
  answer: MessageSquareText,
  tool: Wrench,
  problem: CircleAlert,
  refused: ShieldX,
};

const RESULT_SHOWN = 600;
const plural = (count: number) =>
  `${count.toLocaleString("en-US")} character${count === 1 ? "" : "s"}`;

/** What a step says under its line: a sentence, the answer, or a tool's arguments. */
function Detail({ body }: { body: StepBody }) {
  switch (body.kind) {
    case "note":
      return (
        <p className="pl-6 text-sm break-words text-muted-foreground">
          {body.text}
        </p>
      );
    case "answer":
      return (
        <p className="pl-6 text-sm break-words whitespace-pre-wrap text-foreground">
          {body.text}
        </p>
      );
    case "arguments":
      return (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 pl-6 text-xs">
          {body.entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-muted-foreground">{key}</dt>
              <dd className="font-mono break-all text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      );
    default:
      return null;
  }
}

/**
 * One step of a run's timeline as a Trace line: its icon and title, a size beside it, what it
 * says under it. A tool's result opens in place under its line. `writing` is the answer being
 * written: its title breathes until the stored step takes its place.
 */
export function TimelineStep({
  view,
  writing = false,
}: {
  view: StepView;
  writing?: boolean;
}) {
  const Icon = ICON[view.icon];
  const { body } = view;
  const line = (
    <TraceLine
      status={view.tone === "error" ? "error" : writing ? "running" : undefined}
    >
      <TraceIcon className={cn(view.tone === "warning" && "text-warning")}>
        <Icon />
      </TraceIcon>
      <TraceTitle>{view.title}</TraceTitle>
      {body?.kind === "size" ? (
        <TraceDescription>{body.text}</TraceDescription>
      ) : body?.kind === "result" && body.content.length > RESULT_SHOWN ? (
        <TraceDescription>{plural(body.content.length)}</TraceDescription>
      ) : null}
    </TraceLine>
  );
  if (body?.kind === "result") {
    return (
      <TraceCollapsible>
        <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <pre className="max-h-60 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap text-foreground">
            {body.content.slice(0, RESULT_SHOWN * 4)}
          </pre>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    );
  }
  return (
    <>
      {line}
      {body ? <Detail body={body} /> : null}
    </>
  );
}
