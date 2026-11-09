import {
  Bot,
  ChevronDown,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  CircleStop,
  CircleX,
  Cog,
  Database,
  Maximize,
  Minus,
  Plus,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import {
  useCallback,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import type { AgentFlow, FlowNode } from "../api/agents-api";
import { flowSentences } from "../helpers/flow-sentences";

// The api's grid in pixels: a card per column and row, in the order things happen.
const CARD_W = 260;
const CARD_H = 44;
const COLUMN = CARD_W + 72;
const ROW = CARD_H + 20;
const PAD = 24;
const MEMBER_H = 32;
// How far below its two cards a return runs, inside the gap under them.
const LANE = 10;
const MAX_H = 640;
const ZOOM = 1.25;
// How much of the drawing a drag or a zoom leaves in the frame, so it is never lost.
const KEEP = 48;

export type NodeStatus =
  "queued" | "running" | "success" | "failure" | "skipped" | "cancelled";

/** What a run of the workflow says of one step: none of it on Admin, Agents today. */
export interface NodeRun {
  status?: NodeStatus;
  /** Already in words, as "1m 12s". */
  duration?: string;
  href?: string;
}

type Kind = FlowNode["kind"];

const KINDS: Record<Kind, { icon: LucideIcon; shape: string; legend: string }> =
  {
    agent: {
      icon: Bot,
      shape: "rounded-lg border-foreground/30",
      legend: "Agent: opens its page",
    },
    person: {
      icon: UserRound,
      shape: "rounded-full border-foreground/20",
      legend: "A person: a click, a message, an upload",
    },
    system: {
      icon: Cog,
      shape: "rounded-lg border-dashed border-foreground/40",
      legend: "The app: a sweep, a tick, a job",
    },
    data: {
      icon: Database,
      shape: "rounded-sm border-[3px] border-double border-foreground/25",
      legend: "Stored data another step reads",
    },
  };

const STATUSES: Record<
  NodeStatus,
  { icon: LucideIcon; className: string; words: string }
> = {
  queued: {
    icon: CircleDashed,
    className: "text-muted-foreground",
    words: "Queued",
  },
  running: { icon: CircleDashed, className: "text-warning", words: "Running" },
  success: { icon: CircleCheck, className: "text-success", words: "Succeeded" },
  failure: { icon: CircleX, className: "text-destructive", words: "Failed" },
  skipped: {
    icon: CircleSlash,
    className: "text-muted-foreground",
    words: "Skipped",
  },
  cancelled: {
    icon: CircleStop,
    className: "text-muted-foreground",
    words: "Cancelled",
  },
};

// A group shows its most telling member's status: a failure first, then a run still going.
const WORST: NodeStatus[] = [
  "failure",
  "running",
  "queued",
  "cancelled",
  "skipped",
  "success",
];

/** One card of the drawing: a step, or the agents of a group listed under its name. */
interface Card {
  key: string;
  node: FlowNode;
  members: FlowNode[];
}

interface Connector {
  from: Card;
  to: Card;
}

const x0 = (card: Card) => PAD + card.node.column * COLUMN;
const y0 = (card: Card) => PAD + card.node.row * ROW;

function cardsOf(flow: AgentFlow) {
  const cards: Card[] = [];
  const cardOf = new Map<string, Card>();
  const groups = new Map<string, Card>();
  const ordered = [...flow.nodes].sort(
    (a, b) => a.column - b.column || a.row - b.row,
  );
  for (const node of ordered) {
    const grouped = node.kind === "agent" && node.group !== "";
    const known = grouped ? groups.get(node.group) : undefined;
    if (known) {
      known.members.push(node);
      cardOf.set(node.id, known);
      continue;
    }
    const card: Card = grouped
      ? {
          key: `group:${node.group}`,
          node: { ...node, label: node.group, note: "" },
          members: [node],
        }
      : { key: node.id, node, members: [] };
    if (grouped) groups.set(node.group, card);
    cards.push(card);
    cardOf.set(node.id, card);
  }
  const connectors: Connector[] = [];
  const seen = new Set<string>();
  for (const edge of flow.edges) {
    const from = cardOf.get(edge.from);
    const to = cardOf.get(edge.to);
    if (!from || !to || from === to) continue;
    const pair = `${from.key} ${to.key}`;
    if (seen.has(pair)) continue;
    seen.add(pair);
    connectors.push({ from, to });
  }
  return { cards, cardOf, connectors };
}

/**
 * The line of a connector, its right-angle turns rounded: to a later column from the right side
 * to the left side, turning halfway; within a column from bottom to top; back to an earlier
 * column out to the right, along the gap under both cards and in from the left.
 */
function pathOf({ from, to }: Connector): {
  d: string;
  ends: [number, number][];
  back: boolean;
} {
  const r = 8;
  if (to.node.column === from.node.column) {
    const x = x0(from) + CARD_W / 2;
    const down = to.node.row > from.node.row;
    const y1 = down ? y0(from) + CARD_H : y0(from);
    const y2 = down ? y0(to) : y0(to) + CARD_H;
    return {
      d: `M ${String(x)} ${String(y1)} V ${String(y2)}`,
      ends: [
        [x, y1],
        [x, y2],
      ],
      back: false,
    };
  }
  const x1 = x0(from) + CARD_W;
  const y1 = y0(from) + CARD_H / 2;
  const x2 = x0(to);
  const y2 = y0(to) + CARD_H / 2;
  const ends: [number, number][] = [
    [x1, y1],
    [x2, y2],
  ];
  const n = (value: number) => String(value);
  if (to.node.column > from.node.column) {
    if (y1 === y2)
      return { d: `M ${n(x1)} ${n(y1)} H ${n(x2)}`, ends, back: false };
    const mx = (x1 + x2) / 2;
    const s = Math.sign(y2 - y1);
    const t = Math.min(r, Math.abs(y2 - y1) / 2);
    return {
      d: `M ${n(x1)} ${n(y1)} H ${n(mx - t)} Q ${n(mx)} ${n(y1)} ${n(mx)} ${n(y1 + s * t)} V ${n(y2 - s * t)} Q ${n(mx)} ${n(y2)} ${n(mx + t)} ${n(y2)} H ${n(x2)}`,
      ends,
      back: false,
    };
  }
  const out = x1 + 16;
  const into = x2 - 16;
  const low = Math.max(y0(from), y0(to)) + CARD_H + LANE;
  return {
    d: `M ${n(x1)} ${n(y1)} H ${n(out - r)} Q ${n(out)} ${n(y1)} ${n(out)} ${n(y1 + r)} V ${n(low - r)} Q ${n(out)} ${n(low)} ${n(out - r)} ${n(low)} H ${n(into + r)} Q ${n(into)} ${n(low)} ${n(into)} ${n(low - r)} V ${n(y2 + r)} Q ${n(into)} ${n(y2)} ${n(into + r)} ${n(y2)} H ${n(x2)}`,
    ends,
    back: true,
  };
}

/** The triggers of a workflow: the clicks and jobs of the app that nothing in it starts. */
function triggersOf(flow: AgentFlow): string[] {
  const started = new Set(flow.edges.map((edge) => edge.to));
  return flow.nodes
    .filter(
      (node) =>
        (node.kind === "person" || node.kind === "system") &&
        !started.has(node.id),
    )
    .map((node) => node.label);
}

function statusOf(card: Card, runs: Record<string, NodeRun>) {
  if (!card.members.length) return runs[card.node.id]?.status;
  const statuses = card.members.map((member) => runs[member.id]?.status);
  return WORST.find((status) => statuses.includes(status));
}

function KindOrStatus({ kind, status }: { kind: Kind; status?: NodeStatus }) {
  if (status) {
    const { icon: Icon, className, words } = STATUSES[status];
    return (
      <>
        {status === "running" ? (
          <Spinner
            aria-hidden="true"
            role={undefined}
            data-status={status}
            className={cn("size-4 shrink-0", className)}
          />
        ) : (
          <Icon
            aria-hidden="true"
            data-status={status}
            className={cn("size-4 shrink-0", className)}
          />
        )}
        <span className="sr-only">{words}, </span>
      </>
    );
  }
  const Icon = KINDS[kind].icon;
  return (
    <Icon
      aria-hidden="true"
      className="size-4 shrink-0 text-muted-foreground"
    />
  );
}

/** A card's tooltip: the agent's registered name, then what starts or feeds it. */
function Tip({
  lines,
  id,
  children,
}: {
  lines: string[];
  id?: string;
  children: ReactNode;
}) {
  if (!lines.length && !id) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" className="max-w-72">
        {id ? <p className="font-mono">{id}</p> : null}
        {lines.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </TooltipContent>
    </Tooltip>
  );
}

const ROW_CLASS = "flex h-11 w-full items-center gap-2 px-3 text-left";
// Inset: the card's overflow-hidden clips a ring drawn outside it.
const FOCUS =
  "outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset";

function StepCard({
  card,
  runs,
  into,
  open,
  onToggle,
}: {
  card: Card;
  runs: Record<string, NodeRun>;
  into: Map<string, string[]>;
  open: boolean;
  onToggle: () => void;
}) {
  const listId = useId();
  const { node, members } = card;
  const run = members.length ? undefined : runs[node.id];
  const href =
    run?.href ??
    (node.kind === "agent" && !members.length
      ? `/admin/agents/${encodeURIComponent(node.id)}`
      : undefined);
  const height = CARD_H + (open ? members.length * MEMBER_H + 8 : 0);
  const head = (
    <>
      <KindOrStatus kind={node.kind} status={statusOf(card, runs)} />
      <span className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate">{node.label}</span>
        {node.note ? (
          <span className="truncate text-[11px] text-muted-foreground">
            {node.note}
          </span>
        ) : null}
      </span>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        {run?.duration ??
          (members.length ? `${String(members.length)} agents` : "")}
      </span>
    </>
  );
  let body: ReactNode;
  if (members.length)
    body = (
      <>
        <Tip lines={into.get(card.key) ?? []}>
          <Button
            variant="ghost"
            aria-expanded={open}
            aria-controls={listId}
            onClick={onToggle}
            className={cn(
              ROW_CLASS,
              "rounded-none font-normal focus-visible:ring-inset",
            )}
          >
            {head}
            <ChevronDown
              aria-hidden="true"
              className={cn(
                "size-4 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-180",
              )}
            />
          </Button>
        </Tip>
        <ul id={listId} hidden={!open} className="border-t p-1">
          {members.map((member) => (
            <li key={member.id}>
              <Tip id={member.id} lines={into.get(member.id) ?? []}>
                <Link
                  to={
                    runs[member.id]?.href ??
                    `/admin/agents/${encodeURIComponent(member.id)}`
                  }
                  className={cn(
                    "flex h-8 items-center gap-2 rounded-md px-2 text-xs",
                    FOCUS,
                  )}
                >
                  <KindOrStatus kind="agent" status={runs[member.id]?.status} />
                  <span className="min-w-0 flex-1 truncate">
                    {member.label.replace(`${node.label}: `, "")}
                  </span>
                  <span className="text-muted-foreground tabular-nums">
                    {runs[member.id]?.duration ?? ""}
                  </span>
                </Link>
              </Tip>
            </li>
          ))}
        </ul>
      </>
    );
  else if (href)
    body = (
      <Tip
        id={node.kind === "agent" ? node.id : undefined}
        lines={into.get(node.id) ?? []}
      >
        <Link to={href} className={cn(ROW_CLASS, FOCUS)}>
          {head}
        </Link>
      </Tip>
    );
  else
    body = (
      <Tip lines={into.get(node.id) ?? [node.label]}>
        <div className={ROW_CLASS}>{head}</div>
      </Tip>
    );
  return (
    <div
      data-kind={node.kind}
      data-card={card.key}
      aria-hidden={href || members.length ? undefined : "true"}
      style={{ left: x0(card), top: y0(card), width: CARD_W, height }}
      className={cn(
        "absolute overflow-hidden border bg-card text-sm text-card-foreground shadow-xs",
        KINDS[node.kind].shape,
      )}
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
                "flex h-5 w-7 items-center justify-center border bg-card",
                KINDS[key].shape,
              )}
            >
              <Icon className="size-3 text-muted-foreground" />
            </span>
            {KINDS[key].legend}
          </li>
        );
      })}
      {[
        { words: "Starts or feeds", dash: undefined },
        { words: "Goes back to an earlier step", dash: "4 3" },
      ].map(({ words, dash }) => (
        <li key={words} className="flex items-center gap-1.5">
          <svg
            aria-hidden="true"
            width="28"
            height="8"
            className="text-muted-foreground"
          >
            <line
              x1="3"
              y1="4"
              x2="25"
              y2="4"
              stroke="currentColor"
              strokeDasharray={dash}
            />
            <circle
              cx="3"
              cy="4"
              r="2.5"
              stroke="currentColor"
              className="fill-card"
            />
            <circle
              cx="25"
              cy="4"
              r="2.5"
              stroke="currentColor"
              className="fill-card"
            />
          </svg>
          {words}
        </li>
      ))}
    </ul>
  );
}

