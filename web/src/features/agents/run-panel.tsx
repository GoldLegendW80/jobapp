/**
 * RunPanel: one agent run, live. A timeline of its steps as the worker writes them (text, tool
 * calls and their results, refused tools, usage, status changes), the model, tokens, cost and
 * duration, a Cancel button while it is queued or running, and the state of the live connection
 * (it reconnects by itself after a network loss and picks up exactly where it stopped).
 * The answer appears while it is written (issue #33): the last item of the timeline grows with
 * the text, then the stored step takes its place, same title, same markup, in one render.
 * Reusable: the /runs pages show it today, job detail embeds it from A3 on.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Brain,
  CircleAlert,
  CircleCheck,
  CircleDot,
  CircleX,
  Gauge,
  LoaderCircle,
  MessageSquareText,
  Radio,
  ShieldX,
  Square,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { describeError, errorDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { LiveText } from "./live-text";
import {
  STATUS_LABEL,
  costLabel,
  costNote,
  describeCost,
  kindOfUsage,
  elapsed,
  formatCost,
  formatDuration,
  formatTokens,
} from "./format";
import {
  cancelRun,
  runKey,
  runsKey,
  type Connection,
  type CredentialKind,
  type RunStatus,
  type RunStep,
} from "./runs-api";
import { useNow } from "./use-now";
import { useRunStream } from "./use-run-stream";

const RESULT_SHOWN = 600;

export function StatusBadge({ status }: { status: RunStatus }) {
  const variant =
    status === "failed"
      ? "destructive"
      : status === "done"
        ? "default"
        : status === "running"
          ? "secondary"
          : "outline";
  return (
    <Badge variant={variant} data-status={status}>
      {status === "running" ? (
        <LoaderCircle aria-hidden="true" className="animate-spin" />
      ) : null}
      {STATUS_LABEL[status]}
    </Badge>
  );
}

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
  ended: "Ended",
};

function ConnectionState({ state }: { state: Connection }) {
  return (
    <span
      role="status"
      data-connection={state}
      className={cn(
        "inline-flex items-center gap-1.5 text-xs text-muted-foreground",
        state === "reconnecting" && "text-amber-700 dark:text-amber-400",
      )}
    >
      {state === "live" ? (
        <Radio aria-hidden="true" className="size-3.5 text-emerald-600" />
      ) : state === "ended" ? null : (
        <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
      )}
      {CONNECTION_LABEL[state]}
    </span>
  );
}

function Metric({
  label,
  value,
  describedBy,
}: {
  label: string;
  value: ReactNode;
  /** The id of a sentence that says what the value is (the cost note). */
  describedBy?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        aria-describedby={describedBy}
        className="truncate text-sm font-medium tabular-nums"
      >
        {value}
      </dd>
    </div>
  );
}

function plural(count: number, noun: string): string {
  return `${count.toLocaleString("en-US")} ${noun}${count === 1 ? "" : "s"}`;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value ?? "");
}

interface StepView {
  icon: LucideIcon;
  title: string;
  body?: ReactNode;
  tone?: "error" | "warning" | "muted";
}

