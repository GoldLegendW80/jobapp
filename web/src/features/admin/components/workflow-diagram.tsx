import { Bot, Cog, Database, UserRound, type LucideIcon } from "lucide-react";
import { Fragment, useId } from "react";
import { Link } from "react-router";

import { cn } from "@/lib/utils";

import { flowSentences } from "../helpers/flow-sentences";

import type { AgentFlow, FlowEdge, FlowNode } from "../api/agents-api";

// The grid the api lays its nodes on: a column and a row each, in pixels here.
const NODE_W = 168;
const NODE_H = 56;
const COLUMN = 256;
const ROW = 96;
const PAD = 24;
// How far a return arrow dips below the lower of its two boxes, inside the gap under them.
const DIP = 32;

type Kind = FlowNode["kind"];

const KINDS: Record<
  Kind,
  { icon: LucideIcon; box: string; legend: string; spoken: string }
> = {
  agent: {
    icon: Bot,
    box: "rounded-xl border-2 border-primary bg-card font-mono text-[11px] text-foreground",
    legend: "Agent: opens its page",
    spoken: "agent",
  },
  person: {
    icon: UserRound,
    box: "rounded-full border border-foreground/40 bg-background px-3 text-xs text-foreground",
    legend: "A person: a click, a message, an upload",
    spoken: "a person",
  },
  system: {
    icon: Cog,
    box: "rounded-none border border-dashed border-foreground/50 bg-background text-xs text-foreground",
    legend: "The app: a sweep, a tick, a job",
    spoken: "the app",
  },
  data: {
    icon: Database,
    box: "rounded-md border-[3px] border-double border-foreground/40 bg-muted text-xs text-foreground",
    legend: "Stored data another step reads",
    spoken: "stored data",
  },
};

const left = (node: FlowNode) => PAD + node.column * COLUMN;
const top = (node: FlowNode) => PAD + node.row * ROW;

interface Route {
  path: string;
  x: number;
  y: number;
  back: boolean;
  vertical: boolean;
}

/**
 * The line of one arrow: to a later column from the right side to the left side, within a
 * column from the facing sides, and back to an earlier column (a return) from bottom to bottom,
 * dipping into the gap under the two boxes. `x`, `y` is where its words go.
 */
function route(a: FlowNode, b: FlowNode): Route {
  if (b.column > a.column) {
    const x1 = left(a) + NODE_W;
    const y1 = top(a) + NODE_H / 2;
    const x2 = left(b);
    const y2 = top(b) + NODE_H / 2;
    const dx = (x2 - x1) / 2;
    return {
      path: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
      x: (x1 + x2) / 2,
      y: (y1 + y2) / 2,
      back: false,
      vertical: false,
    };
  }
  if (b.column === a.column) {
    const x = left(a) + NODE_W / 2;
    const down = b.row > a.row;
    const y1 = down ? top(a) + NODE_H : top(a);
    const y2 = down ? top(b) : top(b) + NODE_H;
    return {
      path: `M ${x} ${y1} L ${x} ${y2}`,
      x,
      y: (y1 + y2) / 2,
      back: false,
      vertical: true,
    };
  }
  // Off centre, so a return does not meet a downward arrow leaving the same box.
  const x1 = left(a) + NODE_W * 0.35;
  const y1 = top(a) + NODE_H;
  const x2 = left(b) + NODE_W * 0.65;
  const y2 = top(b) + NODE_H;
  const low = Math.max(y1, y2) + DIP;
  return {
    path: `M ${x1} ${y1} C ${x1} ${low}, ${x2} ${low}, ${x2} ${y2}`,
    x: (x1 + x2) / 2,
    y: (y1 + 6 * low + y2) / 8,
    back: true,
    vertical: false,
  };
}

/**
 * Whether an arrow's words are drawn: once for arrows that share them and leave or reach the
 * same box (the seven reviewers of the hiring panel), so a fan of arrows reads as one step.
 */
function labelled(edges: FlowEdge[]): boolean[] {
  return edges.map(
    (edge, index) =>
      !edges
        .slice(0, index)
        .some(
          (other) =>
            other.label === edge.label &&
            (other.from === edge.from || other.to === edge.to),
        ),
  );
}

/** The words of an agent's name, broken after each underscore so a long one wraps cleanly. */
function AgentName({ name }: { name: string }) {
  const parts = name.split("_");
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part}
          {index < parts.length - 1 ? (
            <>
              _<wbr />
            </>
          ) : null}
        </Fragment>
      ))}
    </>
  );
}