interface View {
  scale: number;
  x: number;
  y: number;
}

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [48, 0],
  ArrowRight: [-48, 0],
  ArrowUp: [0, 48],
  ArrowDown: [0, -48],
};

interface Point {
  x: number;
  y: number;
}

const midpoint = (a: Point, b: Point): Point => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
});

/**
 * Pan and zoom inside the frame: Fit shows the whole drawing, minus and plus zoom around the
 * frame's centre, two fingers zoom around their midpoint, a drag (on a touch screen, one finger
 * once the drawing is zoomed or moved) or the arrow keys move it, never all of it out of the
 * frame. The page never scrolls sideways for it.
 */
function usePanZoom(
  frame: RefObject<HTMLDivElement | null>,
  width: number,
  height: number,
) {
  const [frameW, setFrameW] = useState(0);
  const fitted = useCallback((): View => {
    if (!frameW) return { scale: 1, x: 0, y: 0 };
    const scale = Math.min(1, frameW / width, MAX_H / height);
    return { scale, x: (frameW - width * scale) / 2, y: 0 };
  }, [frameW, width, height]);
  // Null while the drawing is fitted, so it fits again when the frame's width changes.
  const [moved, setView] = useState<View | null>(null);
  const view = moved ?? fitted();
  // The pointers down on the frame, where each was last: one drags, two pinch.
  const pointers = useRef(new Map<number, Point>());
  // The gesture moved the drawing: the click it ends with is no click on a card.
  const gestured = useRef(false);

  useLayoutEffect(() => {
    const element = frame.current;
    if (!element) return;
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      setFrameW(element.clientWidth);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [frame]);

  const frameH = Math.max(
    160,
    Math.min(MAX_H, Math.ceil(height * fitted().scale)),
  );
  const kept = ({ scale, x, y }: View): View => ({
    scale,
    x: Math.min((frameW || width) - KEEP, Math.max(KEEP - width * scale, x)),
    y: Math.min(frameH - KEEP, Math.max(KEEP - height * scale, y)),
  });
  /** Zoom by `by`, the drawing's point at `from` in the frame drawn at `to` afterwards. */
  const zoomAt = (by: number, from: Point, to: Point = from) => {
    setView((was) => {
      const now = was ?? fitted();
      const scale = Math.min(2, Math.max(0.2, now.scale * by));
      return kept({
        scale,
        x: to.x - ((from.x - now.x) * scale) / now.scale,
        y: to.y - ((from.y - now.y) * scale) / now.scale,
      });
    });
  };
  const zoom = (by: number) => {
    zoomAt(by, { x: (frameW || width) / 2, y: frameH / 2 });
  };
  const move = (dx: number, dy: number) => {
    setView((was) => {
      const now = was ?? fitted();
      return kept({ ...now, x: now.x + dx, y: now.y + dy });
    });
  };
  const release = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
  };
  const handlers = {
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      // A new gesture: forget a pointer whose end never reached the frame.
      if (event.isPrimary) {
        pointers.current.clear();
        gestured.current = false;
      }
      pointers.current.set(event.pointerId, {
        x: event.clientX,
        y: event.clientY,
      });
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      const points = pointers.current;
      const last = points.get(event.pointerId);
      if (!last) return;
      const at = { x: event.clientX, y: event.clientY };
      const [other] = [...points].filter(([id]) => id !== event.pointerId);
      if (other) {
        const rect = event.currentTarget.getBoundingClientRect();
        const local = ({ x, y }: Point) => ({
          x: x - rect.left,
          y: y - rect.top,
        });
        const apart = Math.hypot(last.x - other[1].x, last.y - other[1].y);
        points.set(event.pointerId, at);
        gestured.current = true;
        if (!apart) return;
        zoomAt(
          Math.hypot(at.x - other[1].x, at.y - other[1].y) / apart,
          local(midpoint(last, other[1])),
          local(midpoint(at, other[1])),
        );
        return;
      }
      // A fitted drawing shows whole: a finger on it scrolls the page, never moves the drawing.
      if (moved === null && event.pointerType === "touch") return;
      const dx = at.x - last.x;
      const dy = at.y - last.y;
      if (!gestured.current && Math.hypot(dx, dy) < 4) return;
      if (!gestured.current)
        event.currentTarget.setPointerCapture(event.pointerId);
      points.set(event.pointerId, at);
      gestured.current = true;
      move(dx, dy);
    },
    onPointerUp: release,
    // The browser took the gesture (a page scroll while fitted).
    onPointerCancel: release,
    // A drag or a pinch that moved the drawing is not a click on the card it started on.
    onClickCapture: (event: MouseEvent<HTMLDivElement>) => {
      if (gestured.current) {
        event.preventDefault();
        event.stopPropagation();
      }
      gestured.current = false;
    },
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      const by = ARROWS[event.key];
      if (event.target !== event.currentTarget || !by) return;
      event.preventDefault();
      move(...by);
    },
  };
  return {
    view,
    fitted: moved === null,
    frameH,
    handlers,
    fit: () => {
      setView(null);
    },
    zoomIn: () => {
      zoom(ZOOM);
    },
    zoomOut: () => {
      zoom(1 / ZOOM);
    },
  };
}