function describeStep(
  step: RunStep,
  kind: CredentialKind | null | undefined,
): StepView | null {
  const data = step.data;
  switch (step.kind) {
    case "status": {
      const status = data.status as RunStatus;
      const labels: Partial<Record<string, string>> = STATUS_LABEL;
      const label = labels[text(data.status)] ?? text(data.status);
      if (status === "failed") {
        return {
          icon: CircleX,
          title: label,
          body: data.error ? text(data.error) : undefined,
          tone: "error",
        };
      }
      if (status === "cancelled") return { icon: Ban, title: label };
      if (status === "done") return { icon: CircleCheck, title: label };
      return { icon: CircleDot, title: label, tone: "muted" };
    }
    case "init": {
      const tools = Array.isArray(data.tools) ? data.tools.map(text) : [];
      return {
        icon: Gauge,
        title: `Started on ${text(data.model)}`,
        body: tools.length
          ? `Tools: ${tools.map((t) => t.replace(/^mcp__app__/, "")).join(", ")}`
          : "No tools",
        tone: "muted",
      };
    }
    case "thinking":
      return { icon: Brain, title: "Thinking", tone: "muted" };
    case "text":
      return answerView(text(data.text));
    case "tool_call": {
      const name = text(data.name).replace(/^mcp__app__/, "");
      const args =
        data.input && typeof data.input === "object"
          ? Object.entries(data.input as Record<string, unknown>)
          : [];
      return {
        icon: Wrench,
        title: `Tool call: ${name}`,
        // A private run stores the size of the arguments, never the arguments.
        body:
          !args.length && typeof data.input_size === "number" ? (
            <span className="text-xs text-muted-foreground">
              {`${plural(data.input_size, "character")} of arguments`}
            </span>
          ) : args.length ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-xs">
              {args.map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-muted-foreground">{key}</dt>
                  <dd className="font-mono break-all">{text(value)}</dd>
                </div>
              ))}
            </dl>
          ) : undefined,
      };
    }
    case "tool_result": {
      const failed = data.is_error === true;
      if (data.content === undefined && typeof data.size === "number") {
        // A private run stores the size of a result and its non-text blocks, never the
        // content: show them, and no box for a content that is not there.
        const blocks =
          typeof data.blocks === "number" && data.blocks > 0
            ? `, ${plural(data.blocks, "other block")}`
            : "";
        return {
          icon: failed ? CircleAlert : CircleCheck,
          title: failed ? "Tool error" : "Tool result",
          tone: failed ? "error" : undefined,
          body: (
            <span className="text-xs text-muted-foreground">
              {`${plural(data.size, "character")}${blocks}`}
            </span>
          ),
        };
      }
      const content = text(data.content);
      return {
        icon: failed ? CircleAlert : CircleCheck,
        title: failed ? "Tool error" : "Tool result",
        tone: failed ? "error" : undefined,
        body: (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              {content.length > RESULT_SHOWN
                ? `${content.length} characters`
                : "Show"}
            </summary>
            <pre className="mt-1 max-h-60 overflow-auto rounded-md bg-muted p-2 font-mono whitespace-pre-wrap">
              {content.slice(0, RESULT_SHOWN * 4)}
            </pre>
          </details>
        ),
      };
    }
    case "tool_denied":
      return {
        icon: ShieldX,
        title: `Refused: ${text(data.name)}`,
        body: "This tool is not available to this agent.",
        tone: "warning",
      };
    case "usage":
      return {
        icon: Gauge,
        title: "Usage",
        body: `${formatTokens(
          Number(data.input_tokens ?? 0) +
            Number(data.cache_creation_input_tokens ?? 0) +
            Number(data.cache_read_input_tokens ?? 0),
          Number(data.output_tokens ?? 0),
        )}, ${describeCost(
          typeof data.cost_usd === "number" ? data.cost_usd : null,
          // The step says which kind of credential the run used; older steps do not.
          kindOfUsage(data) ?? kind,
        )}`,
        tone: "muted",
      };
    case "api_error":
      return {
        icon: CircleAlert,
        title: "Model API error",
        body: text(data.error),
        tone: "error",
      };
    case "rate_limit":
      return {
        icon: CircleAlert,
        title: "Rate limit",
        body: text(data.status),
        tone: "warning",
      };
    default:
      return null;
  }
}

/** An answer in the timeline: the stored step and the text being written look the same. */
function answerView(value: string): StepView {
  return {
    icon: MessageSquareText,
    title: "Answer",
    body: (
      <p className="text-sm break-words whitespace-pre-wrap text-foreground">
        {value}
      </p>
    ),
  };
}

interface TimelineItem {
  key: string;
  kind: string;
  view: StepView;
  live: boolean;
}