function NodeBox({ node }: { node: FlowNode }) {
  const kind = KINDS[node.kind];
  const Icon = kind.icon;
  const style = {
    left: left(node),
    top: top(node),
    width: NODE_W,
    height: NODE_H,
  };
  const body = (
    <>
      <Icon
        aria-hidden="true"
        className="size-4 shrink-0 text-muted-foreground"
      />
      <span className="flex min-w-0 flex-col leading-tight">
        {node.kind === "agent" ? <AgentName name={node.label} /> : node.label}
        {node.note ? (
          <span className="font-sans text-[11px] text-muted-foreground italic">
            {node.note}
          </span>
        ) : null}
      </span>
    </>
  );
  const shape = cn(
    "absolute flex items-center gap-2 px-2 py-1 shadow-xs",
    kind.box,
  );
  if (node.kind === "agent")
    return (
      <Link
        to={`/admin/agents/${encodeURIComponent(node.id)}`}
        style={style}
        data-kind={node.kind}
        className={cn(
          shape,
          "font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50",
        )}
      >
        {body}
      </Link>
    );
  return (
    <div
      style={style}
      data-kind={node.kind}
      aria-hidden="true"
      className={shape}
    >
      {body}
    </div>
  );
}

function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
      {(Object.keys(KINDS) as Kind[]).map((key) => {
        const Icon = KINDS[key].icon;
        return (
          <li key={key} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={cn(
                "flex size-6 items-center justify-center px-0",
                KINDS[key].box,
              )}
            >
              <Icon className="size-3.5 text-muted-foreground" />
            </span>
            {KINDS[key].legend}
          </li>
        );
      })}
      <li className="flex items-center gap-1.5">
        <svg
          aria-hidden="true"
          width="28"
          height="8"
          className="text-muted-foreground"
        >
          <line
            x1="0"
            y1="4"
            x2="28"
            y2="4"
            stroke="currentColor"
            strokeWidth="1.5"
          />
        </svg>
        Starts or feeds
      </li>
      <li className="flex items-center gap-1.5">
        <svg
          aria-hidden="true"
          width="28"
          height="8"
          className="text-muted-foreground"
        >
          <line
            x1="0"
            y1="4"
            x2="28"
            y2="4"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeDasharray="4 3"
          />
        </svg>
        Goes back to an earlier step
      </li>
    </ul>
  );
}

/**
 * The workflow of one category of Admin, Agents (agents A9): its agents and what starts or feeds
 * each one, as the api lays them out (catalog.FLOWS). The drawing scrolls sideways inside its own
 * frame on a narrow screen, never the page; a screen reader gets each arrow as a sentence.
 */
export function WorkflowDiagram({
  title,
  flow,
}: {
  title: string;
  flow: AgentFlow;
}) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const arrow = `flow-arrow-${id}`;
  const byId = new Map(flow.nodes.map((node) => [node.id, node]));
  const columns = Math.max(0, ...flow.nodes.map((node) => node.column));
  const rows = Math.max(0, ...flow.nodes.map((node) => node.row));
  const width = PAD * 2 + columns * COLUMN + NODE_W;
  const height = PAD * 2 + rows * ROW + NODE_H + DIP;
  const shown = labelled(flow.edges);
  const routes = flow.edges.map((edge) => {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    return from && to ? { ...route(from, to), label: edge.label } : null;
  });
  // Left to right, top to bottom: the order a keyboard and a screen reader meet the agents.
  const nodes = [...flow.nodes].sort(
    (a, b) => a.column - b.column || a.row - b.row,
  );
  const sentences = flowSentences(flow);
  return (
    <figure
      aria-label={`${title}, workflow`}
      className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-3"
    >
      <div
        className="max-w-full overflow-x-auto overscroll-x-contain"
        tabIndex={0}
        role="group"
        aria-label={`${title}, workflow drawing`}
      >
        <div className="relative" style={{ width, height }}>
          <svg
            aria-hidden="true"
            width={width}
            height={height}
            className="absolute inset-0 text-muted-foreground"
          >
            <defs>
              <marker
                id={arrow}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
              </marker>
            </defs>
            {routes.map((line, index) =>
              line ? (
                <path
                  key={index}
                  d={line.path}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeDasharray={line.back ? "5 4" : undefined}
                  markerEnd={`url(#${arrow})`}
                />
              ) : null,
            )}
          </svg>
          {routes.map((line, index) =>
            line && shown[index] ? (
              <span
                key={index}
                aria-hidden="true"
                style={{ left: line.x, top: line.y }}
                className={cn(
                  "absolute -translate-x-1/2 -translate-y-1/2 rounded bg-card px-1 text-center text-[11px] leading-tight text-muted-foreground",
                  line.vertical ? "w-max max-w-40" : "w-max max-w-24",
                )}
              >
                {line.label}
              </span>
            ) : null,
          )}
          {nodes.map((node) => (
            <NodeBox key={node.id} node={node} />
          ))}
        </div>
      </div>
      <figcaption>
        <Legend />
      </figcaption>
      <ul className="sr-only" aria-label={`${title}, each step in words`}>
        {sentences.map((sentence, index) => (
          <li key={index}>{sentence}</li>
        ))}
      </ul>
    </figure>
  );
}