/**
 * The workflow of one category of Admin, Agents (agents A9), drawn as a run graph: its triggers,
 * then its steps as cards in columns in the order things happen (catalog.FLOWS), joined by
 * connectors, agents that run side by side as one card that lists them. `runs` gives a step a
 * status, a duration and a link, for a run of the workflow; the agents list passes none. A
 * screen reader gets each connector as a sentence.
 */
export function WorkflowDiagram({
  title,
  flow,
  runs = {},
}: {
  title: string;
  flow: AgentFlow;
  runs?: Record<string, NodeRun>;
}) {
  const { cards, cardOf, connectors } = cardsOf(flow);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const columns = Math.max(0, ...flow.nodes.map((node) => node.column));
  const rows = Math.max(0, ...flow.nodes.map((node) => node.row));
  const width = PAD * 2 + columns * COLUMN + CARD_W;
  const height = PAD * 2 + rows * ROW + CARD_H + LANE;
  const frame = useRef<HTMLDivElement>(null);
  const pan = usePanZoom(frame, width, height);
  const byId = new Map(flow.nodes.map((node) => [node.id, node]));
  const into = new Map<string, string[]>();
  for (const edge of flow.edges) {
    const from = byId.get(edge.from);
    if (!from) continue;
    const line = `From ${from.label}: ${edge.label}`;
    for (const key of [edge.to, cardOf.get(edge.to)?.key ?? edge.to]) {
      const lines = into.get(key) ?? [];
      if (!lines.includes(line)) into.set(key, [...lines, line]);
    }
  }
  const triggers = triggersOf(flow);
  const stepsRun = flow.nodes.filter((node) => runs[node.id]?.status);
  const { x, y, scale } = pan.view;
  return (
    <TooltipProvider>
      <figure
        aria-label={`${title}, workflow`}
        className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-3"
      >
        <div className="flex min-w-0 items-start gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <p className="text-sm font-medium">{title}</p>
            {triggers.length ? (
              <p className="truncate font-mono text-xs text-muted-foreground">
                on: {triggers.join(", ")}
              </p>
            ) : null}
          </div>
          {/* Beside the title, never over a card of the drawing. */}
          <div className="flex shrink-0 gap-0.5 rounded-md border bg-card p-0.5 shadow-xs">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Fit the drawing"
              onClick={pan.fit}
            >
              <Maximize aria-hidden="true" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom out"
              onClick={pan.zoomOut}
            >
              <Minus aria-hidden="true" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom in"
              onClick={pan.zoomIn}
            >
              <Plus aria-hidden="true" />
            </Button>
          </div>
        </div>
        <div
          ref={frame}
          role="group"
          aria-label={`${title}, workflow drawing`}
          tabIndex={0}
          // Fitted, a vertical swipe scrolls the page; zoomed or moved, a finger moves the drawing.
          style={{
            height: pan.frameH,
            touchAction: pan.fitted ? "pan-y" : "none",
          }}
          className="relative cursor-grab overflow-hidden rounded-lg border bg-muted/40 outline-none select-none focus-visible:ring-[3px] focus-visible:ring-ring/50 active:cursor-grabbing"
          {...pan.handlers}
        >
          <div
            data-drawing=""
            className="absolute top-0 left-0 origin-top-left"
            style={{
              width,
              height,
              transform: `translate(${String(x)}px, ${String(y)}px) scale(${String(scale)})`,
            }}
          >
            <svg
              aria-hidden="true"
              width={width}
              height={height}
              className="absolute inset-0 text-foreground/35"
            >
              {connectors.map((connector) => {
                const line = pathOf(connector);
                return (
                  <g key={`${connector.from.key} ${connector.to.key}`}>
                    <path
                      d={line.d}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeDasharray={line.back ? "5 4" : undefined}
                    />
                    {line.ends.map(([cx, cy]) => (
                      <circle
                        key={`${String(cx)} ${String(cy)}`}
                        cx={cx}
                        cy={cy}
                        r="3"
                        strokeWidth="1.5"
                        stroke="currentColor"
                        className="fill-card"
                      />
                    ))}
                  </g>
                );
              })}
            </svg>
            {cards.map((card) => (
              <StepCard
                key={card.key}
                card={card}
                runs={runs}
                into={into}
                open={Boolean(open[card.key])}
                onToggle={() => {
                  setOpen((now) => ({ ...now, [card.key]: !now[card.key] }));
                }}
              />
            ))}
          </div>
        </div>
        <figcaption>
          <Legend />
        </figcaption>
        <ul className="sr-only" aria-label={`${title}, each step in words`}>
          {flowSentences(flow).map((sentence, index) => (
            <li key={index}>{sentence}</li>
          ))}
        </ul>
        {stepsRun.length ? (
          <ul className="sr-only" aria-label={`${title}, this run`}>
            {stepsRun.map((node) => {
              const run = runs[node.id];
              const words = run?.status ? STATUSES[run.status].words : "";
              const time = run?.duration ? `, ${run.duration}` : "";
              return <li key={node.id}>{`${node.label}: ${words}${time}.`}</li>;
            })}
          </ul>
        ) : null}
      </figure>
    </TooltipProvider>
  );
}