function Timeline({
  steps,
  live,
  kind,
}: {
  steps: RunStep[];
  live: LiveText | null;
  kind: CredentialKind | null | undefined;
}) {
  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No step yet.</p>;
  }
  const views: TimelineItem[] = [];
  for (const step of steps) {
    const view = describeStep(step, kind);
    if (view !== null) {
      views.push({ key: String(step.seq), kind: step.kind, view, live: false });
    }
  }
  // The answer being written comes last. Its item is the stored step's, before the step exists.
  if (live?.text) {
    views.push({
      key: "live",
      kind: "text",
      view: answerView(live.text),
      live: true,
    });
  }
  const latest = views.at(-1)?.view.title ?? "";
  return (
    <>
      {/* Screen readers hear the newest step's title only: "Answer" once when the answer
          starts, not every piece of it, and not again when the stored step replaces it. */}
      <p className="sr-only" role="status" aria-live="polite">
        {latest}
      </p>
      <ol aria-label="Steps" className="flex flex-col">
        {views.map(({ key, kind, view, live: writing }) => {
          const Icon = view.icon;
          return (
            <li
              key={key}
              data-kind={kind}
              data-live={writing ? "true" : undefined}
              aria-busy={writing ? "true" : undefined}
              className="group relative flex gap-3 pb-4 last:pb-0"
            >
              <span
                aria-hidden="true"
                className="absolute top-6 bottom-0 left-[11px] w-px bg-border group-last:hidden"
              />
              <Icon
                aria-hidden="true"
                className={cn(
                  "mt-0.5 size-6 shrink-0 rounded-full border bg-background p-1",
                  view.tone === "error" &&
                    "border-destructive text-destructive",
                  view.tone === "warning" &&
                    "border-amber-600 text-amber-700 dark:text-amber-400",
                  view.tone === "muted" && "text-muted-foreground",
                )}
              />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <p
                  className={cn(
                    "text-sm font-medium",
                    view.tone === "error" && "text-destructive",
                    view.tone === "muted" && "text-muted-foreground",
                  )}
                >
                  {view.title}
                </p>
                {typeof view.body === "string" ? (
                  <p className="text-sm break-words text-muted-foreground">
                    {view.body}
                  </p>
                ) : (
                  view.body
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}

export function RunPanel({ runId, title }: { runId: string; title?: string }) {
  const client = useQueryClient();
  const { detail, steps, live, status, connection, gone } = useRunStream(runId);
  const run = detail.data;
  const noteId = useId();
  const active = status === "queued" || status === "running";
  const now = useNow(active);
  const titleRef = useRef<HTMLSpanElement>(null);
  const cancelFocused = useRef(false);
  const cancel = useMutation({
    mutationFn: () => cancelRun(runId),
    onSuccess: (updated) => {
      toast.info(
        updated.status === "cancelled" ? "Run cancelled." : "Stopping the run.",
      );
      void client.invalidateQueries({ queryKey: runKey(runId) });
      void client.invalidateQueries({ queryKey: runsKey, exact: true });
    },
    onError: (error) => {
      toast.error(errorDetail(error) ?? describeError(error));
      void client.invalidateQueries({ queryKey: runKey(runId) });
    },
  });

  // The Cancel button goes away when the run ends: if it had the focus, give it to the title
  // (which now says the run's status) instead of letting it fall back to the page.
  useEffect(() => {
    if (active || !cancelFocused.current) return;
    cancelFocused.current = false;
    const lost =
      document.activeElement === null ||
      document.activeElement === document.body;
    if (lost) titleRef.current?.focus();
  }, [active]);

  if (detail.isPending) {
    return (
      <Card aria-busy="true">
        <CardContent className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the run
        </CardContent>
      </Card>
    );
  }
  // Only when there is nothing to show: a failed refresh keeps the panel and its timeline.
  if (gone || !run) {
    return (
      <Alert variant="destructive">
        <CircleAlert aria-hidden="true" />
        <AlertDescription>
          {gone
            ? "This run no longer exists."
            : `The run could not be loaded. ${describeError(detail.error)}`}
        </AlertDescription>
      </Alert>
    );
  }
  const shownStatus = status ?? run.status;
  const stopping = run.cancel_requested && active;
  // What the amount is, when it is not a plain cost: on a subscription, an estimate.
  const note = run.cost_usd !== null ? costNote(run.credential_kind) : null;
  return (
    <Card data-testid="run-panel" data-status={shownStatus}>
      <CardHeader>
        <CardTitle>
          <span
            ref={titleRef}
            tabIndex={-1}
            data-testid="run-title"
            className="flex flex-wrap items-center gap-2 rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <span>{title ?? `Agent ${run.agent}`}</span>
            <StatusBadge status={shownStatus} />
          </span>
        </CardTitle>
        <CardDescription className="flex flex-wrap items-center gap-3">
          <ConnectionState state={connection} />
          {detail.isRefetchError ? (
            <span className="text-xs text-amber-700 dark:text-amber-400">
              Could not refresh the totals; they update with the next step.
            </span>
          ) : null}
        </CardDescription>
        {active ? (
          <CardAction>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                cancel.mutate();
              }}
              onFocus={() => {
                cancelFocused.current = true;
              }}
              onBlur={() => {
                cancelFocused.current = false;
              }}
              disabled={cancel.isPending || stopping}
              aria-busy={cancel.isPending}
            >
              {cancel.isPending || stopping ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : (
                <Square aria-hidden="true" />
              )}
              {stopping ? "Stopping" : "Cancel"}
            </Button>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Metric label="Model" value={run.model ?? "Default"} />
          <Metric
            label="Tokens"
            value={formatTokens(run.input_tokens, run.output_tokens)}
          />
          <Metric
            label={
              run.cost_usd === null ? "Cost" : costLabel(run.credential_kind)
            }
            value={formatCost(run.cost_usd)}
            describedBy={note ? noteId : undefined}
          />
          <Metric label="Duration" value={formatDuration(elapsed(run, now))} />
        </dl>
        {/* Tied to the amount for assistive technology (aria-describedby on its dd). */}
        {note ? (
          <p
            id={noteId}
            data-testid="cost-note"
            className="text-sm text-muted-foreground"
          >
            {note}
          </p>
        ) : null}
        <Timeline steps={steps} live={live} kind={run.credential_kind} />
      </CardContent>
    </Card>
  );
}
